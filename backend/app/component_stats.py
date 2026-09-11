"""Current-library component use, never a lifetime ledger.

Full-resolution, bounded Parquet batches. Each eligible row represents 1/fs s;
no interpolation, TP overlap, plot filters or elapsed dropout time is integrated.
The bounded cache contains numerical source summaries only. Associations and
active membership are read anew on every request. Lock order: catalog -> test
read -> native slot; cache/registry locks never acquire a test/catalog lock.
"""
from collections import OrderedDict
from datetime import datetime, timezone
import json
import math
from threading import Lock

import numpy as np
import pyarrow.parquet as pq
from fastapi import HTTPException

from . import analysis_sources, components, store
from .locks import catalog_read, data_read
from .paths import is_link_or_junction

METHOD = 'component-usage-v1'
BATCH_SIZE = 65536
MAX_CACHE = 256
RANGES = [0, 1000, 3000, 6000, 10000]
_cache = OrderedDict()
_cache_lock = Lock()


def apply_rpm(meta, column, expected_revision):
    revision = meta.get('component_rpm_revision', 0)
    if expected_revision is None or expected_revision != revision:
        raise HTTPException(409, 'Component RPM selection changed. Reload saved metadata before saving your draft again.')
    if column is not None and (column == meta.get('time_column') or column not in meta.get('columns', [])):
        raise HTTPException(422, 'Select an existing non-time column containing revolutions per minute, or leave RPM unassigned.')
    if column != meta.get('component_rpm_column'):
        meta['component_rpm_column'] = column
        meta['component_rpm_revision'] = revision + 1


def _empty():
    return dict(running_seconds=0., stopped_seconds=0., missing_rpm_seconds=0.,
                gap_seconds=0., running_samples=0, stopped_samples=0,
                missing_rpm_samples=0, gap_samples=0, mean_rpm=None,
                min_rpm=None, max_rpm=None, sd_rpm=None, _variance=0.,
                ranges_seconds=[0.] * len(RANGES))


def _merge(target, source):
    old, weight = target['running_seconds'], source['running_seconds']
    total = old + weight
    if weight:
        if old:
            fraction = weight / total
            delta = source['mean_rpm'] - target['mean_rpm']
            target['_variance'] = ((1 - fraction) * target['_variance'] + fraction * source['_variance']
                                   + (delta * math.sqrt(fraction * (1 - fraction))) ** 2)
            target['mean_rpm'] += fraction * delta
            target['min_rpm'] = min(target['min_rpm'], source['min_rpm'])
            target['max_rpm'] = max(target['max_rpm'], source['max_rpm'])
        else:
            for key in ('mean_rpm', 'min_rpm', 'max_rpm', '_variance'):
                target[key] = source[key]
        target['sd_rpm'] = math.sqrt(target['_variance'])
    for key in ('running_seconds', 'stopped_seconds', 'missing_rpm_seconds', 'gap_seconds',
                'running_samples', 'stopped_samples', 'missing_rpm_samples', 'gap_samples'):
        target[key] += source[key]
    target['ranges_seconds'] = [a + b for a, b in zip(target['ranges_seconds'], source['ranges_seconds'])]


def _gap_ranges(meta, n):
    # Old fill operations deliberately cleared time_gap_ranges. Do not silently
    # treat their fabricated rows as observed operation when history is lost.
    value = meta.get('acquisition_gap_ranges', meta.get('time_gap_ranges'))
    if (('acquisition_gap_ranges' in meta and value is None) or
            ('acquisition_gap_ranges' not in meta and meta.get('nan_policy', 'keep_gaps') != 'keep_gaps')):
        raise ValueError('Acquisition-gap history is unavailable after an older fill edit. Reimport the original CSV to measure component use.')
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError('Acquisition-gap metadata is invalid.')
    merged = []
    for pair in sorted(value):
        if (not isinstance(pair, list) or len(pair) != 2 or
                not all(type(v) is int for v in pair) or not 0 <= pair[0] < pair[1] <= n):
            raise ValueError('Acquisition-gap metadata is invalid.')
        if merged and pair[0] <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], pair[1])
        else:
            merged.append(pair.copy())
    return merged


def _calculate(path, meta):
    fs = float(meta['fs_hz'])
    if not math.isfinite(fs) or fs <= 0 or not math.isfinite(1 / fs):
        raise ValueError('A finite positive sample rate is required.')
    tcol, rpm = meta['time_column'], meta['component_rpm_column']
    result = _empty()
    previous = None
    first = None
    offset = 0
    extra_gap_seconds = 0.
    with pq.ParquetFile(path) as parquet:
        n = parquet.metadata.num_rows
        if n != meta['n_rows'] or n == 0:
            raise ValueError('Sample count does not match the current test metadata.')
        gaps = _gap_ranges(meta, n)
        for batch in parquet.iter_batches(batch_size=BATCH_SIZE, columns=[tcol, rpm]):
            time = np.asarray(batch.column(tcol).to_numpy(zero_copy_only=False), dtype=float)
            values = np.asarray(batch.column(rpm).to_numpy(zero_copy_only=False), dtype=float)
            delta = np.diff(time if previous is None else np.r_[previous, time])
            if not np.isfinite(time).all() or not np.isfinite(delta).all() or np.any(delta <= 0):
                raise ValueError('Timestamps must be finite and strictly increasing; runtime is unavailable.')
            # A nonuniform legacy axis may omit entire rows. Sample exposure
            # never counts that elapsed gap. Report it separately for coverage.
            extra_gap_seconds += float(np.sum(delta[delta > 1.5 / fs] - 1 / fs))
            if first is None:
                first = float(time[0])
            previous = float(time[-1])
            gap = np.zeros(len(values), dtype=bool)
            for lo, hi in gaps:
                if hi <= offset:
                    continue
                if lo >= offset + len(values):
                    break
                gap[max(0, lo - offset):min(len(values), hi - offset)] = True
            finite = np.isfinite(values)
            running = values[~gap & finite & (values > 0)]
            part = _empty()
            part['gap_samples'] = int(gap.sum())
            part['missing_rpm_samples'] = int((~gap & ~finite).sum())
            part['stopped_samples'] = int((~gap & finite & (values <= 0)).sum())
            part['running_samples'] = len(running)
            for label in ('gap', 'missing_rpm', 'stopped', 'running'):
                part[label + '_seconds'] = part[label + '_samples'] / fs
            if len(running):
                with np.errstate(over='raise', invalid='raise', divide='raise'):
                    part['mean_rpm'] = float(np.mean(running))
                    part['_variance'] = float(np.var(running, ddof=0))
                part['min_rpm'], part['max_rpm'] = float(running.min()), float(running.max())
                part['ranges_seconds'] = (np.histogram(running, bins=[*RANGES, np.inf])[0] / fs).tolist()
            _merge(result, part)
            offset += len(values)
    result['gap_seconds'] += extra_gap_seconds
    result.update(fs_hz=fs, n_rows=n, first_time_s=first, last_time_s=previous,
                  unrepresented_gap_seconds=extra_gap_seconds)
    # Fail a source rather than serialize Infinity or silently cap extreme data.
    json.dumps(result, allow_nan=False)
    return result


def _summary(directory, meta):
    path = directory / 'data.parquet'
    if is_link_or_junction(path):
        raise ValueError('Sample data is a link; component use is unavailable.')
    stat = path.stat()
    key = json.dumps([str(path.resolve()), stat.st_size, stat.st_mtime_ns,
                      stat.st_ctime_ns, stat.st_ino, METHOD, {k: [k in meta, meta.get(k)] for k in
                      ('fs_hz', 'n_rows', 'time_column', 'component_rpm_column',
                       'nan_policy', 'time_gap_ranges', 'acquisition_gap_ranges')}], sort_keys=True, allow_nan=False)
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    value = _calculate(path, meta)
    with _cache_lock:
        _cache[key] = value
        _cache.move_to_end(key)
        while len(_cache) > MAX_CACHE:
            _cache.popitem(last=False)
    return value


def statistics():
    sources = []
    # Hold catalog membership stable against rename/trash/restore during the
    # scan. Each test is read atomically; this is not a global historical snapshot.
    with catalog_read():
        registry = components.list_components()['components']
        known = {item['id']: item['kind'] for item in registry}
        for info in store.list_tests():
            name = info['name']
            row = dict(name=name, status=info['status'], component_ids={},
                       rpm_column=None, warnings=[], issue=None, summary=None, source_id=None)
            sources.append(row)
            try:
                row['component_ids'] = components.ids(info.get('components'))
            except (ValueError, TypeError):
                row['issue'] = 'Component associations are invalid; repair this test metadata before counting it.'
                continue
            if info['status'] != 'ready':
                row['issue'] = f"Test is {info['status']}; it does not contribute."
                continue
            try:
                directory = store.TESTS_DIR / name
                if is_link_or_junction(directory) or directory.resolve().parent != store.TESTS_DIR.resolve():
                    raise ValueError('Test directory is a link; component use is unavailable.')
                with data_read(name):
                    if store.get_status(name).get('status') != 'ready':
                        raise ValueError('Test became busy; refresh after processing completes.')
                    metadata_path = directory / 'meta.json'
                    if is_link_or_junction(metadata_path):
                        raise ValueError('Test metadata is a link; component use is unavailable.')
                    meta = store.get_meta(name)
                    if not isinstance(meta, dict):
                        raise ValueError('Current test metadata is unavailable.')
                    row['component_ids'] = components.ids(meta.get('components'))
                    row['association_revision'] = meta.get('components_revision', 0)
                    row['source_id'] = analysis_sources.read_identity(directory)
                    rpm_column = meta.get('component_rpm_column')
                    if rpm_column is not None and not isinstance(rpm_column, str):
                        raise ValueError('The component RPM column reference is invalid.')
                    row['rpm_column'] = rpm_column
                    row['rpm_revision'] = meta.get('component_rpm_revision', 0)
                    row['time_source'] = meta.get('time_source') if meta.get('time_source') in ('measured', 'generated') else 'unknown'
                    if not row['source_id']:
                        row['warnings'].append('Legacy test has no durable dataset ID; this active folder is counted once.')
                    if row['time_source'] != 'measured':
                        row['warnings'].append('Timing is generated or legacy: source acquisition gaps cannot be inferred.')
                    if meta.get('nan_policy', 'keep_gaps') != 'keep_gaps':
                        row['warnings'].append('Uses current edited RPM values, including filled values outside acquisition gaps.')
                    if any(cid and known.get(cid) != kind for kind, cid in row['component_ids'].items()):
                        row['warnings'].append('An assigned component is unavailable; its contribution is not reassigned.')
                    if not any(row['component_ids'].values()):
                        row['issue'] = 'No components assigned.'
                    elif not row['rpm_column']:
                        row['issue'] = 'Select the component RPM column in Edit to include this test.'
                    elif row['rpm_column'] == meta['time_column'] or row['rpm_column'] not in meta['columns']:
                        row['issue'] = 'The selected RPM column is unavailable. Choose a current RPM column in Edit.'
                    else:
                        row['summary'] = _summary(directory, meta)
            except (OSError, ValueError, KeyError, TypeError, OverflowError, FloatingPointError) as error:
                row['issue'] = f'Component use unavailable: {error}'
                row['summary'] = None
    counts = {}
    for row in sources:
        if row['source_id']:
            counts[row['source_id']] = counts.get(row['source_id'], 0) + 1
    for row in sources:
        if counts.get(row['source_id'], 0) > 1:
            row.update(issue='Duplicate dataset identity; resolve the copied test before counting either source.', summary=None)
    totals = []
    for item in registry:
        assigned = [row for row in sources if row['component_ids'].get(item['kind']) == item['id']]
        total = _empty()
        for row in assigned:
            if row['summary']:
                _merge(total, row['summary'])
        totals.append({**item, 'assigned_tests': len(assigned),
                       'included_tests': sum(row['summary'] is not None for row in assigned),
                       'summary': total})
    # Internal variance is needed only by the stable batch/source merge.
    def public(summary):
        return {k: v for k, v in summary.items() if not k.startswith('_')}
    for item in totals:
        item['summary'] = public(item['summary'])
    for row in sources:
        if row['summary']:
            row['summary'] = public(row['summary'])
    return dict(version=1, method=METHOD, policy='active-tests-positive-rpm',
                generated_at=datetime.now(timezone.utc).isoformat(),
                rpm_range_lower_bounds=RANGES, components=totals, sources=sources)
