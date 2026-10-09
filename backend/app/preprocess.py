"""Saved whole-record filtering into an independent, non-destructive test copy.

Only the destination is ever written. Each selected column is processed at its
native sample rate, then staged separately so RAM scales with one full column,
not the complete recording. No plot windows/reduced samples enter this path.
The destination's ready status is the commit marker; failed/interrupted copies
remain manageable error rows and can never replace their source.
"""
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import shutil
import time
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException
import numpy as np
import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import BaseModel, ConfigDict, Field
import scipy

from . import analysis_sources, dsp, store, trash
from .config import MAX_FILTER_SAMPLES, ROW_GROUP_SIZE, UPLOAD_DISK_RESERVE_BYTES
from .ingest import build_pyramid
from .locks import catalog_write, data_read, test_read, test_write
from .paths import is_link_or_junction
from .status import write_status

router = APIRouter()
logger = logging.getLogger('kiha.preprocess')


class FilterSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    kind: Literal['lowpass', 'highpass', 'bandpass', 'bandstop',
                  'moving_avg', 'detrend', 'despike']
    order: int = Field(default=4, ge=1, le=10, strict=True)
    f1: float | None = Field(default=None, gt=0)
    f2: float | None = Field(default=None, gt=0)
    window_s: float | None = Field(default=None, gt=0)
    max_spike_s: float = Field(default=dsp.DEFAULT_MAX_SPIKE_S, gt=0)
    threshold: float = Field(default=dsp.DEFAULT_DESPIKE_THRESHOLD, gt=0)
    abs_floor: float = Field(default=dsp.DEFAULT_DESPIKE_ABS_FLOOR, ge=0)
    replacement: Literal['linear', 'median'] = 'linear'


class ColumnFilter(BaseModel):
    model_config = ConfigDict(extra='forbid')
    column: str = Field(min_length=1, max_length=255)
    filter: FilterSettings


class PreprocessRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=200)
    source_id: UUID
    source_revision: str = Field(min_length=1, max_length=128)
    filters: list[ColumnFilter] = Field(min_length=1, max_length=512)


def _directory(name: str) -> Path:
    trash.validate_name(name)
    directory = store.TESTS_DIR / name
    if (is_link_or_junction(directory)
            or directory.resolve().parent != store.TESTS_DIR.resolve()):
        raise HTTPException(400, 'Invalid test path.')
    return directory


def _ready(name: str) -> dict:
    directory = _directory(name)
    if not directory.is_dir():
        raise HTTPException(404, f"Test '{name}' was not found.")
    if store.get_status(name).get('status') != 'ready':
        raise HTTPException(409, f"Test '{name}' is not ready.")
    for relative in ('meta.json', 'data.parquet', 'testpoints.json', 'raw.csv'):
        if is_link_or_junction(directory / relative):
            raise HTTPException(409, 'Linked test files cannot be pre-processed.')
    meta = store.get_meta(name)
    if not isinstance(meta, dict):
        raise HTTPException(409, 'Test metadata is unavailable.')
    return meta


def _source(name: str) -> dict:
    # Caller holds the test writer when migrating a legacy identity. Workers
    # only call this after the reservation already established that identity.
    snapshot = analysis_sources._snapshot(_directory(name), 'ready')
    return {key: snapshot[key] for key in ('id', 'revision')} | {'name': name}


@router.get('/api/tests/{name}/preprocess')
def get_preprocess(name: str):
    _ready(name)  # fail busy promptly, before waiting for a long native writer
    with test_write(name):
        meta = _ready(name)
        return {'source': _source(name), 'meta': meta,
                'preprocessing': meta.get('preprocessing'),
                'max_samples': MAX_FILTER_SAMPLES}


def _validate(meta: dict, filters: list[ColumnFilter]) -> None:
    if meta.get('preprocessing') is not None:
        raise HTTPException(409, 'This is a pre-processed copy. Start from the original test to create another version.')
    if not 2 <= meta['n_rows'] <= MAX_FILTER_SAMPLES:
        raise HTTPException(400, f'Whole-record pre-processing supports 2–{MAX_FILTER_SAMPLES:,} samples per test; no samples were changed.')
    columns = [entry.column for entry in filters]
    if len(columns) != len(set(columns)):
        raise HTTPException(400, 'Choose only one filter per parameter.')
    for entry in filters:
        if entry.column == meta['time_column']:
            raise HTTPException(400, 'The time column must stay unchanged.')
        if entry.column not in meta['columns']:
            raise HTTPException(400, f"Unknown parameter '{entry.column}'.")
        f = entry.filter
        fs = float(meta['fs_hz'])
        try:
            if f.kind in {'lowpass', 'highpass', 'bandpass', 'bandstop'}:
                dsp._make_sos(f.kind, f.order, f.f1, f.f2, fs)
            elif f.kind == 'moving_avg':
                if f.window_s is None or max(1, round(f.window_s * fs)) >= meta['n_rows']:
                    raise ValueError('moving average needs a positive window shorter than the recording')
            elif f.kind == 'despike':
                window, *_ = dsp._despike_sample_parameters(
                    fs, f.window_s, f.max_spike_s, f.threshold, f.abs_floor)
                if window >= meta['n_rows']:
                    raise ValueError('despike window must be shorter than the recording')
        except (ValueError, OverflowError) as exc:
            raise HTTPException(400, f'{entry.column}: {exc}') from None


@router.post('/api/tests/{name}/preprocess', status_code=202)
def create_preprocess(name: str, payload: PreprocessRequest,
                      background: BackgroundTasks):
    _ready(name)
    destination = _directory(payload.name)
    # Catalog reservation and guards are short. Expensive native work runs only
    # after the response, under source read + destination write locks.
    with catalog_write():
        if destination.exists():
            raise HTTPException(409, f"Test '{payload.name}' already exists. Choose a different output name.")
        return _reserve_copy(name, payload, background, destination)


def _reserve_copy(name: str, payload: PreprocessRequest,
                  background: BackgroundTasks, destination: Path):
    """Caller holds catalog_write and has rejected occupied destinations.

    The source uses a reader so another independent copy can be reserved while
    a worker reads it. Both reservation and worker acquire source before output;
    no long source writer is acquired while blocking the whole catalog.
    """
    with test_read(name, check=lambda: _ready(name)), test_write(payload.name):
        meta = _ready(name)
        _validate(meta, payload.filters)
        if analysis_sources.read_identity(_directory(name)) is None:
            raise HTTPException(409, 'Source identity is unavailable. Close and reopen pre-processing.')
        source = _source(name)
        if source['id'] != str(payload.source_id) or source['revision'] != payload.source_revision:
            raise HTTPException(409, 'The source changed. Close and reopen pre-processing to review the current data.')
        if destination.exists():
            raise HTTPException(409, f"Test '{payload.name}' already exists. Choose a different output name.")
        origin = _directory(name)
        raw = origin / 'raw.csv'
        # Advisory reservation includes raw copy, sample data, staged columns,
        # and pyramid headroom. The worker still handles later disk failures.
        estimate = (raw.stat().st_size if raw.is_file() else 0)
        estimate += max((origin / 'data.parquet').stat().st_size * 2,
                        meta['n_rows'] * len(meta['columns']) * 8)
        estimate += meta['n_rows'] * len(payload.filters) * 8
        if shutil.disk_usage(store.TESTS_DIR).free < estimate + UPLOAD_DISK_RESERVE_BYTES:
            raise HTTPException(507, 'Not enough free disk space for an independent pre-processed copy.')
        created_at = datetime.now(timezone.utc).isoformat(timespec='seconds')
        recipe = {'version': 1, 'source': source,
                  'filters': [entry.model_dump() for entry in payload.filters],
                  'created_at': created_at, 'method': 'whole_native_recording',
                  'warnings': [], 'original_raw_available': raw.is_file()}
        destination.mkdir()
        try:
            store.write_json_atomic(destination / analysis_sources.IDENTITY_FILE,
                                    {'version': 1, 'id': str(uuid4())})
            write_status(destination, 'rebuilding', preprocessing=recipe,
                         preprocessing_progress={'stage': 'Queued', 'completed_columns': 0,
                                                 'total_columns': len(payload.filters)})
        except BaseException:
            shutil.rmtree(destination)
            raise
    background.add_task(build_copy, name, payload.name, recipe)
    return {'name': payload.name, 'status': 'rebuilding'}


def _filter_column(values: np.ndarray, meta: dict, settings: dict) -> tuple[np.ndarray, list[str]]:
    """Use the plot DSP operators, preserving non-finite samples and hard gaps.

    Unlike temporary plot overlays, a saved copy must never silently replace
    short valid segments with NaNs. A filter that cannot cover such a segment
    fails the entire copy with an actionable error.
    """
    output = values.copy()
    settings = dict(settings)
    kind = settings.pop('kind')
    warnings = []
    # Durable preprocessing also respects acquisition history retained after
    # Edit filled a dropout. Those fabricated values stay as stored, but never
    # become filter state/interpolation shoulders for observed regions.
    ranges = []
    for key in ('time_gap_ranges', 'acquisition_gap_ranges'):
        saved = meta.get(key)
        if isinstance(saved, list):
            ranges.extend(pair for pair in saved if isinstance(pair, list)
                          and len(pair) == 2 and all(type(v) is int for v in pair))
    gaps = dsp._known_gap_slices({'time_gap_ranges': sorted(ranges)}, 0, len(values))
    start = 0
    missing = int(np.count_nonzero(~np.isfinite(values)))
    if missing:
        warnings.append(f'{missing:,} missing/non-finite samples were preserved.')
    if gaps:
        warnings.append(f'{len(gaps)} acquisition gap(s) split filtering into independent regions.')
    if meta.get('acquisition_gap_ranges') is None and meta.get('nan_policy', 'keep_gaps') != 'keep_gaps':
        warnings.append('Legacy acquisition gap history is unavailable; only retained gaps can separate filtering.')
    applied = 0
    for end, next_start in gaps + [(len(values), len(values))]:
        if end > start:
            segment = values[start:end]
            clean, mask = dsp._interp_nan(segment)
            if np.count_nonzero(mask) < 2 and mask.any():
                raise ValueError(f'rows {start}–{end} contain fewer than two finite samples; this region cannot be filtered')
            if clean is not None:
                try:
                    result = dsp._apply(kind, clean, float(meta['fs_hz']), **settings)
                except ValueError as exc:
                    raise ValueError(f'rows {start}–{end}: {exc}; choose a shorter window or lower filter order') from None
                if not np.isfinite(result[mask]).all():
                    raise ValueError('filter produced non-finite values from finite samples')
                result[~mask] = segment[~mask]
                output[start:end] = result
                applied += 1
        start = max(start, next_start)
    if not applied:
        raise ValueError('fewer than two finite samples in every continuous region')
    return output, warnings


def _merge_columns(source: Path, staged: dict[str, Path], target: Path) -> None:
    with ExitStack() as stack:
        original = stack.enter_context(pq.ParquetFile(source))
        readers = {col: iter(stack.enter_context(pq.ParquetFile(path)).iter_batches(
            batch_size=ROW_GROUP_SIZE)) for col, path in staged.items()}
        writer = None
        for batch in original.iter_batches(batch_size=ROW_GROUP_SIZE):
            table = pa.Table.from_batches([batch])
            for col, reader in readers.items():
                filtered = next(reader)
                if filtered.num_rows != batch.num_rows:
                    raise ValueError('Pre-processing row alignment failed.')
                table = table.set_column(table.schema.get_field_index(col), col, filtered.column(0))
            if writer is None:
                writer = stack.enter_context(pq.ParquetWriter(target, table.schema,
                                                             compression='zstd'))
            writer.write_table(table, row_group_size=ROW_GROUP_SIZE)
        for reader in readers.values():
            if next(reader, None) is not None:
                raise ValueError('Pre-processing output has extra rows.')


def build_copy(source_name: str, output_name: str, recipe: dict) -> None:
    destination = _directory(output_name)
    staging = destination / '.preprocess'
    started = time.monotonic()
    # The source lock is always acquired before the native slot. Busy output
    # names reject rename/delete/edit immediately, so no inverse lock order.
    with data_read(source_name), test_write(output_name):
        try:
            meta = _ready(source_name)
            source = _source(source_name)
            if source != recipe['source']:
                raise ValueError('Source changed before processing began. Create a new copy from the current original.')
            if meta.get('preprocessing') is not None:
                raise ValueError('Pre-processing must start from an original test.')
            origin = _directory(source_name)
            staging.mkdir()
            staged = {}
            warnings = []
            total = len(recipe['filters'])

            def report(stage, completed):
                write_status(destination, 'rebuilding', preprocessing=recipe,
                             preprocessing_progress={'stage': stage, 'completed_columns': completed,
                                                     'total_columns': total})

            for index, entry in enumerate(recipe['filters']):
                column = entry['column']
                report(f'Filtering {column}', index)
                frame = pl.read_parquet(origin / 'data.parquet', columns=[column])
                try:
                    result, notes = _filter_column(frame[column].to_numpy().astype(np.float64),
                                                   meta, entry['filter'])
                except ValueError as exc:
                    raise ValueError(f'{column}: {exc}') from None
                path = staging / f'column-{index}.parquet'
                # Keep original Arrow nulls separate from IEEE NaN/Inf.
                array = pa.array(result, mask=frame[column].is_null().to_numpy())
                pq.write_table(pa.table({column: array}), path, row_group_size=ROW_GROUP_SIZE,
                               compression='zstd')
                staged[column] = path
                warnings.extend(f'{column}: {note}' for note in notes)
                del frame, result, array

            report('Saving complete recording', total)
            parquet = staging / 'data.parquet'
            _merge_columns(origin / 'data.parquet', staged, parquet)
            report('Building plot data', total)
            inf_counts = {}
            nan_counts, levels = build_pyramid(parquet, staging / 'pyramid',
                                               meta['time_column'], inf_counts=inf_counts)
            report('Preserving original CSV', total)
            if recipe['original_raw_available']:
                shutil.copy2(origin / 'raw.csv', staging / 'raw.csv')
            saved = deepcopy(meta)
            saved.update(name=output_name, created_at=recipe['created_at'],
                         nan_counts={c: n for c, n in nan_counts.items() if n},
                         inf_counts={c: n for c, n in inf_counts.items() if n},
                         pyramid_rows=levels)
            recipe = {**recipe, 'warnings': warnings,
                      'completed_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                      'seconds': round(time.monotonic() - started, 2),
                      'boundary_policy': 'whole continuous regions; sparse missing samples temporarily interpolated and restored; known acquisition gaps never crossed and their stored values preserved',
                      'derived_variables_policy': 'Stored equation outputs are filtered only when selected; equations are not recomputed after filtering their dependencies.',
                      'runtime': {'numpy': np.__version__, 'scipy': scipy.__version__}}
            saved['preprocessing'] = recipe
            points = deepcopy(store.read_testpoints(source_name))
            points['test'] = output_name
            # Publish all artifacts while status is rebuilding. Ready comes
            # last, after no staged or partial files can be served as a test.
            os.replace(parquet, destination / 'data.parquet')
            os.replace(staging / 'pyramid', destination / 'pyramid')
            if recipe['original_raw_available']:
                os.replace(staging / 'raw.csv', destination / 'raw.csv')
            store.write_json_atomic(destination / 'testpoints.json', points)
            store.write_json_atomic(destination / 'meta.json', saved)
            shutil.rmtree(staging)
            write_status(destination, 'ready')
        except Exception as exc:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            write_status(destination, 'error', f'Pre-processing failed: {exc}. The original test is unchanged.',
                         preprocessing=recipe)
            logger.exception("Pre-processing '%s' from '%s' failed", output_name, source_name)
