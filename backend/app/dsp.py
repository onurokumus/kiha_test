"""Server-side signal processing: filters over a time window + FFT/Welch spectra.

Full-test filtered series use the same window format, display levels and
bucket boundaries as store.read_window. Saved TP filters share exact rows
and the original trace's time origin, but use TP-local min/max buckets.
"""

import math
from dataclasses import dataclass

import numpy as np
import polars as pl
from scipy import signal
from scipy.ndimage import median_filter, uniform_filter1d

from . import analysis_metadata as analysis
from . import export_progress as progress
from .config import MAX_FILTER_SAMPLES, TESTS_DIR
from .store import (bucket_minmax, get_meta, merge_over_cap, plot_budget,
                    resolve_window_display, testpoint_range, to_json_list,
                    window_bounds)

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
        progress.checkpoint()
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


@dataclass
class FilteredSamples:
    """Full-rate processing result; never use reduced JSON arrays for export.

    ``frame``/``filtered`` span [s0,s1), which may include full-test envelope
    shoulders. The user's source rows are [i0,i1). Callers hold data_read.
    """

    frame: pl.DataFrame
    filtered: dict[str, np.ndarray]
    time_column: str
    i0: int
    i1: int
    s0: int
    s1: int
    mode: str
    level: int
    budget: int
    warnings: dict
    tp_id: int | None
    analysis: dict


def filtered_samples(name: str, cols: list[str], kind: str,
                    t0: float | None, t1: float | None, px: int,
                    order: int = 4, f1: float | None = None,
                    f2: float | None = None,
                    window_s: float | None = None,
                    display: str = "auto",
                    max_spike_s: float | None = DEFAULT_MAX_SPIKE_S,
                    threshold: float = DEFAULT_DESPIKE_THRESHOLD,
                    abs_floor: float = DEFAULT_DESPIKE_ABS_FLOOR,
                    replacement: str = "linear",
                    tp_id: int | None = None) -> FilteredSamples:
    """Shared full-resolution DSP for plots and exports, before reduction.

    A saved ``tp_id`` instead selects its exact half-open rows. TP-local
    envelope buckets must not read adjacent samples; their timestamps need
    not match the original trace's ordered-extrema reduction. Both traces
    use the actual first stored sample as their relative-time origin.
    """
    meta = get_meta(name)
    if meta is None:
        raise FileNotFoundError(name)
    if kind not in FILTER_KINDS:
        raise ValueError(f"unknown filter type '{kind}'")
    fs = meta["fs_hz"]
    n_rows = meta["n_rows"]
    tcol = meta["time_column"]
    if tp_id is not None:
        if t0 is not None or t1 is not None:
            raise ValueError("tp_id cannot be combined with t0 or t1")
        i0, i1 = testpoint_range(name, tp_id)
    else:
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

    if raw_mode or tp_id is not None:
        s0, s1 = i0, i1
    else:
        # bucket-aligned slice so envelope buckets match the pyramid's
        b0, b1 = i0 // level, -(-i1 // level)
        s0, s1 = b0 * level, min(b1 * level, n_rows)

    progress.checkpoint()
    df = (pl.scan_parquet(test_dir / "data.parquet")
          .slice(s0, s1 - s0)
          .select([tcol] + cols).collect())

    processing_gaps = _known_gap_slices(meta, s0, s1)
    requested_gap_count = len(_known_gap_slices(meta, i0, i1))
    nan_counts: dict[str, int] = {}
    filt: dict[str, np.ndarray] = {}
    replacement_counts: dict[str, int] = {}
    spike_event_counts: dict[str, int] = {}
    skipped_segments = 0
    for c in cols:
        progress.checkpoint()
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
                progress.checkpoint()
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

    progress.checkpoint()
    details = analysis.source_context(name, meta, cols, i0, i1, tp_id=tp_id, t0=t0, t1=t1)
    parameters = {'kind': kind}
    if kind in {'lowpass', 'highpass', 'bandpass', 'bandstop'}:
        parameters.update(order=min(max(int(order), 1), 10), f1_hz=f1,
                          implementation='scipy.signal.sosfiltfilt', design='butterworth',
                          padtype='odd', padlen='scipy_default')
        if kind in {'bandpass', 'bandstop'}:
            parameters['f2_hz'] = f2
    elif kind == 'moving_avg':
        parameters.update(window_s=window_s,
            window_samples=max(1, int(round(window_s * fs))) if window_s is not None and window_s > 0 else None,
            implementation='scipy.ndimage.uniform_filter1d', boundary='nearest', origin=0)
    elif kind == 'detrend':
        parameters.update(implementation='scipy.signal.detrend', type='linear')
    else:
        parameters.update(window_s=DEFAULT_DESPIKE_WINDOW_S if window_s is None else window_s,
            max_spike_s=DEFAULT_MAX_SPIKE_S if max_spike_s is None else max_spike_s,
            window_samples=despike_params[0], max_spike_samples=despike_params[1],
            threshold=despike_params[2], abs_floor=despike_params[3],
            mad_normal_scale=_MAD_NORMAL_SCALE, replacement=replacement)
    details.update(method_version='kiha-time-filter-v1', filter=parameters,
        processing_rows={'i0': s0, 'i1': s1, 'bounds': 'half_open'},
        source_centers={'first_time_s': analysis.clean(float(df[tcol][i0-s0])),
                        'last_time_s': analysis.clean(float(df[tcol][i1-s0-1]))},
        missing_values='linear_by_index_hold_edges_then_restore_nonfinite',
        known_gap_policy='independent_continuous_regions', processing_gap_ranges=processing_gaps,
        warnings=warn, display={'mode': resolved_mode, 'level': level, 'px': px, 'request': display})
    return FilteredSamples(
        df, filt, tcol, i0, i1, s0, s1, resolved_mode, level, budget, warn, tp_id, details)


def filtered_window(name: str, cols: list[str], kind: str,
                    t0: float | None, t1: float | None, px: int,
                    order: int = 4, f1: float | None = None,
                    f2: float | None = None,
                    window_s: float | None = None,
                    display: str = "auto",
                    max_spike_s: float | None = DEFAULT_MAX_SPIKE_S,
                    threshold: float = DEFAULT_DESPIKE_THRESHOLD,
                    abs_floor: float = DEFAULT_DESPIKE_ABS_FLOOR,
                    replacement: str = "linear",
                    tp_id: int | None = None) -> dict:
    """Reduce the shared full-rate result using the existing plot contract."""
    samples = filtered_samples(
        name, cols, kind, t0, t1, px, order=order, f1=f1, f2=f2,
        window_s=window_s, display=display, max_spike_s=max_spike_s,
        threshold=threshold, abs_floor=abs_floor, replacement=replacement,
        tp_id=tp_id)
    df, filt, tcol = samples.frame, samples.filtered, samples.time_column
    i0, i1, level, budget = samples.i0, samples.i1, samples.level, samples.budget
    n_raw, raw_mode, warn = i1 - i0, samples.mode == "raw", samples.warnings
    scope = ({"tp_id": tp_id, "time_origin_s": float(df[tcol][0])}
             if tp_id is not None else {})
    scope["analysis"] = samples.analysis

    if raw_mode:
        t = df[tcol].to_numpy()[::level]
        if tp_id is not None:
            scope["relative_t"] = to_json_list(t - scope["time_origin_s"])
        return {"mode": "raw", "level": level, "n_raw": n_raw,
                "i0": i0, "i1": i1,
                "t": to_json_list(t),
                "series": {c: to_json_list(filt[c][::level]) for c in cols},
                **warn, **scope}

    if tp_id is not None:
        t = df[tcol].to_numpy()[::level]
    else:
        test_dir = TESTS_DIR / name
        b0, b1 = i0 // level, -(-i1 // level)
        t = (pl.scan_parquet(test_dir / "pyramid" / f"L{level}.parquet")
             .slice(b0, b1 - b0).select([tcol]).collect())[tcol].to_numpy()
    series = {c: bucket_minmax(filt[c], level) for c in cols}

    # same bucket-merge rule as store.read_window when over the budget
    t, series, merge = merge_over_cap(t, series, budget)
    if tp_id is not None:
        scope["relative_t"] = to_json_list(t - scope["time_origin_s"])

    return {"mode": "envelope", "level": level * merge, "n_raw": n_raw,
            "i0": i0, "i1": i1, "t": to_json_list(t),
            "series": {c: {"min": to_json_list(mn), "max": to_json_list(mx)}
                       for c, (mn, mx) in series.items()},
            **warn, **scope}


SPECTRUM_METHOD_VERSION = "kiha-spectrum-v2"


@dataclass
class SpectrumResult:
    """Unrounded, unreduced Hz bins and ordinates for analysis/export reuse.

    Callers hold the test read lock for the entire operation.  Metadata records
    the samples and processing actually used.  Welch values remain density per
    Hz even when a client chooses to display frequency as shaft order.
    """
    freqs: np.ndarray
    mag: np.ndarray
    metadata: dict


def spectrum_samples(name: str, col: str, mode: str, t0: float | None,
                     t1: float | None, nperseg: int = 4096,
                     rpm_col: str | None = None,
                     tp_id: int | None = None) -> SpectrumResult:
    """Compute one full-resolution stored-signal FFT or Welch result.

    Saved TP rows are authoritative; legacy/open TP bounds use the shared
    resolver.  Full-test windows retain the existing nominal-time conversion.
    Display filtering never changes this stored-signal path.
    """
    if mode not in {"fft", "welch"}:
        raise ValueError(f"unknown spectrum mode: {mode}")
    if tp_id is not None and (t0 is not None or t1 is not None):
        raise ValueError("tp_id cannot be combined with t0 or t1")
    if any(value is not None and not math.isfinite(value)
           for value in (t0, t1)):
        raise ValueError("time bounds must be finite")
    meta = get_meta(name)
    if meta is None:
        raise FileNotFoundError(name)
    fs = float(meta["fs_hz"])
    if not math.isfinite(fs) or fs <= 0:
        raise ValueError("sample rate must be finite and positive")
    i0, i1 = (testpoint_range(name, tp_id) if tp_id is not None
              else window_bounds(meta, t0, t1))
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

    tcol = meta["time_column"]
    requested_cols = list(dict.fromkeys([tcol, col]))
    if rpm_col is not None and rpm_col not in requested_cols:
        requested_cols.append(rpm_col)
    progress.checkpoint()
    frame = (pl.scan_parquet(TESTS_DIR / name / "data.parquet")
             .slice(i0, n).select(requested_cols).collect())
    progress.checkpoint()
    if frame.height != n:
        raise ValueError("stored sample count differs from metadata; reload the test")
    first_time, last_time = float(frame[tcol][0]), float(frame[tcol][-1])
    if not math.isfinite(first_time) or not math.isfinite(last_time):
        raise ValueError("selected range has nonfinite boundary timestamps")
    v = frame[col].to_numpy().astype(np.float64)
    clean, mask = _interp_nan(v)
    nan_count = int(v.size - mask.sum())
    if clean is None:
        raise ValueError("range is all NaN")
    clean = signal.detrend(clean, type="constant")  # drop DC so it can't dwarf peaks
    progress.checkpoint()

    extra = {}
    if mode == "welch":
        seg = int(min(max(nperseg, 64), clean.size))
        overlap = seg // 2
        # Spell out the existing SciPy defaults to keep the method reproducible
        # across library upgrades; periodic Hann is distinct from symmetric Hann.
        window = signal.get_window("hann", seg, fftbins=True)
        freqs, mag = signal.welch(
            clean, fs=fs, window=window, nperseg=seg, noverlap=overlap,
            nfft=seg, detrend="constant", return_onesided=True,
            scaling="density", average="mean")
        extra["nperseg"] = seg
        segment_count = 1 + (clean.size - seg) // (seg - overlap)
        used_samples = seg + (segment_count - 1) * (seg - overlap)
    else:
        mag = np.abs(np.fft.rfft(clean)) * 2 / clean.size
        mag[0] /= 2
        if clean.size % 2 == 0:
            mag[-1] /= 2
        freqs = np.fft.rfftfreq(clean.size, d=1.0 / fs)
        seg, overlap, segment_count, used_samples = None, None, 1, clean.size
    progress.checkpoint()
    if not np.isfinite(mag).all():
        raise ValueError("spectrum contains nonfinite results; check signal magnitude")

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
            "rpm_finite_count": int(finite_rpm.size),
            "rpm_nan_count": int(rpm.size - finite_rpm.size),
        }
    nfft = seg if seg is not None else clean.size
    peak = int(np.argmax(mag))  # First native bin wins an exact tie.
    metadata = {
        "analysis": analysis.source_context(name, meta, [col] + ([rpm_col] if rpm_col else []), i0, i1, tp_id=tp_id, t0=t0, t1=t1),
        "mode": mode, "col": col, "fs_hz": fs, "n_samples": n,
        "nan_count": nan_count, "finite_count": int(mask.sum()),
        "i0": i0, "i1": i1, "tp_id": tp_id,
        "time_start_s": first_time, "time_end_s": last_time,
        "method": {
            "version": SPECTRUM_METHOD_VERSION,
            "source": "stored", "prefilter": "none", "sampling": "metadata_fs",
            "missing_values": "linear_by_index_hold_edges", "detrend": "constant",
            "window": "hann_periodic" if mode == "welch" else "rectangular",
            "nfft": int(nfft), "bin_spacing_hz": fs / nfft, "onesided": True,
            "scaling": "density" if mode == "welch" else "peak_amplitude",
            "units": "U²/Hz" if mode == "welch" else "U",
            "nperseg": seg, "noverlap": overlap,
            "average": "mean" if mode == "welch" else None,
            "segment_count": int(segment_count), "used_samples": int(used_samples),
            "trailing_samples": int(clean.size - used_samples),
        },
        "quality": {
            "known_gap_count": 0,
            "gap_metadata_available": isinstance(meta.get("time_gap_ranges"), list),
            "time_source": meta.get("time_source"),
            "time_quantized": meta.get("time_quantized"),
            "jitter_warning": meta.get("jitter_warning"),
        },
        "peak": {"frequency_hz": float(freqs[peak]), "magnitude": float(mag[peak]),
                 "bin_index": peak},
        **rpm_reference, **extra,
    }
    return SpectrumResult(freqs=freqs, mag=mag, metadata=metadata)


def spectrum(name: str, col: str, mode: str, t0: float | None,
             t1: float | None, nperseg: int = 4096,
             max_bins: int = 4000, rpm_col: str | None = None,
             tp_id: int | None = None) -> dict:
    """Display payload preserving each retained maximum's actual native bin.

    ``max_bins`` is an internal display cap, never an analysis/export input.
    Maxima are unsuitable for density integration; use spectrum_samples for
    the unreduced, uniformly spaced result.  Frequencies are not rounded.
    """
    if not isinstance(max_bins, int) or isinstance(max_bins, bool) or max_bins < 1:
        raise ValueError("max_bins must be a positive integer")
    result = spectrum_samples(name, col, mode, t0, t1, nperseg,
                              rpm_col=rpm_col, tp_id=tp_id)
    count = len(result.freqs)
    factor = max(1, math.ceil(count / max_bins))
    if factor == 1:
        indices = np.arange(count)
    else:
        full_buckets = count // factor
        complete = result.mag[:full_buckets * factor].reshape(-1, factor)
        indices = np.arange(full_buckets) * factor + np.argmax(complete, axis=1)
        tail_start = full_buckets * factor
        if tail_start < count:
            indices = np.append(indices, tail_start + np.argmax(result.mag[tail_start:]))
    return {
        **result.metadata,
        "freqs": result.freqs[indices].tolist(),
        "mag": result.mag[indices].tolist(),
        "bin_indices": indices.tolist(),
        "reduction": {
            "method": "max-bin" if factor > 1 else "none", "factor": factor,
            "n_bins_original": count, "n_bins_returned": int(indices.size),
        },
    }
