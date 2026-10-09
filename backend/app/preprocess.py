"""Reversible whole-record filtering within one stable test identity.

Recipes always start from an internal original snapshot. A rollback journal
protects the last-good active data while staged artifacts are published.
"""
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import shutil
import time
from typing import Literal
from uuid import UUID

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
from .locks import test_write
from .paths import is_link_or_junction
from .status import write_status

router = APIRouter()
logger = logging.getLogger('kiha.preprocess')
ORIGINAL = '.preprocess-original'
WORK = '.preprocess-work'
ARTIFACTS = ('data.parquet', 'pyramid', 'meta.json')
SAMPLE_FIELDS = ('columns', 'time_column', 'fs_hz', 'n_rows', 'n_columns',
                 't_start', 'duration_s', 'nan_counts', 'inf_counts', 'pyramid_rows',
                 'nan_policy', 'time_gap_ranges', 'acquisition_gap_ranges', 'derived_variables')


def is_in_place(recipe) -> bool:
    return isinstance(recipe, dict) and recipe.get('version') == 2 and recipe.get('mode') == 'in_place'


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
    request_id: UUID
    source_id: UUID
    source_revision: str = Field(min_length=1, max_length=128)
    filters: list[ColumnFilter] = Field(max_length=512)


def _directory(name: str) -> Path:
    trash.validate_name(name)
    directory = store.TESTS_DIR / name
    if (is_link_or_junction(directory)
            or directory.resolve().parent != store.TESTS_DIR.resolve()):
        raise HTTPException(400, 'Invalid test path.')
    return directory


@contextmanager
def _source_access(name: str):
    """Report recoverable source-file problems without hiding them in a 500."""
    try:
        yield
    except PermissionError:
        logger.warning("Pre-processing source access denied for '%s'", name, exc_info=True)
        raise HTTPException(409, 'Pre-processing cannot access this test\'s stored files. '
                            'Ask the server administrator to check the backend service account\'s '
                            'read/write permissions for this test, then retry.') from None
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        logger.warning("Pre-processing source verification failed for '%s'", name, exc_info=True)
        raise HTTPException(409, 'Pre-processing could not verify this test\'s source identity, '
                            'metadata or stored samples. Ask the server administrator to check '
                            'this test\'s files and the backend log, then retry. '
                            'The original test has not been changed.') from None


def _ready(name: str) -> dict:
    with _source_access(name):
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
    with _source_access(name):
        snapshot = analysis_sources.sample_snapshot(_directory(name), 'ready')
    return {key: snapshot[key] for key in ('id', 'revision')} | {'name': name}


@router.get('/api/tests/{name}/preprocess')
def get_preprocess(name: str):
    _ready(name)  # fail busy promptly, before waiting for a long native writer
    with test_write(name):
        meta = _ready(name)
        with _source_access(name):
            _, baseline = original_data(_directory(name), meta)
        recipe = meta.get('preprocessing')
        return {'source': _source(name), 'meta': _sample_meta(meta, baseline),
                'preprocessing': recipe if is_in_place(recipe) else None,
                'legacy_preprocessing': recipe if recipe and not is_in_place(recipe) else None,
                'preprocessing_operation': public_operation(store.get_status(name)),
                'max_samples': MAX_FILTER_SAMPLES}


def _validate(meta: dict, filters: list[ColumnFilter]) -> None:
    if filters and not 2 <= meta['n_rows'] <= MAX_FILTER_SAMPLES:
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
    previous = _repeat(name, payload)
    if previous:
        return previous
    _ready(name)
    with test_write(name):
        previous = _repeat(name, payload)
        if previous:
            return previous
        meta = _ready(name)
        destination = _directory(name)
        with _source_access(name):
            _cleanup(destination)
            origin, baseline = original_data(destination, meta)
            _validate(baseline, payload.filters)
            if analysis_sources.read_identity(_directory(name)) is None:
                raise HTTPException(409, 'Source identity is unavailable. Close and reopen pre-processing.')
        source = _source(name)
        if source['id'] != str(payload.source_id) or source['revision'] != payload.source_revision:
            raise HTTPException(409, 'The source changed. Close and reopen pre-processing to review the current data.')
        operation = {'request_id': str(payload.request_id), 'state': 'running',
                     'request': payload.model_dump(mode='json')}
        if not payload.filters and not is_in_place(meta.get('preprocessing')):
            operation['state'] = 'completed'
            write_status(destination, 'ready', preprocessing_operation=operation)
            return _response(name)
        estimate = max((origin / 'data.parquet').stat().st_size * 3,
                       baseline['n_rows'] * len(baseline['columns']) * 16)
        estimate += baseline['n_rows'] * len(payload.filters) * 8
        if shutil.disk_usage(store.TESTS_DIR).free < estimate + UPLOAD_DISK_RESERVE_BYTES:
            raise HTTPException(507, 'Not enough free disk space to safely pre-process this test.')
        created_at = datetime.now(timezone.utc).isoformat(timespec='seconds')
        old = meta.get('preprocessing')
        recipe = {'version': 2, 'mode': 'in_place',
                  'source': old['source'] if is_in_place(old) else source,
                  'legacy_copy': bool(baseline.get('preprocessing')),
                  'filters': [entry.model_dump() for entry in payload.filters],
                  'created_at': created_at, 'method': 'whole_native_recording',
                  'warnings': [], 'original_raw_available': (destination / 'raw.csv').is_file()}
        write_status(destination, 'rebuilding', preprocessing=recipe,
                     preprocessing_operation=operation,
                     preprocessing_progress={'stage': 'Queued', 'completed_columns': 0,
                                             'total_columns': len(payload.filters)})
    background.add_task(build_in_place, name, source, recipe, operation)
    return _response(name)


def public_operation(status: dict):
    operation = status.get('preprocessing_operation')
    if not isinstance(operation, dict):
        return None
    return {key: operation[key] for key in ('request_id', 'state', 'error') if key in operation}


def _response(name):
    status = store.get_status(name)
    return {'name': name, 'status': status['status'],
            'preprocessing_operation': public_operation(status)}


def _repeat(name, payload):
    _directory(name)
    status = store.get_status(name)
    previous = status.get('preprocessing_operation')
    if not isinstance(previous, dict):
        return None
    if previous.get('request_id') != str(payload.request_id):
        return None
    if previous.get('request') != payload.model_dump(mode='json'):
        raise HTTPException(409, 'This pre-processing request ID was already used for different settings.')
    return _response(name)


def _safe_tree(path: Path):
    if is_link_or_junction(path) or (path.is_dir() and any(is_link_or_junction(p) for p in path.rglob('*'))):
        raise ValueError('Linked preprocessing artifacts are not supported.')


def original_data(directory: Path, meta: dict) -> tuple[Path, dict]:
    """Return the physical original for processing/statistics, never a catalog row."""
    if not is_in_place(meta.get('preprocessing')):
        return directory, meta
    original = directory / ORIGINAL
    _safe_tree(original)
    baseline = store._read_json(original / 'meta.json')
    if not isinstance(baseline, dict) or not (original / 'data.parquet').is_file():
        raise ValueError('The retained original is unavailable; no samples were changed.')
    return original, baseline


def _sample_meta(current, baseline):
    result = deepcopy(current)
    for key in SAMPLE_FIELDS:
        if key in baseline:
            result[key] = deepcopy(baseline[key])
        else:
            result.pop(key, None)
    return result


def _filter_column(values: np.ndarray, meta: dict, settings: dict) -> tuple[np.ndarray, list[str]]:
    """Use the plot DSP operators, preserving non-finite samples and hard gaps.

    Unlike temporary plot overlays, a saved result must never silently replace
    short valid segments with NaNs. A filter that cannot cover such a segment
    fails the entire operation with an actionable error.
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


def _discard(path: Path):
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _copy_artifact(source: Path, target: Path):
    _safe_tree(source)
    if source.is_dir():
        shutil.copytree(source, target)
    else:
        shutil.copy2(source, target)


def _retain_original(directory: Path, meta: dict):
    original = directory / ORIGINAL
    if is_in_place(meta.get('preprocessing')):
        return original_data(directory, meta)
    # An unused snapshot can remain after a failed cleanup; it must never be
    # treated as current after an ordinary Edit changed the active recording.
    _safe_tree(original)
    _discard(original)
    pending = directory / (ORIGINAL + '.tmp')
    _safe_tree(pending)
    _discard(pending)
    pending.mkdir()
    for relative in ARTIFACTS:
        source = directory / relative
        if source.exists():
            _copy_artifact(source, pending / relative)
    os.replace(pending, original)
    return original, deepcopy(meta)


def _publish(directory: Path, staging: Path):
    rollback = staging / 'rollback'
    rollback.mkdir()
    manifest = {relative: (directory / relative).exists() for relative in ARTIFACTS}
    # The manifest precedes every move. Recovery uses backup existence to
    # distinguish artifacts already moved from those still in their old place.
    store.write_json_atomic(staging / 'transaction.json', manifest)
    for relative in ARTIFACTS:
        active, new = directory / relative, staging / 'next' / relative
        if manifest[relative]:
            os.replace(active, rollback / relative)
        if new.exists():
            os.replace(new, active)


def _rollback(directory: Path):
    staging = directory / WORK
    _safe_tree(staging)
    manifest = store._read_json(staging / 'transaction.json')
    if manifest is None and not (staging / 'transaction.json').exists():
        return
    if not isinstance(manifest, dict) or set(manifest) != set(ARTIFACTS) or any(type(v) is not bool for v in manifest.values()):
        raise ValueError('Pre-processing rollback journal is invalid.')
    for relative in ARTIFACTS:
        active, old = directory / relative, staging / 'rollback' / relative
        if old.exists():
            _discard(active)
            os.replace(old, active)
        elif not manifest[relative]:
            _discard(active)


def _cleanup(directory: Path):
    for relative in (WORK, ORIGINAL + '.tmp'):
        path = directory / relative
        _safe_tree(path)
        _discard(path)
    if not is_in_place((store.get_meta(directory.name) or {}).get('preprocessing')):
        _safe_tree(directory / ORIGINAL)
        _discard(directory / ORIGINAL)


def recover_in_place(directory: Path) -> bool:
    """Recover only our marked jobs, before readers are admitted at startup."""
    status = store.get_status(directory.name)
    operation = status.get('preprocessing_operation')
    if not isinstance(operation, dict):
        return False
    interrupted = status.get('status') == 'rebuilding'
    retry_recovery = status.get('status') == 'error' and (directory / WORK / 'transaction.json').is_file()
    if interrupted or retry_recovery:
        try:
            _rollback(directory)
            message = 'Pre-processing was interrupted by a backend restart. The previous active data has been restored.'
            operation = {**operation, 'state': 'failed', 'error': message}
            write_status(directory, 'ready', preprocessing_operation=operation)
        except Exception:
            logger.exception("Cannot recover pre-processing for '%s'", directory.name)
            write_status(directory, 'error', 'Pre-processing recovery could not restore the active files. '
                         'Retained recovery files have been preserved; ask the server administrator to check the backend log.',
                         preprocessing_operation={**operation, 'state': 'failed', 'error': 'Recovery needs administrator attention.'})
            return True
    if store.get_status(directory.name).get('status') == 'ready':
        try:
            _cleanup(directory)
        except Exception:
            logger.warning("Pre-processing cleanup pending for '%s'", directory.name, exc_info=True)
    return True


def build_in_place(name: str, expected: dict, recipe: dict, operation: dict) -> None:
    directory = _directory(name)
    staging = directory / WORK
    started = time.monotonic()
    # Writers serialize native work with every reader and sample/metadata edit.
    # Busy status is published during reservation so other writes reject early.
    with test_write(name):
        try:
            if _source(name) != expected:
                raise ValueError('Source changed before processing began. Reload pre-processing to review the current data.')
            current = store.get_meta(name)
            origin, baseline = _retain_original(directory, current)
            _safe_tree(staging)
            _discard(staging)
            staging.mkdir()
            next_dir = staging / 'next'
            next_dir.mkdir()
            staged = {}
            warnings = []
            total = len(recipe['filters'])

            def report(stage, completed):
                write_status(directory, 'rebuilding', preprocessing=recipe,
                             preprocessing_operation=operation,
                             preprocessing_progress={'stage': stage, 'completed_columns': completed,
                                                     'total_columns': total})

            for index, entry in enumerate(recipe['filters']):
                column = entry['column']
                report(f'Filtering {column}', index)
                frame = pl.read_parquet(origin / 'data.parquet', columns=[column])
                try:
                    result, notes = _filter_column(frame[column].to_numpy().astype(np.float64),
                                                   baseline, entry['filter'])
                except ValueError as exc:
                    raise ValueError(f'{column}: {exc}') from None
                path = staging / f'column-{index}.parquet'
                array = pa.array(result, mask=frame[column].is_null().to_numpy())
                pq.write_table(pa.table({column: array}), path, row_group_size=ROW_GROUP_SIZE,
                               compression='zstd')
                staged[column] = path
                warnings.extend(f'{column}: {note}' for note in notes)
                del frame, result, array

            saved = _sample_meta(current, baseline)
            if total:
                report('Saving complete recording', total)
                parquet = next_dir / 'data.parquet'
                _merge_columns(origin / 'data.parquet', staged, parquet)
                report('Building plot data', total)
                inf_counts = {}
                nan_counts, levels = build_pyramid(parquet, next_dir / 'pyramid',
                                                   baseline['time_column'], inf_counts=inf_counts)
                saved.update(nan_counts={c: n for c, n in nan_counts.items() if n},
                             inf_counts={c: n for c, n in inf_counts.items() if n}, pyramid_rows=levels)
                saved['preprocessing'] = {**recipe, 'warnings': warnings,
                    'completed_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                    'seconds': round(time.monotonic() - started, 2),
                    'boundary_policy': 'whole continuous regions; sparse missing samples temporarily interpolated and restored; known acquisition gaps never crossed and their stored values preserved',
                    'derived_variables_policy': 'Stored equation outputs are filtered only when selected; equations are not recomputed after filtering their dependencies.',
                    'runtime': {'numpy': np.__version__, 'scipy': scipy.__version__}}
            else:
                report('Restoring original data', 0)
                for relative in ('data.parquet', 'pyramid'):
                    if (origin / relative).exists():
                        _copy_artifact(origin / relative, next_dir / relative)
                if 'preprocessing' in baseline:
                    saved['preprocessing'] = deepcopy(baseline['preprocessing'])
                else:
                    saved.pop('preprocessing', None)
            store.write_json_atomic(next_dir / 'meta.json', saved)
            _publish(directory, staging)
            write_status(directory, 'ready', preprocessing_operation={**operation, 'state': 'completed'})
        except Exception as exc:
            logger.exception("Pre-processing '%s' failed", name)
            try:
                _rollback(directory)
                message = f'Pre-processing failed: {exc}. The previous active data is unchanged.'
                write_status(directory, 'ready', preprocessing_operation={**operation, 'state': 'failed', 'error': message})
            except Exception:
                logger.exception("Pre-processing rollback failed for '%s'", name)
                write_status(directory, 'error', 'Pre-processing recovery could not restore the active files. '
                             'Recovery files have been preserved; ask the server administrator to check the backend log.',
                             preprocessing_operation={**operation, 'state': 'failed', 'error': 'Recovery needs administrator attention.'})
                return
        try:
            _cleanup(directory)
        except Exception:
            logger.warning("Pre-processing cleanup pending for '%s'", name, exc_info=True)
