"""Independent numerical/lifecycle oracles for measured component use."""
from concurrent.futures import ThreadPoolExecutor
import json
import math
from threading import Event
from unittest.mock import patch
from uuid import uuid4

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from fastapi.testclient import TestClient

from app import component_stats as stats, components, edit, ingest, main, store
from ._base import DataDirTestCase


class ComponentStatsTests(DataDirTestCase):
    def setUp(self):
        super().setUp()
        self.client = TestClient(main.app, raise_server_exceptions=False)
        stats._cache.clear()
        self.ids = {kind: self.create(kind, kind + '_1') for kind in components.KINDS}

    def create(self, kind, name):
        response = self.client.post('/api/components', json={'kind': kind, 'name': name})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['id']

    def fixture(self, name, rpm, fs=2, time=None, signals=None, **overrides):
        directory = self.tests / name
        directory.mkdir()
        time = np.arange(len(rpm)) / fs if time is None else time
        data = {'time': time, 'rpm': rpm, 'other': np.arange(len(rpm), dtype=float), **(signals or {})}
        pq.write_table(pa.table(data), directory / 'data.parquet')
        meta = dict(name=name, columns=list(data), time_column='time', time_unit='s',
                    fs_hz=fs, n_rows=len(rpm), n_columns=len(data), t_start=float(time[0]), duration_s=len(rpm) / fs,
                    time_source='measured', nan_policy='keep_gaps', time_gap_ranges=[], acquisition_gap_ranges=[],
                    components=self.ids.copy(), components_revision=0, component_rpm_column='rpm', component_rpm_revision=0,
                    notes='Preserve findings', user_meta={'motor': 'legacy free text'})
        meta.update(overrides)
        store.write_json_atomic(directory / 'meta.json', meta)
        store.write_json_atomic(directory / 'status.json', {'status': 'ready'})
        store.write_json_atomic(directory / 'source_identity.json', {'version': 1, 'id': str(uuid4())})
        return meta

    def change_meta(self, name, **fields):
        value = store.get_meta(name)
        value.update(fields)
        store.write_json_atomic(self.tests / name / 'meta.json', value)

    def read(self):
        response = self.client.get('/api/component-statistics')
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def motor(self, doc, id=None):
        return next(c for c in doc['components'] if c['id'] == (id or self.ids['motor']))

    def binding(self, name='Set 1', ids=None, rpm='rpm', **fields):
        return {**dict(id=str(uuid4()), name=name, components=self.ids.copy() if ids is None else ids,
                       rpm_column=rpm, motor_temperature_column=None, motor_temperature_unit='C',
                       power_column=None, power_unit='W'), **fields}

    def test_full_resolution_weighted_population_ranges_and_individual_ids(self):
        self.fixture('alpha', [0., 1000., 3000., None, -50., math.inf], fs=2)
        self.fixture('beta', [6000., 10000., 2000., 0.], fs=4)
        store.write_json_atomic(self.tests / 'alpha/testpoints.json', {'test_points': [
            {'id': 1, 'start_idx': 0, 'end_idx': 4}, {'id': 2, 'start_idx': 1, 'end_idx': 6}]})
        before = {p: p.read_bytes() for p in self.tests.rglob('*') if p.is_file()}
        actual = self.read()
        values = np.array([1000, 3000, 6000, 10000, 2000])
        weights = np.array([.5, .5, .25, .25, .25])
        mean = np.average(values, weights=weights)
        for item in actual['components']:
            summary = item['summary']
            self.assertEqual((item['included_tests'], item['assigned_tests']), (2, 2))
            self.assertEqual(summary['running_seconds'], 1.75)
            self.assertEqual(summary['missing_rpm_seconds'], 1.)
            self.assertEqual(summary['stopped_seconds'], 1.25)
            self.assertAlmostEqual(summary['mean_rpm'], mean)
            self.assertAlmostEqual(summary['sd_rpm'], np.sqrt(np.average((values - mean) ** 2, weights=weights)))
            self.assertEqual((summary['min_rpm'], summary['max_rpm']), (1000, 10000))
            self.assertEqual(summary['ranges_seconds'], [0, .75, .5, .25, .25])
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)

    def test_zero_is_distinct_from_unconfigured_missing_and_unassociated(self):
        self.fixture('stopped', [0., -1.])
        self.fixture('missing', [None, math.nan, math.inf, -math.inf])
        self.fixture('unconfigured', [1200., 1300.], component_rpm_column=None)
        self.fixture('unassociated', [1000., 1500.], components={})
        doc = self.read()
        motor = self.motor(doc)
        self.assertEqual((motor['included_tests'], motor['assigned_tests']), (2, 3))
        self.assertEqual(motor['summary']['running_seconds'], 0)
        self.assertIsNone(motor['summary']['mean_rpm'])
        self.assertIsNone(motor['summary']['sd_rpm'])
        self.assertEqual(motor['summary']['missing_rpm_samples'], 4)
        self.assertEqual(sum(row['summary'] is None for row in doc['sources']), 2)

    def test_batch_seams_gaps_histogram_and_large_constant(self):
        self.fixture('alpha', [1000.] * 10, time=[0., .5, 1., 1.5, 2., 5., 5.5, 6., 6.5, 7.],
                     acquisition_gap_ranges=[[2, 5], [4, 6]])
        with patch.object(stats, 'BATCH_SIZE', 3):
            result = self.read()['sources'][0]['summary']
        self.assertEqual(result['gap_samples'], 4)
        self.assertEqual(result['gap_seconds'], 4.5)
        self.assertEqual(result['running_samples'], 6)
        self.assertEqual(result['running_seconds'], 3.)
        self.assertEqual(result['sd_rpm'], 0)
        stats._cache.clear()
        self.assertEqual(self.read()['sources'][0]['summary'], result)

    def test_native_multibatch_keeps_single_sample_extreme_without_reduction(self):
        n = 2 * stats.BATCH_SIZE + 31
        rpm = np.full(n, 1000.)
        rpm[-2] = 12000.
        self.fixture('alpha', rpm, fs=2048)
        value = self.read()['sources'][0]['summary']
        self.assertEqual(value['running_samples'], n)
        self.assertEqual(value['running_seconds'], n / 2048)
        self.assertEqual(value['max_rpm'], 12000)
        self.assertAlmostEqual(value['mean_rpm'], 1000 + 11000 / n)
        self.assertAlmostEqual(value['sd_rpm'], 11000 * math.sqrt((n - 1) / (n * n)))
        self.assertEqual(value['ranges_seconds'][-1], 1 / 2048)

    def test_damaged_metadata_is_a_visible_exclusion_not_a_failed_library(self):
        self.fixture('alpha', [100., 200.])
        self.fixture('beta', [10., 20.])
        (self.tests / 'alpha/meta.json').write_text('broken')
        doc = self.read()
        self.assertIn('metadata is unavailable', doc['sources'][0]['issue'])
        self.assertEqual(self.motor(doc)['summary']['running_seconds'], 1)

    def test_rpm_validation_revision_atomic_conflict_and_noop(self):
        self.fixture('alpha', [10., 20.])
        route = '/api/tests/alpha/meta'
        for column in ('time', 'missing', '', 42, [], 'x' * 256):
            with self.subTest(column=repr(column)[:30]):
                self.assertEqual(self.client.patch(route, json={'component_rpm_column': column,
                    'expected_component_rpm_revision': 0, 'notes': 'Do not apply'}).status_code, 422)
        self.assertEqual(store.get_meta('alpha')['notes'], 'Preserve findings')
        self.assertEqual(self.client.patch(route, json={'component_rpm_column': None}).status_code, 409)
        first = self.client.patch(route, json={'component_rpm_column': 'rpm', 'expected_component_rpm_revision': 0})
        self.assertEqual(first.json()['component_rpm_revision'], 0)
        second = self.client.patch(route, json={'component_rpm_column': 'other', 'expected_component_rpm_revision': 0})
        self.assertEqual(second.json()['component_rpm_revision'], 1)
        before = (self.tests / 'alpha/meta.json').read_bytes()
        with patch.object(store, 'write_json_atomic', side_effect=OSError('disk full')):
            self.assertEqual(self.client.patch(route, json={'component_rpm_column': None, 'expected_component_rpm_revision': 1}).status_code, 500)
        self.assertEqual(self.client.patch(route, json={'component_rpm_column': None, 'expected_component_rpm_revision': 0,
            'components': {}, 'expected_components_revision': 0}).status_code, 409)
        self.assertEqual((self.tests / 'alpha/meta.json').read_bytes(), before)
        self.assertEqual(self.client.patch(route, json={'component_rpm_column': None, 'expected_component_rpm_revision': 1}).json()['component_rpm_revision'], 2)

    def test_cache_fresh_membership_assignments_rpm_rate_and_replacement(self):
        self.fixture('alpha', [1000., 2000.])
        second = self.create('motor', 'motor_2')
        with patch.object(stats, '_calculate', wraps=stats._calculate) as calculate:
            self.read(); self.read()
            self.assertEqual(calculate.call_count, 1)
            response = self.client.patch('/api/tests/alpha/meta', json={'components': {**self.ids, 'motor': second}, 'expected_components_revision': 0})
            self.assertEqual(response.status_code, 200)
            doc = self.read()
            self.assertEqual(self.motor(doc)['assigned_tests'], 0)
            self.assertEqual(self.motor(doc, second)['summary']['running_seconds'], 1)
            self.assertEqual(calculate.call_count, 1)
            self.change_meta('alpha', fs_hz=4)
            self.assertEqual(self.motor(self.read(), second)['summary']['running_seconds'], .5)
            self.change_meta('alpha', component_rpm_column='other')
            self.assertEqual(self.motor(self.read(), second)['summary']['mean_rpm'], 1)
            self.assertEqual(calculate.call_count, 3)
            pq.write_table(pa.table({'time': [0., .25], 'rpm': [1., 1.], 'other': [2000., 2000.]}), self.tests / 'alpha/data.parquet')
            self.assertEqual(self.motor(self.read(), second)['summary']['mean_rpm'], 2000)
            self.assertEqual(calculate.call_count, 4)

    def test_active_lifecycle_rename_trash_restore_name_conflict_and_permanent_delete(self):
        self.fixture('alpha', [1000., 2000.])
        self.read()
        self.assertEqual(self.client.post('/api/tests/alpha/rename?new_name=renamed').status_code, 200)
        self.assertEqual(self.read()['sources'][0]['name'], 'renamed')
        self.assertEqual(self.client.delete('/api/tests/renamed').status_code, 200)
        self.assertEqual(self.motor(self.read())['assigned_tests'], 0)
        self.assertEqual(self.client.post('/api/tests/renamed/restore').status_code, 200)
        self.assertEqual(self.motor(self.read())['summary']['running_seconds'], 1.)
        self.client.delete('/api/tests/renamed')
        entry = self.client.get('/api/trash').json()['entries'][0]
        self.assertEqual(self.client.delete('/api/trash/' + entry['id']).status_code, 200)
        self.assertEqual(self.motor(self.read())['assigned_tests'], 0)

    def test_rpm_reference_rename_drop_trim_and_rebuild_invalidation(self):
        self.fixture('alpha', [0., 1000., 2000., 3000., 4000., 5000.])
        self.read()
        edit.rebuild_test('alpha', {'rename': {'rpm': 'shaft speed'}, 'trim_t0': .5, 'trim_t1': 2})
        meta = store.get_meta('alpha')
        self.assertEqual((meta['component_rpm_column'], meta['component_rpm_revision']), ('shaft speed', 1))
        summary = self.read()['sources'][0]['summary']
        self.assertEqual(summary['running_seconds'], 2.)
        self.assertEqual(summary['mean_rpm'], 2500.)
        edit.rebuild_test('alpha', {'drop': ['shaft speed']})
        self.assertIsNone(store.get_meta('alpha')['component_rpm_column'])
        self.assertEqual(store.get_meta('alpha')['component_rpm_revision'], 2)
        self.assertIsNone(self.read()['sources'][0]['summary'])

    def test_ingested_gap_provenance_survives_fill_then_trim(self):
        path = self.root / 'observed.csv'
        path.write_text('time,rpm\n0,1000\n0.5,1000\n1,1000\n3,1000\n3.5,1000\n4,1000\n')
        ingest.ingest_csv(path, 'alpha')
        self.change_meta('alpha', components=self.ids, component_rpm_column='rpm')
        meta = store.get_meta('alpha')
        self.assertEqual(meta['acquisition_gap_ranges'], [[3, 6]])
        expected = self.read()['sources'][0]['summary']['running_seconds']
        edit.rebuild_test('alpha', {'nan_policy': 'interpolate'})
        meta = store.get_meta('alpha')
        self.assertEqual(meta['time_gap_ranges'], [])
        self.assertEqual(meta['acquisition_gap_ranges'], [[3, 6]])
        self.assertEqual(self.read()['sources'][0]['summary']['running_seconds'], expected)
        edit.rebuild_test('alpha', {'trim_t0': 1, 'trim_t1': 3.5})
        self.assertEqual(store.get_meta('alpha')['acquisition_gap_ranges'], [[1, 4]])
        self.assertEqual(self.read()['sources'][0]['summary']['running_seconds'], 1.5)

    def test_legacy_fill_history_unknown_generated_time_and_duplicate_identity(self):
        self.fixture('alpha', [10., 20.], time_source='generated')
        self.assertTrue(self.read()['sources'][0]['warnings'])
        meta = store.get_meta('alpha'); meta.pop('acquisition_gap_ranges'); meta['nan_policy'] = 'interpolate'
        store.write_json_atomic(self.tests / 'alpha/meta.json', meta)
        self.assertIn('history', self.read()['sources'][0]['issue'])
        edit.rebuild_test('alpha', {'nan_policy': 'keep_gaps'})
        self.assertIsNone(store.get_meta('alpha')['acquisition_gap_ranges'])
        self.assertIn('history', self.read()['sources'][0]['issue'])
        self.fixture('beta', [10., 20.])
        (self.tests / 'beta/source_identity.json').write_bytes((self.tests / 'alpha/source_identity.json').read_bytes())
        doc = self.read()
        self.assertTrue(all('Duplicate' in row['issue'] for row in doc['sources']))
        self.assertEqual(self.motor(doc)['included_tests'], 0)

    def test_invalid_sources_partial_busy_unknown_component_and_retry(self):
        self.fixture('good', [10., 20.])
        self.fixture('bad', [100., 200.])
        for fields in ({'fs_hz': 0}, {'fs_hz': math.inf}, {'n_rows': 999}, {'acquisition_gap_ranges': [[-1, 4]]},
                       {'acquisition_gap_ranges': 'bad'}, {'component_rpm_column': 'missing'},
                       {'component_rpm_column': {'bad': True}}, {'components': 'legacy text'}, {'components': []}):
            with self.subTest(fields=fields):
                saved = store.get_meta('bad')
                self.change_meta('bad', **fields)
                result = self.read()
                self.assertIsNotNone(result['sources'][0]['issue'])
                self.assertEqual(self.motor(result)['included_tests'], 1)
                store.write_json_atomic(self.tests / 'bad/meta.json', saved)
        store.write_json_atomic(self.tests / 'bad/status.json', {'status': 'rebuilding'})
        self.assertEqual(self.motor(self.read())['included_tests'], 1)
        store.write_json_atomic(self.tests / 'bad/status.json', {'status': 'ready'})
        self.assertEqual(self.motor(self.read())['included_tests'], 2)
        self.change_meta('bad', components={**self.ids, 'motor': str(uuid4())})
        doc = self.read()
        self.assertTrue(doc['sources'][0]['warnings'])
        self.assertEqual(self.motor(doc)['assigned_tests'], 1)

    def test_invalid_timestamps_and_extreme_values_do_not_publish_nonfinite_totals(self):
        self.fixture('alpha', [100., 200.])
        for time, rpm in (([0., 0.], [1., 2.]), ([1., 0.], [1., 2.]), ([0., math.nan], [1., 2.]),
                          ([0., 1.], [1e308, 1e308])):
            with self.subTest(time=time, rpm=rpm):
                pq.write_table(pa.table({'time': time, 'rpm': rpm, 'other': [1., 2.]}), self.tests / 'alpha/data.parquet')
                self.assertIsNone(self.read()['sources'][0]['summary'])
        (self.tests / 'alpha/data.parquet').write_bytes(b'broken')
        self.assertIsNone(self.read()['sources'][0]['summary'])

    def test_cache_bounded_registry_failure_and_no_identity_migration(self):
        self.fixture('alpha', [100., 200.])
        (self.tests / 'alpha/source_identity.json').unlink()
        self.read()
        self.assertFalse((self.tests / 'alpha/source_identity.json').exists())
        meta = store.get_meta('alpha'); meta.pop('acquisition_gap_ranges')
        store.write_json_atomic(self.tests / 'alpha/meta.json', meta)
        self.assertIsNotNone(self.read()['sources'][0]['summary'])
        self.change_meta('alpha', acquisition_gap_ranges=None)
        self.assertIsNone(self.read()['sources'][0]['summary'])
        self.change_meta('alpha', acquisition_gap_ranges=[])
        with patch.object(stats, 'MAX_CACHE', 2):
            for fs in (3, 4, 5):
                self.change_meta('alpha', fs_hz=fs); self.read()
            self.assertLessEqual(len(stats._cache), 2)
        components._path().write_text('not json')
        self.assertEqual(self.client.get('/api/component-statistics').status_code, 409)

    def test_concurrent_reassignment_is_atomic_and_does_not_deadlock_reader(self):
        self.fixture('alpha', [100., 200.])
        entered, release = Event(), Event()
        original = stats._calculate
        def held(*args):
            entered.set(); self.assertTrue(release.wait(5)); return original(*args)
        with patch.object(stats, '_calculate', side_effect=held), ThreadPoolExecutor(max_workers=2) as pool:
            read = pool.submit(self.read)
            self.assertTrue(entered.wait(3))
            writer = pool.submit(self.client.patch, '/api/tests/alpha/meta', json={
                'components': {}, 'expected_components_revision': 0})
            release.set()
            self.assertEqual(self.motor(read.result(5))['included_tests'], 1)
            self.assertEqual(writer.result(5).status_code, 200)
        self.assertEqual(self.motor(self.read())['included_tests'], 0)

    def test_independent_sets_rpm_masks_telemetry_coverage_and_motor_only_temperature(self):
        right_ids = {kind: self.create(kind, kind + '_right') for kind in components.KINDS}
        bindings = [
            self.binding('Left', motor_temperature_column='temp', power_column='power', power_unit='kW'),
            self.binding('Right', ids=right_ids, rpm='rpm2', motor_temperature_column='temp2',
                         motor_temperature_unit='F', power_column='power2'),
        ]
        self.fixture('alpha', [1000., 0., 3000., None, 5000., 6000.], signals={
            'rpm2': [0., 2000., None, 4000., 0., 8000.],
            'temp': [20., 999., None, 999., 999., 60.],
            'power': [1., 999., 3., 999., 999., None],
            'temp2': [999., 86., 999., None, 999., 122.],
            'power2': [999., -200., 999., 400., 999., 0.],
        }, component_sets=bindings, component_sets_revision=7, acquisition_gap_ranges=[[4, 5]])
        with patch.object(stats, 'BATCH_SIZE', 2):
            doc = self.read()
        self.assertEqual((doc['version'], doc['method']), (1, 'component-usage-v2'))
        left, right = doc['sources']
        self.assertEqual(len(doc['sources']), 2)
        self.assertEqual(left['source_id'], right['source_id'])
        for row, binding in zip(doc['sources'], bindings):
            self.assertIsNone(row['issue'])
            self.assertEqual((row['set_id'], row['set_name'], row['set_revision']), (binding['id'], binding['name'], 7))
            self.assertEqual(row['summary']['running_seconds'], 1.5)
            self.assertEqual(row['summary']['gap_samples'], 1)
            for key in stats.BINDING_FIELDS:
                self.assertEqual(row[key], binding[key])
        self.assertAlmostEqual(left['summary']['mean_rpm'], 10000 / 3)
        self.assertAlmostEqual(right['summary']['mean_rpm'], 14000 / 3)
        expected = {'unit': 'C', 'samples': 2, 'seconds': 1., 'missing_samples': 1,
                    'missing_seconds': .5, 'mean': 40., 'sd': 20., 'min': 20., 'max': 60.}
        self.assertEqual(left['summary']['motor_temperature'], expected)
        self.assertEqual(right['summary']['motor_temperature'], {**expected, 'sd': 10., 'min': 30., 'max': 50.})
        self.assertEqual(left['summary']['power'], {**expected, 'unit': 'W', 'mean': 2000., 'sd': 1000., 'min': 1000., 'max': 3000.})
        self.assertEqual(right['summary']['power']['missing_samples'], 0)
        self.assertAlmostEqual(right['summary']['power']['mean'], 200 / 3)
        for item in doc['components']:
            row = left if item['id'] in self.ids.values() else right
            self.assertEqual((item['assigned_tests'], item['included_tests']), (1, 1))
            self.assertEqual(item['summary']['power'], row['summary']['power'])
            if item['kind'] == 'motor':
                self.assertEqual(item['summary']['motor_temperature'], row['summary']['motor_temperature'])
            else:
                self.assertEqual(item['summary']['motor_temperature']['samples'], 0)
                self.assertIsNone(item['summary']['motor_temperature']['mean'])
        stats._cache.clear()
        def assert_close(expected, actual):
            self.assertEqual(expected.keys(), actual.keys())
            for key, value in expected.items():
                if isinstance(value, dict):
                    assert_close(value, actual[key])
                elif isinstance(value, float):
                    self.assertAlmostEqual(value, actual[key])
                else:
                    self.assertEqual(value, actual[key])
        for expected_row, actual_row in zip(doc['sources'], self.read()['sources']):
            assert_close(expected_row, actual_row)

    def test_telemetry_converts_then_aggregates_by_its_observed_seconds(self):
        self.fixture('alpha', [100., 200.], signals={'temp': [32., 212.], 'power': [1., None]},
                     component_sets=[self.binding(motor_temperature_column='temp', motor_temperature_unit='F',
                                                  power_column='power', power_unit='kW')])
        self.fixture('beta', [100., 100., 100., 0., 100.], fs=4,
                     signals={'temp': [323.15, 373.15, None, 999., 273.15], 'power': [0., 2000., 3000., 999., None]},
                     component_sets=[self.binding(motor_temperature_column='temp', motor_temperature_unit='K', power_column='power')])
        summary = self.motor(self.read())['summary']
        self.assertEqual(summary['running_seconds'], 2.)
        for key, values, weights, missing in (
            ('motor_temperature', [0., 100., 50., 100., 0.], [.5, .5, .25, .25, .25], .25),
            ('power', [1000., 0., 2000., 3000.], [.5, .25, .25, .25], .75),
        ):
            with self.subTest(metric=key):
                metric = summary[key]
                mean = np.average(values, weights=weights)
                self.assertEqual(metric['samples'], len(values))
                self.assertEqual(metric['seconds'], sum(weights))
                self.assertEqual(metric['missing_seconds'], missing)
                self.assertAlmostEqual(metric['mean'], mean)
                self.assertAlmostEqual(metric['sd'], np.sqrt(np.average((values - mean) ** 2, weights=weights)))
                self.assertEqual((metric['min'], metric['max']), (min(values), max(values)))

    def test_zero_telemetry_is_distinct_from_unconfigured_or_nonfinite(self):
        self.fixture('alpha', [100., 100.], signals={'temp': [0., 0.], 'power': [0., 0.]},
                     component_sets=[self.binding(motor_temperature_column='temp', power_column='power')])
        self.fixture('beta', [100., 100.], component_sets=[self.binding()])
        self.fixture('gamma', [100., 100.], signals={'temp': [math.inf, math.nan], 'power': [None, -math.inf]},
                     component_sets=[self.binding(motor_temperature_column='temp', power_column='power')])
        doc = self.read()
        self.assertEqual(self.motor(doc)['summary']['running_seconds'], 3.)
        for key in stats.METRICS:
            metric = self.motor(doc)['summary'][key]
            self.assertEqual((metric['mean'], metric['sd'], metric['min'], metric['max']), (0, 0, 0, 0))
            self.assertEqual((metric['samples'], metric['seconds'], metric['missing_samples'], metric['missing_seconds']), (2, 1, 4, 2))
            self.assertIsNone(doc['sources'][1]['summary'][key]['mean'])
            self.assertIsNone(doc['sources'][2]['summary'][key]['mean'])

    def test_unavailable_or_invalid_optional_telemetry_preserves_rpm_and_other_metric(self):
        binding = self.binding(motor_temperature_column='missing', power_column='power')
        self.fixture('alpha', [100., 200.], signals={'power': [10., 20.], 'bad': ['text', 'bad'],
                     'extreme': [1e308, -1e308]}, component_sets=[binding])
        self.fixture('beta', [100., 200.], signals={'temp': [40., 60.]},
                     component_sets=[self.binding(motor_temperature_column='temp')])
        original = store.get_meta('alpha')
        for column, columns in (
            ('missing', original['columns']),
            ('missing', [*original['columns'], 'missing']),
            ('time', original['columns']),
            ('bad', original['columns']),
            ('extreme', original['columns']),
        ):
            with self.subTest(column=column, stale='missing' in columns):
                self.change_meta('alpha', component_sets=[{**binding, 'motor_temperature_column': column}], columns=columns)
                doc = self.read()
                row = doc['sources'][0]
                self.assertIsNone(row['issue'])
                self.assertTrue(any('motor temperature' in message for message in row['warnings']))
                self.assertEqual(row['summary']['running_seconds'], 1.)
                metric = row['summary']['motor_temperature']
                self.assertEqual((metric['samples'], metric['missing_samples']), (0, 2))
                self.assertIsNone(metric['mean'])
                self.assertEqual(row['summary']['power']['mean'], 15.)
                # An unavailable channel in one test adds missing coverage; it
                # must not discard valid observations in other assigned tests.
                total = self.motor(doc)['summary']['motor_temperature']
                self.assertEqual((total['mean'], total['sd'], total['samples'], total['missing_samples']), (50., 10., 2, 2))

    def test_multiset_cache_binds_signals_units_but_never_component_assignments(self):
        other_motor = self.create('motor', 'Motor 2')
        bindings = [self.binding(ids={'motor': self.ids['motor']}, motor_temperature_column='temp',
                                 motor_temperature_unit='F', power_column='power', power_unit='kW'),
                    self.binding('Right', ids={'motor': other_motor}, rpm='rpm2', motor_temperature_column='temp', power_column='power')]
        self.fixture('alpha', [100., 200., 0.], signals={'rpm2': [0., 1000., 3000.], 'temp': [32., 68., 104.],
                     'alt': [100., 200., 300.], 'power': [1., 2., 3.]}, component_sets=bindings)
        with patch.object(stats, '_calculate', wraps=stats._calculate) as calculate:
            self.read(); self.read()
            self.assertEqual(calculate.call_count, 2)
            bindings[0]['components'], bindings[1]['components'] = bindings[1]['components'], bindings[0]['components']
            bindings[0]['name'] = 'Moved motor'
            self.change_meta('alpha', component_sets=bindings, component_sets_revision=1)
            doc = self.read()
            self.assertEqual(calculate.call_count, 2)
            self.assertEqual(self.motor(doc, other_motor)['summary']['motor_temperature']['mean'], 10.)
            self.assertEqual(self.motor(doc)['summary']['motor_temperature']['mean'], 86.)
            for fields, metric, expected in (
                ({'motor_temperature_unit': 'C'}, 'motor_temperature', 50.),
                ({'power_unit': 'W'}, 'power', 1.5),
                ({'motor_temperature_column': 'alt'}, 'motor_temperature', 150.),
                ({'rpm_column': 'rpm2'}, 'motor_temperature', 250.),
            ):
                prior = calculate.call_count
                bindings[0].update(fields)
                self.change_meta('alpha', component_sets=bindings)
                self.assertEqual(self.motor(self.read(), other_motor)['summary'][metric]['mean'], expected)
                self.assertEqual(calculate.call_count, prior + 1)
            self.change_meta('alpha', component_sets=[bindings[1]])
            self.assertEqual(self.motor(self.read(), other_motor)['included_tests'], 0)
            self.assertEqual(calculate.call_count, 6)

    def test_duplicate_datasets_count_distinct_tests_including_an_empty_set_list(self):
        other_motor = self.create('motor', 'Motor 2')
        self.fixture('alpha', [100., 200.], component_sets=[self.binding(ids={'motor': self.ids['motor']}),
                     self.binding('Right', ids={'motor': other_motor}, rpm='other')])
        self.assertTrue(all(row['summary'] for row in self.read()['sources']))
        self.fixture('beta', [100., 200.], component_sets=[])
        (self.tests / 'beta/source_identity.json').write_bytes((self.tests / 'alpha/source_identity.json').read_bytes())
        doc = self.read()
        self.assertEqual(len(doc['sources']), 3)
        self.assertTrue(all('Duplicate dataset' in row['issue'] for row in doc['sources']))
        self.assertEqual(self.motor(doc)['included_tests'], 0)
        store.write_json_atomic(self.tests / 'beta/source_identity.json', {'version': 1, 'id': str(uuid4())})
        doc = self.read()
        self.assertTrue(all(row['summary'] for row in doc['sources'][:2]))
        self.assertEqual(doc['sources'][2]['issue'], 'No component sets assigned.')
        self.change_meta('alpha', component_sets=[])
        doc = self.read()
        self.assertEqual(len(doc['sources']), 2)
        self.assertTrue(all(row['set_id'] is None and row['set_name'] is None and row['component_ids'] == {}
                            and row['issue'] == 'No component sets assigned.' for row in doc['sources']))
        self.assertEqual(self.motor(doc)['assigned_tests'], 0)
        store.write_json_atomic(self.tests / 'beta/status.json', {'status': 'rebuilding'})
        self.assertEqual(self.read()['sources'][1]['issue'], 'No component sets assigned.')

    def test_corrupt_duplicate_assignments_and_ids_exclude_only_affected_sets(self):
        other_motor = self.create('motor', 'Motor 2')
        third_motor = self.create('motor', 'Motor 3')
        bindings = [self.binding(ids={'motor': self.ids['motor']}),
                    self.binding('Second', ids={'motor': self.ids['motor']}, rpm='other'),
                    self.binding('Third', ids={'motor': third_motor})]
        self.fixture('alpha', [100., 200.], component_sets=bindings)
        doc = self.read()
        self.assertTrue(all('Duplicate set' in row['issue'] for row in doc['sources'][:2]))
        self.assertIsNotNone(doc['sources'][2]['summary'])
        self.assertEqual((self.motor(doc)['assigned_tests'], self.motor(doc)['included_tests']), (1, 0))
        self.assertEqual(self.motor(doc, third_motor)['summary']['running_seconds'], 1)
        bindings[1].update(components={'motor': other_motor}, id=bindings[0]['id'])
        self.change_meta('alpha', component_sets=bindings)
        doc = self.read()
        self.assertTrue(all('Duplicate set' in row['issue'] for row in doc['sources'][:2]))
        self.assertEqual(self.motor(doc, other_motor)['included_tests'], 0)
        self.assertEqual(self.motor(doc, third_motor)['included_tests'], 1)

    def test_missing_rpm_in_one_set_does_not_exclude_other_set(self):
        other_motor = self.create('motor', 'Motor 2')
        bindings = [self.binding(ids={'motor': self.ids['motor']}, rpm='missing'),
                    self.binding('Second', ids={'motor': other_motor})]
        meta = self.fixture('alpha', [100., 200.], component_sets=bindings)
        for columns in (meta['columns'], [*meta['columns'], 'missing']):
            self.change_meta('alpha', columns=columns)
            doc = self.read()
            self.assertIsNotNone(doc['sources'][0]['issue'])
            self.assertIsNone(doc['sources'][1]['issue'])
            self.assertEqual(self.motor(doc, other_motor)['summary']['running_seconds'], 1)

    def test_optional_columns_use_bounded_projection_without_reading_other_signals(self):
        self.fixture('alpha', [100., 200., 300.], signals={'temp': [10., 20., 30.],
                     'unused': ['invalid', 'as', 'numbers']}, component_sets=[self.binding(
                     motor_temperature_column='temp', power_column='temp')])
        native_file = stats.pq.ParquetFile
        requests = []
        class RecordingParquet:
            def __init__(self, path):
                self.parquet = native_file(path)
                self.metadata = self.parquet.metadata
                self.schema_arrow = self.parquet.schema_arrow
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.parquet.close()
            def iter_batches(self, **kwargs):
                requests.append(kwargs)
                yield from self.parquet.iter_batches(**kwargs)
        with patch.object(stats, 'BATCH_SIZE', 2), patch.object(stats.pq, 'ParquetFile', RecordingParquet):
            row = self.read()['sources'][0]
        self.assertEqual(requests, [{'batch_size': 2, 'columns': ['time', 'rpm', 'temp']}])
        self.assertEqual(row['summary']['power']['mean'], 20.)
        self.assertEqual(row['summary']['motor_temperature']['mean'], 20.)
