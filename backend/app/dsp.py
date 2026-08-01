"""Server-side signal processing: filters over a time window + FFT/Welch spectra.

Filtered series are served in the same window format as store.read_window
(raw vs min/max envelope, identical level selection and bucket boundaries),
so the frontend can overlay them on the raw series on a shared time axis.
"""

import math

import numpy as np
import polars as pl
from scipy import signal
from scipy.ndimage import median_filter, uniform_filter1d

from .config import MAX_FILTER_SAMPLES, TESTS_DIR
from .store import (bucket_minmax, get_meta, merge_over_cap, plot_budget,
                    resolve_window_display, to_json_list, window_bounds)

FILTER_KINDS = {"lowpass", "highpass", "bandpass", "bandstop",
                "moving_avg", "detrend", "despike"}

DEFAULT_DESPIKE_WINDOW_S = 0.025
DEFAULT_MAX_SPIKE_S = 0.010
DEFAULT_DESPIKE_THRESHOLD = 3.5
DEFAULT_DESPIKE_ABS_FLOOR = 0.0
_MAD_NORMAL_SCALE = 1.4826
# A pathological rolling-median footprint can make scipy allocate excessive
# working memory. This is deliberately much larger than normal despike windows
# (25 ms at the rig's 2048 Hz rate resolves to 51 samples).
_MAX_DESPIKE_WINDOW_SAMPLES = 100_001


def _known_gap_slices(meta: dict, start: int,
                      end: int) -> list[tuple[int, int]]:
    """Return merged, clipped missing-row ranges in global row coordinates."""
    clipped: list[tuple[int, int]] = []
    for value in meta.get("time_gap_ranges") or []:
        if (not isinstance(value, list) or len(value) != 2
                or not all(isinstance(item, int) for item in value)):
            continue
        lo = max(start, value[0])
        hi = min(end, value[1])
        if lo >= hi:
            continue
        if clipped and lo <= clipped[-1][1]:
            clipped[-1] = (clipped[-1][0], max(hi, clipped[-1][1]))
        else:
            clipped.append((lo, hi))
    return clipped


def _interp_nan(v: np.ndarray):
    """Linear-interpolate NaN so DSP sees a gapless signal.
    Returns (clean, finite_mask); clean is None if < 2 valid samples."""
    m = np.isfinite(v)
    if m.all():
        return v, m
    if m.sum() < 2:
        return None, m
    idx = np.arange(v.size)
    out = v.copy()
    out[~m] = np.interp(idx[~m], idx[m], v[m])
    return out, m


def _make_sos(kind: str, order: int, f1: float | None, f2: float | None,
              fs: float):
    nyq = fs / 2
    if kind in ("lowpass", "highpass"):
        if f1 is None or not 0 < f1 < nyq:
            raise ValueError(f"cutoff f1 must be in (0, {nyq:g}) Hz")
        wn = f1
    else:
        if f1 is None or f2 is None or not 0 < f1 < f2 < nyq:
            raise ValueError(f"band filter needs 0 < f1 < f2 < {nyq:g} Hz")
        wn = [f1, f2]
    return signal.butter(order, wn, btype=kind, fs=fs, output="sos")


def _despike_sample_parameters(
        fs: float,
        window_s: float | None,
        max_spike_s: float | None,
        threshold: float,
        abs_floor: float) -> tuple[int, int, float, float]:
    """Validate time-domain parameters and resolve them to sample counts.

    Hampel detection needs more clean than contaminated samples in every
    window. Enforcing ``window > 2 * max_spike`` makes that assumption explicit
    rather than silently failing on the interior of a rectangular pulse.
    """
    resolved_window_s = (
        DEFAULT_DESPIKE_WINDOW_S if window_s is None else float(window_s))
    resolved_max_spike_s = (
        DEFAULT_MAX_SPIKE_S
        if max_spike_s is None else float(max_spike_s))
    resolved_threshold = float(threshold)
    resolved_abs_floor = float(abs_floor)

    if not math.isfinite(resolved_window_s) or resolved_window_s <= 0:
        raise ValueError("despike window_s must be a finite value > 0")
    if (not math.isfinite(resolved_max_spike_s)
            or resolved_max_spike_s <= 0):
        raise ValueError("despike max_spike_s must be a finite value > 0")
    if not math.isfinite(resolved_threshold) or resolved_threshold <= 0:
        raise ValueError("despike threshold must be a finite value > 0")
    if not math.isfinite(resolved_abs_floor) or resolved_abs_floor < 0:
        raise ValueError("despike abs_floor must be a finite value >= 0")

    window_samples = max(3, int(round(resolved_window_s * fs)))
    if window_samples % 2 == 0:
        window_samples += 1
    if window_samples > _MAX_DESPIKE_WINDOW_SAMPLES:
        raise ValueError(
            "despike window_s resolves to "
            f"{window_samples} samples (max "
            f"{_MAX_DESPIKE_WINDOW_SAMPLES}); reduce window_s")

    # A run of N samples occupies N/fs seconds in the uniformly sampled data.
    # Floor so the configured maximum duration is never exceeded.
    max_spike_samples = max(
        1, int(math.floor(resolved_max_spike_s * fs + 1e-12)))
    if 2 * max_spike_samples >= window_samples:
        raise ValueError(
            "despike window_s must resolve to more than twice "
            "max_spike_s (resolved to "
            f"{window_samples} and {max_spike_samples} samples)")

    return (window_samples, max_spike_samples,
            resolved_threshold, resolved_abs_floor)


def _despike(
        v: np.ndarray,
        window_samples: int,
        max_spike_samples: int,
        threshold: float,
        abs_floor: float,
        replacement: str) -> tuple[np.ndarray, np.ndarray]:
    """Remove short Hampel outlier runs and return (values, replaced mask).

    ``v`` is finite: callers interpolate ordinary sparse NaNs first and restore
    their mask afterwards. Known acquisition gaps are passed as separate
    segments, so neither rolling statistics nor linear shoulders cross them.
    """
    if replacement not in {"linear", "median"}:
        raise ValueError(
            "despike replacement must be 'linear' or 'median'")
    if window_samples >= v.size:
        raise ValueError("despike window longer than the range")

    # Reflecting only samples from the same continuous segment gives boundary
    # runs a fair clean majority. ``nearest`` would repeat an edge spike until
    # it became the median and therefore escaped detection.
    local_median = median_filter(
        v, size=window_samples, mode="reflect")
    deviation = np.abs(v - local_median)
    local_mad = median_filter(
        deviation, size=window_samples, mode="reflect")
    limit = np.maximum(
        abs_floor, threshold * _MAD_NORMAL_SCALE * local_mad)
    candidates = deviation > limit

    out = v.copy()
    replaced = np.zeros(v.size, dtype=bool)
    if not candidates.any():
        return out, replaced

    padded = np.empty(v.size + 2, dtype=np.int8)
    padded[0] = padded[-1] = 0
    padded[1:-1] = candidates
    transitions = np.diff(padded)
    starts = np.flatnonzero(transitions == 1)
    ends = np.flatnonzero(transitions == -1)

    for start, end in zip(starts, ends):
        run_length = int(end - start)
        if run_length > max_spike_samples:
            continue

        if replacement == "linear" and start > 0 and end < v.size:
            # Both immediate shoulders were classified as clean. np.interp
            # produces the same straight bridge for one or many samples.
            out[start:end] = np.linspace(
                v[start - 1], v[end], run_length + 2)[1:-1]
        else:
            # At a selected-range/continuous-segment boundary there cannot be
            # two clean shoulders. The local robust baseline is the safe
            # fallback and, importantly, never reaches across a hard gap.
            out[start:end] = local_median[start:end]
        replaced[start:end] = True

    return out, replaced


def _apply(kind: str, v: np.ndarray, fs: float, order: int,
           f1: float | None, f2: float | None,
           window_s: float | None,
           max_spike_s: float | None = DEFAULT_MAX_SPIKE_S,
           threshold: float = DEFAULT_DESPIKE_THRESHOLD,
           abs_floor: float = DEFAULT_DESPIKE_ABS_FLOOR,
           replacement: str = "linear") -> np.ndarray:
    if kind == "detrend":
        return signal.detrend(v, type="linear")
    if kind == "moving_avg":
        if window_s is None or window_s <= 0:
            raise ValueError("window_s must be > 0")
        w = max(1, int(round(window_s * fs)))
        if w >= v.size:
            raise ValueError("moving-average window longer than the range")
        return uniform_filter1d(v, size=w, mode="nearest")
    if kind == "despike":
        params = _despike_sample_parameters(
            fs, window_s, max_spike_s, threshold, abs_floor)
        return _despike(v, *params, replacement)[0]
    order = min(max(int(order), 1), 10)
    sos = _make_sos(kind, order, f1, f2, fs)
    if v.size <= 3 * (2 * sos.shape[0] + 1):  # sosfiltfilt padding needs room
        raise ValueError("range too short for this filter order")
    return signal.sosfiltfilt(sos, v)


def filtered_window(name: str, cols: list[str], kind: str,
                    t0: float | None, t1: float | None, px: int,
                    order: int = 4, f1: float | None = None,
                    f2: float | None = None,
                    window_s: float | None = None,
                    display: str = "auto",
                    max_spike_s: float | None = DEFAULT_MAX_SPIKE_S,
                    threshold: float = DEFAULT_DESPIKE_THRESHOLD,
                    abs_floor: float = DEFAULT_DESPIKE_ABS_FLOOR,
                    replacement: str = "linear") -> dict:
    """Filter cols over [t0, t1] at full resolution, then serve like
    store.read_window so the result aligns 1:1 with the base window."""
    meta = get_meta(name)
    if meta is None:
        raise FileNotFoundError(name)
    if kind not in FILTER_KINDS:
        raise ValueError(f"unknown filter type '{kind}'")
    fs = meta["fs_hz"]
    n_rows = meta["n_rows"]
    tcol = meta["time_column"]
    i0, i1 = window_bounds(meta, t0, t1)
    n_raw = max(0, i1 - i0)
    if n_raw > MAX_FILTER_SAMPLES:
        raise ValueError(
            f"range spans {n_raw} samples (max {MAX_FILTER_SAMPLES}); "
            "zoom in or select a test point")
    if n_raw < 8:
        raise ValueError("range too short")
    despike_params: tuple[int, int, float, float] | None = None
    if kind == "despike":
        despike_params = _despike_sample_parameters(
            fs, window_s, max_spike_s, threshold, abs_floor)
        if replacement not in {"linear", "median"}:
            raise ValueError(
                "despike replacement must be 'linear' or 'median'")
    boundary = i0 == 0 or i1 >= n_rows
    budget = plot_budget(px)
    test_dir = TESTS_DIR / name
    resolved_mode, level = resolve_window_display(n_raw, budget, display)
    raw_mode = resolved_mode == "raw"

    if raw_mode:
        s0, s1 = i0, i1
    else:
        # bucket-aligned slice so envelope buckets match the pyramid's
        b0, b1 = i0 // level, -(-i1 // level)
        s0, s1 = b0 * level, min(b1 * level, n_rows)

    df = (pl.scan_parquet(test_dir / "data.parquet")
          .slice(s0, s1 - s0)
          .select(([tcol] if raw_mode else []) + cols).collect())

    processing_gaps = _known_gap_slices(meta, s0, s1)
    requested_gap_count = len(_known_gap_slices(meta, i0, i1))
    nan_counts: dict[str, int] = {}
    filt: dict[str, np.ndarray] = {}
    replacement_counts: dict[str, int] = {}
    spike_event_counts: dict[str, int] = {}
    skipped_segments = 0
    for c in cols:
        v = df[c].to_numpy().astype(np.float64)
        replaced_mask = (
            np.zeros(v.size, dtype=bool)
            if despike_params is not None else None)
        nan_counts[c] = int(v.size - np.isfinite(v).sum())
        if not processing_gaps:
            clean, mask = _interp_nan(v)
            if clean is None:
                filt[c] = np.full(v.size, np.nan)
            else:
                if despike_params is None:
                    y = _apply(
                        kind, clean, fs, order, f1, f2, window_s)
                else:
                    y, local_replaced = _despike(
                        clean, *despike_params, replacement)
                    local_replaced &= mask
                    replaced_mask[:] = local_replaced
                y[~mask] = np.nan
                filt[c] = y
        else:
            # A known acquisition dropout is a hard DSP boundary. Ordinary
            # sparse NaNs inside each continuous region are still interpolated
            # as before, but no filter state, statistic, replacement shoulder,
            # or interpolation can cross the missing interval.
            y = np.full(v.size, np.nan)
            applied_segments = 0
            column_skipped_segments = 0
            segment_start = 0
            relative_gaps = [
                (gap_start - s0, gap_end - s0)
                for gap_start, gap_end in processing_gaps
            ]
            for gap_start, gap_end in relative_gaps + [(v.size, v.size)]:
                if gap_start > segment_start:
                    segment = v[segment_start:gap_start]
                    clean, mask = _interp_nan(segment)
                    if clean is not None:
                        try:
                            if despike_params is None:
                                filtered = _apply(
                                    kind, clean, fs, order, f1, f2, window_s)
                                local_replaced = None
                            else:
                                filtered, local_replaced = _despike(
                                    clean, *despike_params, replacement)
                        except ValueError as exc:
                            if ("range too short" not in str(exc)
                                    and "window longer" not in str(exc)):
                                raise
                            skipped_segments += 1
                            column_skipped_segments += 1
                        else:
                            filtered[~mask] = np.nan
                            y[segment_start:gap_start] = filtered
                            if local_replaced is not None:
                                local_replaced &= mask
                                replaced_mask[
                                    segment_start:gap_start
                                ] = local_replaced
                            applied_segments += 1
                segment_start = max(segment_start, gap_end)
            if applied_segments == 0 and column_skipped_segments:
                raise ValueError(
                    "continuous regions around missing-data gaps are too "
                    "short for this filter")
            filt[c] = y

        if replaced_mask is not None:
            # Envelope filtering may process bucket-aligned shoulder samples
            # beyond the requested bounds. Counts describe only the user's
            # requested range, even though all processing stays full-rate.
            count_start = max(0, i0 - s0)
            count_end = min(v.size, i1 - s0)
            requested_replacements = replaced_mask[count_start:count_end]
            replacement_counts[c] = int(requested_replacements.sum())
            spike_event_counts[c] = (
                int(requested_replacements[0])
                + int(np.count_nonzero(
                    requested_replacements[1:]
                    & ~requested_replacements[:-1]))
                if requested_replacements.size else 0)

    warn = {"nan_counts": {c: n for c, n in nan_counts.items() if n},
            "boundary_warning": boundary,
            "time_gap_count": requested_gap_count,
            "gap_segment_warning": skipped_segments > 0}
    if despike_params is not None:
        warn["replacement_counts"] = replacement_counts
        warn["spike_event_counts"] = spike_event_counts

    if raw_mode:
        return {"mode": "raw", "level": level, "n_raw": n_raw,
                "i0": i0, "i1": i1,
                "t": to_json_list(df[tcol].to_numpy()[::level]),
                "series": {c: to_json_list(filt[c][::level]) for c in cols},
                **warn}

    t = (pl.scan_parquet(test_dir / "pyramid" / f"L{level}.parquet")
         .slice(b0, b1 - b0).select([tcol]).collect())[tcol].to_numpy()
    series = {c: bucket_minmax(filt[c], level) for c in cols}

    # same bucket-merge rule as store.read_window when over the budget
    t, series, merge = merge_over_cap(t, series, budget)

    return {"mode": "envelope", "level": level * merge, "n_raw": n_raw,
            "i0": i0, "i1": i1, "t": to_json_list(t),
            "series": {c: {"min": to_json_list(mn), "max": to_json_list(mx)}
                       for c, (mn, mx) in series.items()},
            **warn}


def spectrum(name: str, col: str, mode: str, t0: float | None,
             t1: float | None, nperseg: int = 4096,
             max_bins: int = 4000,
             rpm_col: str | None = None) -> dict:
    """FFT magnitude spectrum or Welch PSD of one column over [t0, t1].

    When ``rpm_col`` is supplied, the response also carries the mean shaft
    speed over the identical sample window.  The frontend uses that reference
    speed to express frequency as cycles per revolution (order).  This is a
    fixed-speed normalization, not angular resampling, so it is most useful on
    steady-speed test points or a narrowly zoomed full-test range.
    """
    meta = get_meta(name)
    if meta is None:
        raise FileNotFoundError(name)
    fs = meta["fs_hz"]
    i0, i1 = window_bounds(meta, t0, t1)
    n = max(0, i1 - i0)
    gap_count = len(_known_gap_slices(meta, i0, i1))
    if gap_count:
        noun = "gap" if gap_count == 1 else "gaps"
        raise ValueError(
            f"selected range crosses {gap_count} missing-data {noun}; "
            "zoom in or choose a test point that stays within one continuous "
            "region")
    if n > MAX_FILTER_SAMPLES:
        raise ValueError(
            f"range spans {n} samples (max {MAX_FILTER_SAMPLES}); "
            "zoom in or select a test point")
    if n < 16:
        raise ValueError("range too short for a spectrum")

    requested_cols = [col]
    if rpm_col is not None and rpm_col not in requested_cols:
        requested_cols.append(rpm_col)
    frame = (pl.scan_parquet(TESTS_DIR / name / "data.parquet")
             .slice(i0, n).select(requested_cols).collect())
    v = frame[col].to_numpy().astype(np.float64)
    clean, mask = _interp_nan(v)
    nan_count = int(v.size - mask.sum())
    if clean is None:
        raise ValueError("range is all NaN")
    clean = signal.detrend(clean, type="constant")  # drop DC so it can't dwarf peaks

    extra = {}
    if mode == "welch":
        seg = int(min(max(nperseg, 64), clean.size))
        freqs, mag = signal.welch(clean, fs=fs, nperseg=seg)
        extra["nperseg"] = seg
    else:
        mag = np.abs(np.fft.rfft(clean)) * 2 / clean.size
        mag[0] /= 2
        if clean.size % 2 == 0:
            mag[-1] /= 2
        freqs = np.fft.rfftfreq(clean.size, d=1.0 / fs)

    # cap payload; max per bucket so spectral peaks survive
    factor = max(1, math.ceil(len(freqs) / max_bins))
    if factor > 1:
        _, mag = bucket_minmax(mag, factor)
        freqs = freqs[::factor][: len(mag)]

    rpm_reference = {}
    if rpm_col is not None:
        rpm = frame[rpm_col].to_numpy().astype(np.float64)
        finite_rpm = np.abs(rpm[np.isfinite(rpm)])
        if finite_rpm.size == 0:
            raise ValueError(f"RPM column '{rpm_col}' is all NaN in the selected range")
        mean_rpm = float(np.mean(finite_rpm))
        if not math.isfinite(mean_rpm) or mean_rpm <= 0:
            raise ValueError(
                f"RPM column '{rpm_col}' has no positive mean speed in the selected range")
        rpm_reference = {
            "rpm_col": rpm_col,
            "mean_rpm": mean_rpm,
            "min_rpm": float(np.min(finite_rpm)),
            "max_rpm": float(np.max(finite_rpm)),
        }

    return {"mode": mode, "col": col, "fs_hz": fs, "n_samples": n,
            "nan_count": nan_count,
            "freqs": [round(float(f), 4) for f in freqs],
            "mag": [float(m) if math.isfinite(m) else None for m in mag],
            **rpm_reference, **extra}
