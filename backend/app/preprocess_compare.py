"""Read-only, aligned comparison of saved preprocessing and retained originals.

Broad windows use both saved min/max pyramids with the same bucket boundaries.
Exact difference statistics are only calculated when the complete native window
fits the existing raw display budget; reduced extrema are never subtracted.
"""
import logging
import math
from pathlib import Path
from typing import Annotated
from uuid import UUID
import warnings

from fastapi import APIRouter, HTTPException, Query
import numpy as np
import polars as pl
import pyarrow.parquet as pq

from . import analysis_sources, preprocess, store
from .locks import data_read
from .paths import is_link_or_junction

router = APIRouter()
logger = logging.getLogger('kiha.preprocess_compare')


def _available(name: str) -> tuple[Path, dict]:
    try:
        directory = preprocess._directory(name)
        if is_link_or_junction(directory / 'status.json'):
            raise HTTPException(409, 'Linked test files cannot be compared.')
        meta = preprocess._ready(name)
        recipe = meta.get('preprocessing')
        if not preprocess.is_in_place(recipe) or not recipe.get('filters'):
            raise HTTPException(409, 'This flight has no saved preprocessing to compare.')
        return directory, meta
    except (OSError, ValueError, KeyError, TypeError):
        logger.warning("Comparison status could not be read for '%s'", name, exc_info=True)
        raise HTTPException(409, 'This flight\'s stored files are unavailable. '
                            'Check their permissions and reload the comparison.') from None


def _json(values: np.ndarray) -> list:
    """Retain float64 precision so tiny paired differences/time steps survive."""
    return [value if math.isfinite(value) else None for value in values.tolist()]


def _schema(directory: Path, meta: dict, column: str) -> None:
    with pq.ParquetFile(directory / 'data.parquet') as file:
        if file.metadata.num_rows != meta['n_rows']:
            raise ValueError('Stored sample counts do not match metadata.')
        if not {meta['time_column'], column}.issubset(file.schema_arrow.names):
            raise ValueError('Stored parameters do not match metadata.')


def _raw(directory: Path, tcol: str, column: str,
         i0: int, i1: int) -> tuple[np.ndarray, np.ndarray]:
    frame = (pl.scan_parquet(directory / 'data.parquet')
             .slice(i0, i1 - i0).select([tcol, column]).collect())
    return (frame[tcol].to_numpy().astype(np.float64),
            frame[column].to_numpy().astype(np.float64))


def _aligned(original: np.ndarray, current: np.ndarray, expected: int) -> None:
    if (len(original) != expected or len(current) != expected
            or not np.isfinite(original).all()
            or not np.array_equal(original, current)
            or (len(original) > 1 and not np.all(np.diff(original) > 0))):
        raise ValueError('Original and processed sample times are not aligned.')


def _gaps(meta: dict, i0: int, i1: int) -> list[tuple[int, int]]:
    ranges = []
    for key in ('time_gap_ranges', 'acquisition_gap_ranges'):
        for pair in meta.get(key) or []:
            if (not isinstance(pair, list) or len(pair) != 2
                    or any(type(value) is not int for value in pair)
                    or pair[0] < 0 or pair[1] <= pair[0]
                    or pair[1] > meta['n_rows']):
                raise ValueError('Stored acquisition gap bounds are invalid.')
            if pair[0] < i1 and pair[1] > i0:
                ranges.append((max(i0, pair[0]), min(i1, pair[1])))
    merged = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def _summary(original: np.ndarray, current: np.ndarray) -> dict:
    paired = np.isfinite(original) & np.isfinite(current)
    count = int(np.count_nonzero(paired))
    rms = maximum = None
    if count:
        with np.errstate(over='ignore', invalid='ignore'):
            delta = current[paired] - original[paired]
            scale = float(np.max(np.abs(delta)))
            if math.isfinite(scale):
                maximum = scale
                # Scaling prevents overflow from squaring large finite values.
                rms = scale * float(np.sqrt(np.mean((delta / scale) ** 2))) if scale else 0.0
    return {'finite_pairs': count, 'missing_pairs': len(original) - count,
            'rms_difference': rms, 'max_abs_difference': maximum}


def _envelope(directory: Path, tcol: str, column: str, level: int,
              i0: int, i1: int) -> tuple[np.ndarray, tuple[np.ndarray, np.ndarray]]:
    pyramid = directory / 'pyramid'
    path = pyramid / f'L{level}.parquet'
    if is_link_or_junction(pyramid) or is_link_or_junction(path):
        raise ValueError('Linked sample pyramids cannot be compared.')
    b0, b1 = i0 // level, math.ceil(i1 / level)
    frame = (pl.scan_parquet(path).slice(b0, b1 - b0)
             .select([tcol, f'{column}__min', f'{column}__max']).collect())
    times = frame[tcol].to_numpy().astype(np.float64)
    minimum = frame[f'{column}__min'].to_numpy().astype(np.float64).copy()
    maximum = frame[f'{column}__max'].to_numpy().astype(np.float64).copy()
    if len(times) != b1 - b0:
        raise ValueError('Stored pyramid sample counts are inconsistent.')
    # Edge buckets must describe only the requested window, not nearby peaks.
    for index in {0, len(times) - 1}:
        start, end = (b0 + index) * level, (b0 + index + 1) * level
        lo, hi = max(start, i0), min(end, i1)
        if lo != start or hi != end:
            raw_t, raw_y = _raw(directory, tcol, column, lo, hi)
            if not len(raw_t) or len(raw_t) != hi - lo:
                raise ValueError('Stored edge samples are unavailable.')
            times[index] = raw_t[0]
            finite = raw_y[np.isfinite(raw_y)]
            minimum[index] = np.min(finite) if len(finite) else np.nan
            maximum[index] = np.max(finite) if len(finite) else np.nan
    # Pyramids made by older ingestion can retain infinities; never serialize
    # them or imply that a one-sided, unbounded bucket is a finite envelope.
    invalid = ~np.isfinite(minimum) | ~np.isfinite(maximum)
    minimum[invalid] = maximum[invalid] = np.nan
    return times, (minimum, maximum)


@router.get('/api/tests/{name}/preprocess/compare')
def compare_preprocess(
    name: str,
    column: Annotated[str, Query(min_length=1, max_length=255)],
    source_id: UUID,
    source_revision: Annotated[str, Query(min_length=1, max_length=128)],
    t0: Annotated[float | None, Query(allow_inf_nan=False)] = None,
    t1: Annotated[float | None, Query(allow_inf_nan=False)] = None,
    px: Annotated[int, Query(ge=100, le=4000)] = 900,
):
    if t0 is not None and t1 is not None and t1 <= t0:
        raise HTTPException(400, 'Choose an end time after the start time.')
    _available(name)  # Do not wait behind an already-running whole-flight job.
    with data_read(name):
        directory, meta = _available(name)
        try:
            # A comparison must never migrate or repair identity files.
            if analysis_sources.read_identity(directory) is None:
                raise ValueError('Source identity is unavailable.')
            source = preprocess._source(name)
            if source['id'] != str(source_id) or source['revision'] != source_revision:
                raise HTTPException(409, 'This flight changed. Reload the comparison to use its current data.')
            original, baseline = preprocess.original_data(directory, meta)
            for key in ('columns', 'time_column', 'fs_hz', 'n_rows', 't_start',
                        'duration_s', 'time_gap_ranges', 'acquisition_gap_ranges'):
                if meta.get(key) != baseline.get(key):
                    raise ValueError('Original and processed recording metadata do not match.')
            if column not in meta['columns'] or column == meta['time_column']:
                raise HTTPException(400, 'Choose a data parameter available in this flight.')
            fs, count = float(meta['fs_hz']), meta['n_rows']
            start = float(meta.get('t_start') or 0)
            if (not math.isfinite(fs) or fs <= 0 or type(count) is not int
                    or count < 1 or not math.isfinite(start)):
                raise ValueError('Recording sample bounds are invalid.')
            _schema(directory, meta, column)
            _schema(original, baseline, column)
            i0, i1 = store.window_bounds(meta, t0, t1)
            i0, i1 = min(count, i0), min(count, i1)
            n_raw = i1 - i0
            budget = store.plot_budget(px)
            mode, level = store.resolve_window_display(n_raw, budget)
            gaps = _gaps(baseline, i0, i1)
            messages = []
            summary = None
            if mode == 'raw':
                times, orig = _raw(original, meta['time_column'], column, i0, i1)
                current_t, current = _raw(directory, meta['time_column'], column, i0, i1)
                _aligned(times, current_t, n_raw)
                orig, current = orig.copy(), current.copy()
                for lo, hi in gaps:
                    orig[lo - i0:hi - i0] = current[lo - i0:hi - i0] = np.nan
                if gaps:
                    messages.append('Known acquisition-gap rows are omitted from both traces and exact differences, including previously filled values.')
                summary = _summary(orig, current)
                if summary['finite_pairs'] and summary['max_abs_difference'] is None:
                    messages.append('Some paired differences exceed the supported numeric range. The traces remain available, but RMS and maximum absolute differences cannot be represented.')
                orig, current = _json(orig), _json(current)
            else:
                times, orig = _envelope(original, meta['time_column'], column, level, i0, i1)
                current_t, current = _envelope(directory, meta['time_column'], column, level, i0, i1)
                _aligned(times, current_t, math.ceil(i1 / level) - i0 // level)
                # Merge both sources together, so all four extrema have exactly
                # the same buckets. This retains peaks; striding would not.
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore', RuntimeWarning)
                    times, merged, merge = store.merge_over_cap(
                        times, {'original': orig, 'filtered': current}, budget)
                first_bucket = (i0 // level) * level
                level *= merge
                for lo, hi in gaps:
                    first = max(0, (lo - first_bucket) // level)
                    last = min(len(times), math.ceil((hi - first_bucket) / level))
                    for minimum, maximum in merged.values():
                        minimum[first:last] = maximum[first:last] = np.nan
                orig, current = [
                    {'min': _json(merged[key][0]),
                     'max': _json(merged[key][1])}
                    for key in ('original', 'filtered')]
                messages.append('Min/max bands use stored float32 bucket extrema, not paired samples. Zoom in for native values and exact differences; sparse missing samples may occur within a bucket.')
                if gaps:
                    messages.append('Buckets overlapping known acquisition gaps are hidden to avoid bridging missing time.')
            return {'source': source, 'column': column, 'mode': mode, 'level': level,
                    'n_raw': n_raw, 'i0': i0, 'i1': i1, 't': _json(times),
                    'original': orig, 'filtered': current,
                    'range': {'start': start, 'end': start + count / fs},
                    'gaps': [{'start': start + lo / fs, 'end': start + hi / fs}
                             for lo, hi in gaps],
                    'summary': summary, 'warnings': messages}
        except (OSError, ValueError, KeyError, TypeError, OverflowError, pl.exceptions.PolarsError):
            logger.warning("Comparison source verification failed for '%s'", name, exc_info=True)
            raise HTTPException(409, 'The original and processed data could not be compared safely. '
                                'Check this flight\'s stored files and reload the comparison.') from None
