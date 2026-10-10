"""Read-only, aligned comparison of saved preprocessing and retained originals.

Broad windows use both saved min/max pyramids with the same bucket boundaries.
Exact difference statistics are only calculated when the complete native window
fits the existing raw display budget; reduced extrema are never subtracted.
XY comparison streams aligned native rows and uses one bounded common stride
for both versions, with first-finite fallback for sparse recordings.
"""
import logging
import math
from contextlib import closing
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
    # Own the file descriptor before Arrow opens it: a corrupt footer can make
    # ParquetFile's constructor raise before its context manager can close it.
    with (directory / 'data.parquet').open('rb') as stream, pq.ParquetFile(stream) as file:
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


def _xy_pairs(original: Path, current: Path, tcol: str, x: str, y: str,
              i0: int, i1: int, max_points: int, gaps: list[tuple[int, int]]) -> dict:
    """Stream and validate both native grids before sampling common XY rows.

    Physical row-group boundaries can differ after preprocessing. Align chunks
    by absolute row, never by coordinates, time joins, or independent extrema.
    Memory stays bounded by two Arrow batches plus the returned point budget.
    """
    columns = list(dict.fromkeys([tcol, x, y]))
    stride = max(1, math.ceil((i1 - i0) / max_points))
    records = []
    first_finite = [None, None]
    native_finite = [0, 0]
    sampled_finite = [0, 0]
    row, last_time = i0, None
    arrays = [None, None]
    offsets = [0, 0]
    with closing(store._iter_parquet_slice(original / 'data.parquet', columns, i0, i1)) as orig, \
            closing(store._iter_parquet_slice(current / 'data.parquet', columns, i0, i1)) as saved:
        readers = [orig, saved]
        while row < i1:
            for version, reader in enumerate(readers):
                if arrays[version] is None or offsets[version] == len(arrays[version][tcol]):
                    entry = next(reader, None)
                    if entry is None or entry[0] != row or entry[1].num_rows < 1:
                        raise ValueError('Stored XY sample rows are incomplete or inconsistent.')
                    arrays[version] = {c: store._batch_float64(entry[1], c) for c in columns}
                    offsets[version] = 0
            count = min(len(arrays[v][tcol]) - offsets[v] for v in (0, 1))
            count = min(count, i1 - row)
            parts = [{c: values[offsets[v]:offsets[v] + count]
                      for c, values in arrays[v].items()} for v in (0, 1)]
            times = parts[0][tcol]
            _aligned(times, parts[1][tcol], count)
            if last_time is not None and times[0] <= last_time:
                raise ValueError('Original and processed sample times are not increasing.')
            last_time = times[-1]
            gap_mask = np.zeros(count, dtype=bool)
            for lo, hi in gaps:
                if lo < row + count and hi > row:
                    gap_mask[max(0, lo - row):min(count, hi - row)] = True
            pairs = []
            finite = []
            for version, part in enumerate(parts):
                valid = np.isfinite(part[x]) & np.isfinite(part[y]) & ~gap_mask
                finite.append(valid)
                native_finite[version] += int(valid.sum())
                pairs.append((np.where(valid, part[x], np.nan),
                              np.where(valid, part[y], np.nan)))

            def record(index):
                return (row + index, float(times[index]),
                        float(pairs[0][0][index]), float(pairs[0][1][index]),
                        float(pairs[1][0][index]), float(pairs[1][1][index]))

            for version in (0, 1):
                if first_finite[version] is None and finite[version].any():
                    first_finite[version] = record(int(np.flatnonzero(finite[version])[0]))
            sampled = np.arange((-(row - i0)) % stride, count, stride, dtype=np.int64)
            records.extend(record(int(index)) for index in sampled)
            for version in (0, 1):
                sampled_finite[version] += int(finite[version][sampled].sum())
                offsets[version] += count
            row += count
        # Keep reader lifetime inside the data lock, including empty windows.
        for version, reader in enumerate(readers):
            if ((arrays[version] is not None and offsets[version] != len(arrays[version][tcol]))
                    or next(reader, None) is not None):
                raise ValueError('Stored XY sample counts do not match the requested window.')

    # A uniform display stride can miss every finite point in a sparse source.
    # Add each affected source's first finite row to BOTH versions; replacing a
    # final ordinary stride row preserves the shared indices and point budget.
    fallback = {first_finite[v][0]: first_finite[v] for v in (0, 1)
                if not sampled_finite[v] and first_finite[v] is not None}
    if fallback:
        # Keep a version's only already-visible point when making room for the
        # other version's fallback, even when that point is the final row.
        protected = {next((entry[0] for entry in records if math.isfinite(entry[2 + v * 2])), None)
                     for v in (0, 1)}
        excess = max(0, len(records) + len(fallback) - max_points)
        if excess:
            remove = {entry[0] for entry in
                      [entry for entry in reversed(records) if entry[0] not in protected][:excess]}
            records = [entry for entry in records if entry[0] not in remove]
        records += list(fallback.values())
        records.sort(key=lambda entry: entry[0])
    times = np.array([entry[1] for entry in records], dtype=np.float64)
    result = {'stride': stride, 'indices': [entry[0] for entry in records],
              't': _json(times), 'fallback_indices': sorted(fallback), 'summary': {}}
    for version, key in enumerate(('original', 'filtered')):
        xv = np.array([entry[2 + version * 2] for entry in records], dtype=np.float64)
        yv = np.array([entry[3 + version * 2] for entry in records], dtype=np.float64)
        finite_count = int(np.count_nonzero(np.isfinite(xv) & np.isfinite(yv)))
        result[key] = {'x': _json(xv), 'y': _json(yv)}
        result['summary'][key] = {'finite_pairs': finite_count,
                                 'missing_pairs': len(records) - finite_count,
                                 'native_finite_pairs': native_finite[version],
                                 'native_missing_pairs': i1 - i0 - native_finite[version]}
    return result


@router.get('/api/tests/{name}/preprocess/compare/xy')
def compare_preprocess_xy(
    name: str,
    x: Annotated[str, Query(min_length=1, max_length=255)],
    y: Annotated[str, Query(min_length=1, max_length=255)],
    source_id: UUID,
    source_revision: Annotated[str, Query(min_length=1, max_length=128)],
    t0: Annotated[float | None, Query(allow_inf_nan=False)] = None,
    t1: Annotated[float | None, Query(allow_inf_nan=False)] = None,
    max_points: Annotated[int, Query(ge=100, le=12000)] = 6000,
):
    if t0 is not None and t1 is not None and t1 <= t0:
        raise HTTPException(400, 'Choose an end time after the start time.')
    _available(name)
    with data_read(name):
        directory, meta = _available(name)
        try:
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
            if x not in meta['columns'] or y not in meta['columns']:
                raise HTTPException(400, 'Choose X and Y parameters available in this flight.')
            fs, count = float(meta['fs_hz']), meta['n_rows']
            start = float(meta.get('t_start') or 0)
            if (not math.isfinite(fs) or fs <= 0 or type(count) is not int
                    or count < 1 or not math.isfinite(start) or not math.isfinite(start + count / fs)):
                raise ValueError('Recording sample bounds are invalid.')
            for axis in set((x, y)):
                _schema(directory, meta, axis)
                _schema(original, baseline, axis)
            i0, i1 = store.window_bounds(meta, t0, t1)
            i0, i1 = min(count, i0), min(count, i1)
            gaps = _gaps(baseline, i0, i1)
            pairs = _xy_pairs(original, directory, meta['time_column'], x, y,
                              i0, i1, max_points, gaps)
            messages = []
            if pairs['stride'] > 1:
                messages.append('XY uses a common subset of native sample rows for both versions. '
                                'Sampling can omit brief events and extrema; narrow the recording time window '
                                'for more detail. Zooming the X/Y value axes does not load additional samples.')
            if pairs['fallback_indices']:
                messages.append('The regular sample stride missed all finite pairs for a version. '
                                'Its first finite row is included in both versions within the point limit.')
            if gaps:
                messages.append('Known acquisition-gap rows are omitted from both point clouds, including previously filled values.')
            return {'source': source, 'x': x, 'y': y,
                    'mode': 'raw' if pairs['stride'] == 1 else 'sampled',
                    'n_raw': i1 - i0, 'n_sampled': len(pairs['indices']), 'i0': i0, 'i1': i1,
                    **pairs, 'range': {'start': start, 'end': start + count / fs},
                    'gaps': [{'start': start + lo / fs, 'end': start + hi / fs} for lo, hi in gaps],
                    'warnings': messages}
        except (OSError, ValueError, KeyError, TypeError, OverflowError, pl.exceptions.PolarsError):
            logger.warning("XY comparison source verification failed for '%s'", name, exc_info=True)
            raise HTTPException(409, 'The original and processed data could not be compared safely. '
                                'Check this flight\'s stored files and reload the comparison.') from None


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
