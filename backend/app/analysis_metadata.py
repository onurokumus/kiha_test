"""Small, versioned export records from locked source/executed result snapshots.

Current formula records are not an edit log. Never reconstruct lost history or
claim that file-stat observations are immutable dataset revisions.
"""
from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
import hashlib
from importlib.metadata import version
import json
import math
import platform

from fastapi import HTTPException

from . import formula

SCHEMA = 'kiha-analysis-v1'
MAX_METADATA_BYTES = 2 * 1024 * 1024


@lru_cache(maxsize=1)
def runtime():
    return {'python': platform.python_version(), **{
        name: version(name) for name in ('numpy', 'scipy', 'polars', 'pyarrow')}}


def now():
    return datetime.now(timezone.utc).isoformat()


def clean(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(item) for item in value]
    return value


def encode(value):
    try:
        data = (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + '\n').encode('utf-8')
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise HTTPException(400, f'Invalid analysis metadata: {exc}') from None
    if len(data) > MAX_METADATA_BYTES:
        raise HTTPException(400, 'Analysis metadata exceeds 2 MiB; select fewer plots or sources.')
    return data


def source_context(name, meta, columns, i0, i1, *, tp_id=None, t0=None, t1=None):
    from .store import TESTS_DIR  # late import: store also emits these snapshots
    from .components import sets
    try:
        component_sets = sets(meta)
        component_sets_status = 'normalized'
    except (ValueError, TypeError, AttributeError):
        # Metadata can outlive a damaged registry/hand-edited association.
        # Preserve the saved configuration as evidence without preventing
        # unrelated scientific plotting or export of otherwise valid samples.
        component_sets = meta.get('component_sets') if 'component_sets' in meta else [{
            'id': 'legacy', 'name': 'Set 1', 'components': meta.get('components'),
            'rpm_column': meta.get('component_rpm_column')}]
        component_sets_status = 'invalid_saved_configuration'
    selected = list(dict.fromkeys(columns))
    records = formula.refresh_missing_dependencies(
        formula.rewrite_provenance(meta.get('derived_variables')), meta['columns'])
    by_name = {record['name']: record for record in records}
    needed = set(selected)
    pending = list(selected)
    while pending:
        record = by_name.get(pending.pop(), {})
        for dependency in record.get('dependencies', []):
            if dependency not in needed:
                needed.add(dependency)
                pending.append(dependency)
    observations = {}
    for filename in ('data.parquet', 'meta.json', 'testpoints.json'):
        try:
            stat = (TESTS_DIR / name / filename).stat()
            observations[filename] = {'size_bytes': stat.st_size, 'mtime_ns': str(stat.st_mtime_ns)}
        except OSError:
            observations[filename] = None
    return clean({
        'test': name, 'test_point_id': str(tp_id) if tp_id is not None else None,
        'scope': {'i0': i0, 'i1': i1, 'row_bounds': 'half_open',
                  'requested_t0_s': t0, 'requested_t1_s': t1,
                  'resolution': 'saved_tp_rows' if tp_id is not None else 'nominal_time_window'},
        'source': {'values': 'current_stored_data_including_saved_edits',
                   'source_file': meta.get('source_file'),
                   'component_ids': meta.get('components'),
                   'component_association_revision': meta.get('components_revision', 0),
                   'component_sets': component_sets,
                   'component_sets_status': component_sets_status,
                   'component_sets_revision': meta.get('component_sets_revision', 0),
                   'edited_at': meta.get('edited_at'),
                   'time_column': meta['time_column'], 'fs_hz': meta.get('fs_hz'),
                   'n_rows': meta.get('n_rows'), 'stored_t_start_s': meta.get('t_start'),
                   'duration_s': meta.get('duration_s'),
                   'time_source': meta.get('time_source'),
                   'source_time_origin_s': meta.get('source_time_origin_s'),
                   'time_gap_ranges': meta.get('time_gap_ranges'),
                   'file_observations': observations,
                   'revision_status': 'file_stats_only_not_immutable_or_content_hash'},
        'variables': [{'column': col, 'unit': None, 'unit_status': 'not_recorded'} for col in selected],
        'equations': [record for record in records if record['name'] in needed],
        'equation_status': 'current_records_only' if 'derived_variables' in meta else 'not_recorded',
        'edit_history_status': 'not_retained; prior replacements, trims, fills and dropped equations cannot be reconstructed',
        'runtime': runtime(),
    })


def observe_rows(record, indices, times, keep):
    """Observe actual source and exported centers while the writer reads them."""
    if record is None:
        return
    i0, i1 = record['scope']['i0'], record['scope']['i1']
    requested = (indices >= i0) & (indices < i1)
    for label, mask in (('source_centers', requested), ('exported_centers', keep)):
        positions = indices[mask]
        if positions.size:
            centers = times[mask]
            info = record.setdefault(label, {'first_index': int(positions[0]),
                                              'first_time_s': clean(float(centers[0])), 'count': 0})
            info.update(last_index=int(positions[-1]), last_time_s=clean(float(centers[-1])))
            info['count'] += int(positions.size)


def document(format, plots, *, layout=None):
    return {'schema': SCHEMA, 'created_at_utc': now(), 'format': format,
            'layout': layout, 'plots': plots,
            'limitations': ['Source snapshots are sequential, not a global immutable revision.',
                            'Units and historical operations are recorded only where available.']}


def file_digest(output):
    from . import export_progress as progress
    output.seek(0)
    digest = hashlib.sha256()
    progress.update('Checking file hash')
    while chunk := output.read(256 * 1024):
        progress.checkpoint()
        digest.update(chunk)
    output.seek(0)
    return digest.hexdigest()
