"""Windowed peak-amplitude FFT, reduced to a bounded time/frequency grid.

Call under data_read. Complete windows stay anchored to the selected source and
use its original sample rate. V1 computes every window; V2 refines visible native
frames/bins before display reduction. Cells retain explicit maximum ranges. No
PSD, order tracking, time-plot filters, zero padding or incomplete edge windows.
"""
import math

import numpy as np
import polars as pl
from scipy import signal

from . import dsp

VERSION = "kiha-waterfall-v1"
DETAIL_VERSION = "kiha-waterfall-v2"
MAX_TIMES = 256
MAX_FREQS = 512
DETAIL_MAX_TIMES = 512
DETAIL_MAX_FREQS = 2048
MAX_WINDOW_SAMPLES = 1_048_576
MAX_BATCH_SAMPLES = 1_048_576
RESOLUTIONS_HZ = (.5, .25, .1)
WINDOWS = (64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384)


def validate_detail_options(high_detail, resolution_hz, frequency_range, time_range):
    if not high_detail and any(v is not None for v in (resolution_hz, frequency_range, time_range)):
        raise ValueError("frequency/time refinement and target spacing require high_detail")
    if resolution_hz is not None and resolution_hz not in RESOLUTIONS_HZ:
        raise ValueError("choose a supported frequency spacing: 0.5, 0.25 or 0.1 Hz")
    for label, bounds in (("frequency", frequency_range), ("elapsed time", time_range)):
        if bounds is not None and (len(bounds) != 2 or not all(math.isfinite(v) for v in bounds)
                                   or bounds[0] >= bounds[1]):
            raise ValueError(f"{label} bounds must be finite and increasing")


def _visible_indices(edges, bounds):
    """Contiguous native cells intersecting the viewport, without reanchoring."""
    if bounds is None:
        return 0, len(edges) - 1
    first = int(np.searchsorted(edges[1:], bounds[0], side='left'))
    stop = int(np.searchsorted(edges[:-1], bounds[1], side='right'))
    return first, max(first, stop)


def calculate(name, col, t0=None, t1=None, *, tp_id=None, nperseg=1024, overlap=50,
              high_detail=False, resolution_hz=None, frequency_range=None, time_range=None):
    validate_detail_options(high_detail, resolution_hz, frequency_range, time_range)
    if (resolution_hz is None and nperseg not in WINDOWS) or overlap not in (0, 25, 50, 75):
        raise ValueError("choose a supported FFT window and overlap")
    if tp_id is not None and (t0 is not None or t1 is not None):
        raise ValueError("tp_id cannot be combined with time bounds")
    if any(v is not None and not math.isfinite(v) for v in (t0, t1)):
        raise ValueError("time bounds must be finite")
    if t0 is not None and t1 is not None and t0 > t1:
        raise ValueError("time bounds must be increasing")
    meta = dsp.get_meta(name)
    if meta is None:
        raise FileNotFoundError(name)
    if col not in meta['columns']:
        raise ValueError(f"unknown column: {col}")
    fs = float(meta['fs_hz'])
    if not math.isfinite(fs) or fs <= 0:
        raise ValueError("sample rate must be finite and positive")
    if resolution_hz is not None:
        required = fs / resolution_hz
        if not math.isfinite(required) or required > MAX_WINDOW_SAMPLES:
            raise ValueError(f"requested spacing needs more than {MAX_WINDOW_SAMPLES} samples per window; "
                             "choose a coarser frequency spacing")
        # Even complete windows keep DC/Nyquist scaling and source centers exact.
        nperseg = max(4, 2 * math.ceil(required / 2))
    i0, i1 = dsp.testpoint_range(name, tp_id) if tp_id is not None else dsp.window_bounds(meta, t0, t1)
    n = i1 - i0
    if n < nperseg:
        if resolution_hz is not None:
            raise ValueError(f"{resolution_hz:g} Hz spacing requires {nperseg / fs:g} seconds "
                             f"({nperseg} samples) per FFT window; range has {n} samples "
                             f"({n / fs:g} seconds). Choose a coarser spacing or a longer interval")
        raise ValueError(f"range has {n} samples; choose a smaller FFT window (currently {nperseg}) or a longer interval")
    if n > dsp.MAX_FILTER_SAMPLES:
        raise ValueError(f"range spans {n} samples (max {dsp.MAX_FILTER_SAMPLES}); select a shorter interval")
    if dsp._known_gap_slices(meta, i0, i1):
        raise ValueError("selected range crosses missing-data gaps; select a continuous interval")
    dsp.progress.checkpoint()
    tcol = meta['time_column']
    frame = (pl.scan_parquet(dsp.TESTS_DIR / name / 'data.parquet').slice(i0, n)
             .select(list(dict.fromkeys([tcol, col]))).collect())
    if frame.height != n:
        raise ValueError("stored sample count changed; reload the test")
    times = frame[tcol].to_numpy().astype(np.float64)
    if not np.isfinite(times).all() or np.any(np.diff(times) < 0):
        raise ValueError("selected range has invalid timestamps")
    values, finite = dsp._interp_nan(frame[col].to_numpy().astype(np.float64))
    if values is None:
        raise ValueError("selected signal has fewer than two finite samples")
    hop = nperseg * (100 - overlap) // 100
    count = 1 + (n - nperseg) // hop
    bins = nperseg // 2 + 1
    starts = np.arange(count) * hop
    centers = (starts + (nperseg - 1) / 2) / fs
    native_time_edges = np.r_[centers - hop / (2 * fs), centers[-1] + hop / (2 * fs)]
    native_freq_edges = np.r_[0., (np.arange(1, bins) - .5) * fs / nperseg, fs / 2]
    tfirst, tstop = _visible_indices(native_time_edges, time_range)
    ffirst, fstop = _visible_indices(native_freq_edges, frequency_range)
    tf = max(1, math.ceil((tstop - tfirst) / (DETAIL_MAX_TIMES if high_detail else MAX_TIMES)))
    ff = max(1, math.ceil((fstop - ffirst) / (DETAIL_MAX_FREQS if high_detail else MAX_FREQS)))
    empty = tfirst == tstop or ffirst == fstop
    fstarts = np.arange(ffirst, fstop, ff) if not empty else np.array([], dtype=int)
    tstarts = np.arange(tfirst, tstop, tf) if not empty else np.array([], dtype=int)
    # Bound both windows and total sample workspace per native call.
    batch_size = min(64, max(1, MAX_BATCH_SAMPLES // nperseg))
    window = signal.get_window('hann', nperseg, fftbins=True)
    grid = np.zeros((len(tstarts), len(fstarts)))
    for offset in range(tfirst, tstop if not empty else tfirst, batch_size):
        dsp.progress.checkpoint()
        indices = starts[offset:min(offset + batch_size, tstop), None] + np.arange(nperseg)
        segments = values[indices]
        segments -= segments.mean(axis=1, keepdims=True)
        mag = np.abs(np.fft.rfft(segments * window, axis=1)) * (2 / window.sum())
        mag[:, 0] /= 2
        mag[:, -1] /= 2  # supported windows are all even
        if not np.isfinite(mag).all():
            raise ValueError("FFT produced nonfinite magnitudes; check the signal scale")
        reduced = np.maximum.reduceat(mag[:, ffirst:fstop], fstarts - ffirst, axis=1)
        np.maximum.at(grid, (np.arange(offset, offset + len(segments)) - tfirst) // tf, reduced)
    time_edges = native_time_edges[np.r_[tstarts, tstop]] if not empty else np.array([])
    freq_edges = native_freq_edges[np.r_[fstarts, fstop]] if not empty else np.array([])
    used = int(starts[-1] + nperseg)
    def source_centers(frame_indices):
        left = starts[frame_indices] + nperseg // 2 - 1
        return (times[left] + (times[left + 1] - times[left]) * .5).tolist()

    result = {
        'col': col, 'tp_id': tp_id, 'i0': i0, 'i1': i1, 'fs_hz': fs,
        'n_samples': n, 'nan_count': int(n - finite.sum()),
        'time_start_s': float(times[0]), 'time_end_s': float(times[-1]),
        'frequency_edges_hz': freq_edges.tolist(), 'time_edges_s': time_edges.tolist(),
        'magnitude': grid.tolist(),
        'source_frame_start_s': source_centers(tstarts),
        'source_frame_end_s': source_centers(np.minimum(tstarts + tf, tstop) - 1),
        'method': {'version': DETAIL_VERSION if high_detail else VERSION, 'window': 'hann_periodic', 'nperseg': nperseg,
                   'noverlap': nperseg - hop, 'hop_samples': hop, 'nfft': nperseg,
                   'window_seconds': nperseg / fs, 'step_seconds': hop / fs,
                   'bin_spacing_hz': fs / nperseg, 'detrend': 'constant_per_window',
                   'scaling': 'onesided_peak_amplitude', 'units': 'U',
                   'normalization': 'abs(rfft((x-mean(x))*w))/sum(w); double interior bins',
                   'missing_values': 'linear_by_index_hold_edges', 'prefilter': 'none',
                   'sampling': 'metadata_fs', 'padding': 'none',
                   'frame_count': count, 'used_samples': used, 'trailing_samples': n - used},
        'reduction': {'method': 'max' if tf > 1 or ff > 1 else 'none',
                      'time_factor': tf, 'frequency_factor': ff,
                      'native_frames': count, 'native_bins': bins},
        'quality': {'gap_metadata_available': isinstance(meta.get('time_gap_ranges'), list),
                    **{key: meta.get(key) for key in ('time_source', 'time_quantized', 'jitter_warning')}},
        'analysis': dsp.analysis.source_context(name, meta, [col], i0, i1, tp_id=tp_id, t0=t0, t1=t1),
    }
    if high_detail:
        result['method']['requested_spacing_hz'] = resolution_hz
        result['reduction'].update(selected_native_frames=tstop - tfirst, selected_native_bins=fstop - ffirst)
        result['grid'] = {
            'frequency_range_hz': list(frequency_range) if frequency_range is not None else None,
            'time_range_s': list(time_range) if time_range is not None else None,
            'full_frequency_range_hz': [0., fs / 2],
            'full_time_range_s': [float(native_time_edges[0]), float(native_time_edges[-1])],
            'first_frame_index': tfirst, 'stop_frame_index': tstop,
            'first_bin_index': ffirst, 'stop_bin_index': fstop,
            'max_time_cells': DETAIL_MAX_TIMES, 'max_frequency_cells': DETAIL_MAX_FREQS,
        }
    return result
