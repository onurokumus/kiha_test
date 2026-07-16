"""Server-side signal processing: filters over a time window + FFT/Welch spectra.

Filtered series are served in the same window format as store.read_window
(raw vs min/max envelope, identical level selection and bucket boundaries),
so the frontend can overlay them on the raw series on a shared time axis.
"""

import math

import numpy as np
import polars as pl
from scipy import signal
from scipy.ndimage import uniform_filter1d

from .config import (MAX_FILTER_SAMPLES, MAX_POINTS_RAW, POINT_BUDGET_CAP,
                     PYRAMID_LEVELS, TESTS_DIR)
from .ingest import _bucket_minmax
from .store import _nan_to_none, get_meta

FILTER_KINDS = {"lowpass", "highpass", "bandpass", "bandstop",
                "moving_avg", "detrend"}


def _window_bounds(meta: dict, t0: float | None, t1: float | None):
    fs = meta["fs_hz"]
    n_rows = meta["n_rows"]
    t_start = meta.get("t_start") or 0.0
    duration = n_rows / fs
    lo = t_start if t0 is None else max(t0, t_start)
    hi = t_start + duration if t1 is None else min(t1, t_start + duration)
    i0 = max(0, int((lo - t_start) * fs))
    i1 = min(n_rows, int(math.ceil((hi - t_start) * fs)) + 1)
    return i0, i1


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


def _apply(kind: str, v: np.ndarray, fs: float, order: int,
           f1: float | None, f2: float | None,
           window_s: float | None) -> np.ndarray:
    if kind == "detrend":
        return signal.detrend(v, type="linear")
    if kind == "moving_avg":
        if window_s is None or window_s <= 0:
            raise ValueError("window_s must be > 0")
        w = max(1, int(round(window_s * fs)))
        if w >= v.size:
            raise ValueError("moving-average window longer than the range")
        return uniform_filter1d(v, size=w, mode="nearest")
    order = min(max(int(order), 1), 10)
    sos = _make_sos(kind, order, f1, f2, fs)
    if v.size <= 3 * (2 * sos.shape[0] + 1):  # sosfiltfilt padding needs room
        raise ValueError("range too short for this filter order")
    return signal.sosfiltfilt(sos, v)


def filtered_window(name: str, cols: list[str], kind: str,
                    t0: float | None, t1: float | None, px: int,
                    order: int = 4, f1: float | None = None,
                    f2: float | None = None,
                    window_s: float | None = None) -> dict:
    """Filter cols over [t0, t1] at full resolution, then serve like
    store.read_window so the result aligns 1:1 with the raw window."""
    meta = get_meta(name)
    if meta is None:
        raise FileNotFoundError(name)
    if kind not in FILTER_KINDS:
        raise ValueError(f"unknown filter type '{kind}'")
    fs = meta["fs_hz"]
    n_rows = meta["n_rows"]
    tcol = meta["time_column"]
    i0, i1 = _window_bounds(meta, t0, t1)
    n_raw = max(0, i1 - i0)
    if n_raw > MAX_FILTER_SAMPLES:
        raise ValueError(
            f"range spans {n_raw} samples (max {MAX_FILTER_SAMPLES}); "
            "zoom in or select a test point")
    if n_raw < 8:
        raise ValueError("range too short")
    boundary = i0 == 0 or i1 >= n_rows
    budget = min(max(2 * px, 1000), POINT_BUDGET_CAP)
    test_dir = TESTS_DIR / name
    raw_mode = n_raw <= max(MAX_POINTS_RAW, budget)

    if raw_mode:
        s0, s1 = i0, i1
    else:
        level = PYRAMID_LEVELS[-1]
        for f in PYRAMID_LEVELS:
            if n_raw / f <= POINT_BUDGET_CAP:
                level = f
                break
        # bucket-aligned slice so envelope buckets match the pyramid's
        b0, b1 = i0 // level, -(-i1 // level)
        s0, s1 = b0 * level, min(b1 * level, n_rows)

    df = (pl.scan_parquet(test_dir / "data.parquet")
          .slice(s0, s1 - s0)
          .select(([tcol] if raw_mode else []) + cols).collect())

    nan_counts: dict[str, int] = {}
    filt: dict[str, np.ndarray] = {}
    for c in cols:
        v = df[c].to_numpy().astype(np.float64)
        clean, mask = _interp_nan(v)
        nan_counts[c] = int(v.size - mask.sum())
        if clean is None:
            filt[c] = np.full(v.size, np.nan)
            continue
        y = _apply(kind, clean, fs, order, f1, f2, window_s)
        y[~mask] = np.nan  # keep gaps as gaps
        filt[c] = y

    warn = {"nan_counts": {c: n for c, n in nan_counts.items() if n},
            "boundary_warning": boundary}

    if raw_mode:
        return {"mode": "raw", "level": 1, "n_raw": n_raw, "i0": i0, "i1": i1,
                "t": _nan_to_none(df[tcol].to_numpy()),
                "series": {c: _nan_to_none(filt[c]) for c in cols},
                **warn}

    t = (pl.scan_parquet(test_dir / "pyramid" / f"L{level}.parquet")
         .slice(b0, b1 - b0).select([tcol]).collect())[tcol].to_numpy()
    series = {c: _bucket_minmax(filt[c], level) for c in cols}

    # same bucket-merge rule as store.read_window when over the cap
    merge = max(1, math.ceil(len(t) / POINT_BUDGET_CAP))
    if merge > 1:
        nb = -(-len(t) // merge)
        pad = nb * merge - len(t)

        def _merged(arr, fn):
            if pad:
                arr = np.concatenate([arr, np.full(pad, np.nan)])
            with np.errstate(invalid="ignore"):
                return fn(arr.reshape(nb, merge), axis=1)

        series = {c: (_merged(mn, np.nanmin), _merged(mx, np.nanmax))
                  for c, (mn, mx) in series.items()}
        t = t[::merge][:nb]

    return {"mode": "envelope", "level": level * merge, "n_raw": n_raw,
            "i0": i0, "i1": i1, "t": _nan_to_none(t),
            "series": {c: {"min": _nan_to_none(mn), "max": _nan_to_none(mx)}
                       for c, (mn, mx) in series.items()},
            **warn}


def spectrum(name: str, col: str, mode: str, t0: float | None,
             t1: float | None, nperseg: int = 4096,
             max_bins: int = 4000) -> dict:
    """FFT magnitude spectrum or Welch PSD of one column over [t0, t1]."""
    meta = get_meta(name)
    if meta is None:
        raise FileNotFoundError(name)
    fs = meta["fs_hz"]
    i0, i1 = _window_bounds(meta, t0, t1)
    n = max(0, i1 - i0)
    if n > MAX_FILTER_SAMPLES:
        raise ValueError(
            f"range spans {n} samples (max {MAX_FILTER_SAMPLES}); "
            "zoom in or select a test point")
    if n < 16:
        raise ValueError("range too short for a spectrum")

    v = (pl.scan_parquet(TESTS_DIR / name / "data.parquet")
         .slice(i0, n).select([col]).collect())[col].to_numpy().astype(np.float64)
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
        _, mag = _bucket_minmax(mag, factor)
        freqs = freqs[::factor][: len(mag)]

    return {"mode": mode, "col": col, "fs_hz": fs, "n_samples": n,
            "nan_count": nan_count,
            "freqs": [round(float(f), 4) for f in freqs],
            "mag": [float(m) if math.isfinite(m) else None for m in mag],
            **extra}
