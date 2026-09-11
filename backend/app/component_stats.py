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

METHOD = 'component-usage-v2'
BATCH_SIZE = 65536
MAX_CACHE = 256
RANGES = [0, 1000, 3000, 6000, 10000]
_cache = OrderedDict()
_cache_lock = Lock()
METRICS = {'motor_temperature': 'C', 'power': 'W'}
BINDING_FIELDS = ('motor_temperature_column', 'motor_temperature_unit', 'power_column', 'power_unit')


def apply_rpm(meta, column, expected_revision):
    revision = meta.get('component_rpm_revision', 0)
    if expected_revision is None or expected_revision != revision:
        raise HTTPException(409, 'Component RPM selection changed. Reload saved metadata before saving your draft again.')
    if column is not None and (column == meta.get('time_column') or column not in meta.get('columns', [])):
        raise HTTPException(422, 'Select an existing non-time column containing revolutions per minute, or leave RPM unassigned.')
    if column != meta.get('component_rpm_column'):
        meta['component_rpm_column'] = column
        meta['component_rpm_revision'] = revision + 1
        meta['component_sets_revision'] = meta.get('component_sets_revision', 0) + 1


def _empty_metric(unit):
    return dict(unit=unit, samples=0, seconds=0., missing_samples=0,
                missing_seconds=0., mean=None, sd=None, min=None, max=None,
                _variance=0.)


def _empty():
    return dict(running_seconds=0., stopped_seconds=0., missing_rpm_seconds=0.,
                gap_seconds=0., running_samples=0, stopped_samples=0,
                missing_rpm_samples=0, gap_samples=0, mean_rpm=None,
                min_rpm=None, max_rpm=None, sd_rpm=None, _variance=0.,
                ranges_seconds=[0.] * len(RANGES),
                **{name: _empty_metric(unit) for name, unit in METRICS.items()})


def _merge_metric(target, source):
    old, weight = target['seconds'], source['seconds']
    total = old + weight
    if weight:
        if old:
            fraction = weight / total
            delta = source['mean'] - target['mean']
            target['_variance'] = ((1 - fraction) * target['_variance'] + fraction * source['_variance']
                                   + (delta * math.sqrt(fraction * (1 - fraction))) ** 2)
            target['mean'] += fraction * delta
            target['min'] = min(target['min'], source['min'])
            target['max'] = max(target['max'], source['max'])
        else:
            for key in ('mean', 'min', 'max', '_variance'):
                target[key] = source[key]
        target['sd'] = math.sqrt(target['_variance'])
    for key in ('samples', 'seconds', 'missing_samples', 'missing_seconds'):
        target[key] += source[key]
    json.dumps(target, allow_nan=False)


def _merge(target, source, *, motor_temperature=True):
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
    for name, unit in METRICS.items():
        if name == 'motor_temperature' and not motor_temperature:
            continue
        metric = target[name]
        try:
            if metric.get('_invalid') or source[name].get('_invalid'):
                raise ValueError('Numerical telemetry summary is unavailable.')
            _merge_metric(metric, source[name])
        except (ValueError, OverflowError, FloatingPointError):
            # An extreme optional channel must not destroy valid RPM runtime.
            target[name] = {**_empty_metric(unit), '_invalid': True,
                            'missing_samples': target['running_samples'],
                            'missing_seconds': target['running_seconds']}


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
    warnings = []
    with pq.ParquetFile(path) as parquet:
        n = parquet.metadata.num_rows
        if n != meta['n_rows'] or n == 0:
            raise ValueError('Sample count does not match the current test metadata.')
        gaps = _gap_ranges(meta, n)
        if rpm not in parquet.schema_arrow.names or tcol not in parquet.schema_arrow.names:
            raise ValueError('The selected RPM or time column is unavailable in sample data.')
        available = {}
        for metric in METRICS:
            column = meta.get(metric + '_column')
            if column and (column == tcol or column not in meta['columns'] or column not in parquet.schema_arrow.names):
                warnings.append(f'The selected {metric.replace("_", " ")} column is unavailable; its telemetry is excluded.')
                column = None
            available[metric] = column
        columns = list(dict.fromkeys([tcol, rpm, *(column for column in available.values() if column)]))
        for batch in parquet.iter_batches(batch_size=BATCH_SIZE, columns=columns):
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
            eligible = ~gap & finite & (values > 0)
            running = values[eligible]
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
            for metric, column in available.items():
                telemetry = part[metric]
                telemetry['missing_samples'] = len(running)
                telemetry['missing_seconds'] = len(running) / fs
                if not column or result[metric].get('_invalid'):
                    continue
                try:
                    observed = np.asarray(batch.column(column).to_numpy(zero_copy_only=False))[eligible]
                    observed = np.asarray(observed, dtype=float)
                    # Telemetry has its own finite-value mask. Stopped/missing
                    # RPM and acquisition gaps never enter either metric.
                    observed = observed[np.isfinite(observed)]
                    with np.errstate(over='raise', invalid='raise', divide='raise'):
                        if metric == 'motor_temperature':
                            unit = meta.get('motor_temperature_unit', 'C')
                            if unit == 'F':
                                observed = (observed - 32.) / 1.8
                            elif unit == 'K':
                                observed = observed - 273.15
                            elif unit != 'C':
                                raise ValueError('Unsupported temperature unit.')
                        elif meta.get('power_unit', 'W') == 'kW':
                            observed = observed * 1000.
                        elif meta.get('power_unit', 'W') != 'W':
                            raise ValueError('Unsupported power unit.')
                        if len(observed):
                            telemetry.update(mean=float(np.mean(observed)), _variance=float(np.var(observed, ddof=0)),
                                             min=float(observed.min()), max=float(observed.max()))
                    telemetry.update(samples=len(observed), seconds=len(observed) / fs,
                                     missing_samples=len(running) - len(observed),
                                     missing_seconds=(len(running) - len(observed)) / fs)
                except (ValueError, TypeError, OverflowError, FloatingPointError):
                    telemetry['_invalid'] = True
            _merge(result, part)
            offset += len(values)
    result['gap_seconds'] += extra_gap_seconds
    for metric in METRICS:
        if result[metric].pop('_invalid', False):
            warnings.append(f'The selected {metric.replace("_", " ")} values cannot be summarized numerically; its telemetry is excluded.')
    result.update(fs_hz=fs, n_rows=n, first_time_s=first, last_time_s=previous,
                  unrepresented_gap_seconds=extra_gap_seconds, _warnings=warnings)
    # Fail a source rather than serialize Infinity or silently cap extreme data.
    json.dumps(result, allow_nan=False)
    return result


def _summary(directory, meta, binding):
    # Cache only numeric source summaries. Set names/IDs and component identities
    # are deliberately absent; reassignment must reuse the data, not old totals.
    meta = {**meta, 'component_rpm_column': binding['rpm_column'],
            **{key: binding[key] for key in BINDING_FIELDS}}
    path = directory / 'data.parquet'
    if is_link_or_junction(path):
        raise ValueError('Sample data is a link; component use is unavailable.')
    stat = path.stat()
    key = json.dumps([str(path.resolve()), stat.st_size, stat.st_mtime_ns,
                      stat.st_ctime_ns, stat.st_ino, METHOD, {k: [k in meta, meta.get(k)] for k in
                      ('fs_hz', 'n_rows', 'time_column', 'columns', 'component_rpm_column',
                       *BINDING_FIELDS, 'nan_policy', 'time_gap_ranges', 'acquisition_gap_ranges')}], sort_keys=True, allow_nan=False)
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


def _source_row(name, status, binding=None):
    binding = binding or {}
    return dict(name=name, status=status, set_id=binding.get('id'), set_name=binding.get('name'),
                component_ids=binding.get('components', {}), rpm_column=binding.get('rpm_column'),
                motor_temperature_column=binding.get('motor_temperature_column'),
                motor_temperature_unit=binding.get('motor_temperature_unit', 'C'),
                power_column=binding.get('power_column'), power_unit=binding.get('power_unit', 'W'),
                set_revision=0, warnings=[], issue=None, summary=None, source_id=None)


def _ambiguous_sets(bindings):
    """Manual/old metadata must not charge the same hardware twice per test."""
    ids_seen, hardware_seen, ambiguous = {}, {}, set()
    for index, binding in enumerate(bindings):
        keys = [(ids_seen, binding['id']),
                *((hardware_seen, (kind, cid)) for kind, cid in binding['components'].items() if cid)]
        for seen, key in keys:
            if key in seen:
                ambiguous.update((index, seen[key]))
            else:
                seen[key] = index
    return ambiguous


def statistics():
    sources, dataset_tests = [], {}
    # Hold catalog membership stable against rename/trash/restore during the
    # scan. Each test is read atomically; this is not a global historical snapshot.
    with catalog_read():
        registry = components.list_components()['components']
        known = {item['id']: item['kind'] for item in registry}
        for info in store.list_tests():
            name, status = info['name'], info['status']
            rows = []
            try:
                if status != 'ready':
                    rows = [_source_row(name, status, binding) for binding in components.sets(info)]
                    if rows:
                        for row in rows:
                            row['issue'] = f'Test is {status}; it does not contribute.'
                    else:
                        rows = [_source_row(name, status)]
                        rows[0]['issue'] = 'No component sets assigned.'
                    sources.extend(rows)
                    continue
                directory = store.TESTS_DIR / name
                if is_link_or_junction(directory) or directory.resolve().parent != store.TESTS_DIR.resolve():
                    raise ValueError('Test directory is a link; component use is unavailable.')
                with data_read(name):
                    if store.get_status(name).get('status') != 'ready':
                        raise ValueError('Test became busy; refresh after processing completes.')
                    if is_link_or_junction(directory / 'meta.json'):
                        raise ValueError('Test metadata is a link; component use is unavailable.')
                    meta = store.get_meta(name)
                    if not isinstance(meta, dict):
                        raise ValueError('Current test metadata is unavailable.')
                    source_id = analysis_sources.read_identity(directory)
                    if source_id:
                        dataset_tests.setdefault(source_id, set()).add(name)
                    bindings = components.sets(meta)
                    ambiguous = _ambiguous_sets(bindings)
                    rows = [_source_row(name, status, binding) for binding in bindings]
                    if not rows:
                        # Keep an unconfigured test discoverable in coverage and
                        # duplicate-dataset checks without inventing a legacy set.
                        rows = [_source_row(name, status)]
                        rows[0].update(source_id=source_id, set_revision=meta.get('component_sets_revision', 0),
                                       issue='No component sets assigned.')
                    for index, (row, binding) in enumerate(zip(rows, bindings)):
                        row.update(source_id=source_id, set_revision=meta.get('component_sets_revision', 0),
                                   association_revision=meta.get('components_revision', 0),
                                   rpm_revision=meta.get('component_rpm_revision', 0),
                                   time_source=meta.get('time_source') if meta.get('time_source') in ('measured', 'generated') else 'unknown')
                        if not source_id:
                            row['warnings'].append('Legacy test has no durable dataset ID; this active folder is counted once.')
                        if row['time_source'] != 'measured':
                            row['warnings'].append('Timing is generated or legacy: source acquisition gaps cannot be inferred.')
                        if meta.get('nan_policy', 'keep_gaps') != 'keep_gaps':
                            row['warnings'].append('Uses current edited signal values, including filled values outside acquisition gaps.')
                        if any(cid and known.get(cid) != kind for kind, cid in row['component_ids'].items()):
                            row['warnings'].append('An assigned component is unavailable; its contribution is not reassigned.')
                        if index in ambiguous:
                            row['issue'] = 'Duplicate set ID or component assignment within this test; repair these sets before counting them.'
                        elif not any(row['component_ids'].values()):
                            row['issue'] = 'No components assigned.'
                        elif not row['rpm_column']:
                            row['issue'] = 'Select the component RPM column in Edit to include this set.'
                        elif row['rpm_column'] == meta['time_column'] or row['rpm_column'] not in meta['columns']:
                            row['issue'] = 'The selected RPM column is unavailable. Choose a current RPM column in Edit.'
                        else:
                            try:
                                row['summary'] = _summary(directory, meta, binding)
                                row['warnings'].extend(row['summary'].get('_warnings', []))
                            except (OSError, ValueError, KeyError, TypeError, OverflowError, FloatingPointError) as error:
                                row['issue'] = f'Component use unavailable: {error}'
            except (OSError, ValueError, KeyError, TypeError, OverflowError, FloatingPointError) as error:
                if not rows:
                    rows = [_source_row(name, status)]
                for row in rows:
                    row.update(issue=f'Component use unavailable: {error}', summary=None)
            sources.extend(rows)
    for row in sources:
        # Multiple sets legitimately share a dataset. Only distinct active test
        # folders carrying the same durable identity constitute a copied dataset.
        if len(dataset_tests.get(row['source_id'], ())) > 1:
            row.update(issue='Duplicate dataset identity; resolve the copied test before counting either source.', summary=None)
    totals = []
    for item in registry:
        assigned = [row for row in sources if row['component_ids'].get(item['kind']) == item['id']]
        total = _empty()
        for row in assigned:
            if row['summary']:
                _merge(total, row['summary'], motor_temperature=item['kind'] == 'motor')
        totals.append({**item, 'assigned_tests': len({row['name'] for row in assigned}),
                       'included_tests': len({row['name'] for row in assigned if row['summary'] is not None}),
                       'summary': total})
    # Internal moments/warnings belong to the cache and merge, never JSON output.
    def public(summary):
        return {key: public(value) if isinstance(value, dict) else value
                for key, value in summary.items() if not key.startswith('_')}
    for item in totals:
        item['summary'] = public(item['summary'])
    for row in sources:
        if row['summary']:
            row['summary'] = public(row['summary'])
    return dict(version=1, method=METHOD, policy='active-tests-positive-rpm',
                generated_at=datetime.now(timezone.utc).isoformat(),
                rpm_range_lower_bounds=RANGES, components=totals, sources=sources)
