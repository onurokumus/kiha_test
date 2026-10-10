"""Independent numeric and immutable-source checks for saved-data comparison."""
import asyncio
import hashlib
from contextlib import contextmanager
from unittest.mock import patch
from uuid import uuid4

from fastapi import BackgroundTasks, HTTPException
from fastapi.testclient import TestClient
import numpy as np
import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
from scipy.ndimage import uniform_filter1d

from app import analysis_sources, ingest, main, preprocess, preprocess_compare, store
from ._base import DataDirTestCase


class PreprocessCompareTests(DataDirTestCase):
    def setUp(self):
        super().setUp()
        self.client = TestClient(main.app)
        self.name = 'comparison'
        self.directory = self.tests / self.name
        self.directory.mkdir()
        self.fs = 1000.0
        self.start = 13.0

    def fixture(self, count=1200, gaps=False, both=False):
        self.times = self.start + np.arange(count) / self.fs
        self.values = np.sin(np.arange(count) * .013) + .3 * np.cos(np.arange(count) * .19)
        self.other = np.arange(count) * .123456789
        if gaps:
            self.values[33] = np.nan
            self.values[61] = np.inf
            self.values[300:350] = 77  # Retained history excludes previously filled gaps.
        self.meta = dict(name=self.name, columns=['time', 'signal, unusual / name', 'other'],
                         time_column='time', fs_hz=self.fs, n_rows=count, n_columns=3,
                         t_start=self.start, duration_s=count / self.fs,
                         time_gap_ranges=[], acquisition_gap_ranges=[[300, 350]] if gaps else [],
                         nan_policy='keep_gaps', derived_variables=[])
        self.column = self.meta['columns'][1]
        pq.write_table(pa.table({'time': self.times, self.column: self.values, 'other': self.other}),
                       self.directory / 'data.parquet', row_group_size=311)
        ingest.build_pyramid(self.directory / 'data.parquet', self.directory / 'pyramid', 'time')
        store.write_json_atomic(self.directory / 'meta.json', self.meta)
        store.write_json_atomic(self.directory / 'status.json', {'status': 'ready'})
        store.write_json_atomic(self.directory / 'testpoints.json', {'test_points': []})
        snapshot = preprocess.get_preprocess(self.name)
        filters = [{'column': self.column, 'filter': {'kind': 'moving_avg', 'window_s': 11 / self.fs}}]
        if both:
            filters.append({'column': 'other', 'filter': {'kind': 'moving_avg', 'window_s': 19 / self.fs}})
        payload = preprocess.PreprocessRequest(
            request_id=uuid4(), source_id=snapshot['source']['id'],
            source_revision=snapshot['source']['revision'],
            filters=filters)
        task = BackgroundTasks()
        preprocess.create_preprocess(self.name, payload, task)
        asyncio.run(task())
        self.assertEqual(store.get_status(self.name)['status'], 'ready')
        self.snapshot = preprocess.get_preprocess(self.name)
        self.params = dict(column=self.column, source_id=self.snapshot['source']['id'],
                           source_revision=self.snapshot['source']['revision'])

    def get(self, **extra):
        return self.client.get(f'/api/tests/{self.name}/preprocess/compare',
                               params={**self.params, **extra})

    def fingerprint(self):
        return {str(p.relative_to(self.directory)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.directory.rglob('*') if p.is_file()}

    def xy(self, **extra):
        params = {key: self.params[key] for key in ('source_id', 'source_revision')}
        return self.client.get(f'/api/tests/{self.name}/preprocess/compare/xy',
                               params={**params, 'x': self.column, 'y': 'other', **extra})

    def write_xy(self, original_x, original_y, filtered_x, filtered_y, row_groups=(311, 509)):
        for version, directory, xv, yv in [
                (0, self.directory / preprocess.ORIGINAL, original_x, original_y),
                (1, self.directory, filtered_x, filtered_y)]:
            pq.write_table(pa.table({'time': self.times, self.column: xv, 'other': yv}),
                           directory / 'data.parquet', row_group_size=row_groups[version])
        self.params['source_revision'] = preprocess.get_preprocess(self.name)['source']['revision']

    def test_xy_native_pairs_preserve_both_filtered_axes_precision_and_source_files(self):
        self.fixture(both=True)
        before = self.fingerprint()
        response = self.xy()
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data['mode'], 'raw')
        self.assertEqual(data['stride'], 1)
        self.assertEqual(data['n_raw'], 1200)
        self.assertEqual(data['n_sampled'], 1200)
        self.assertEqual(data['indices'], list(range(1200)))
        self.assertEqual(data['fallback_indices'], [])
        np.testing.assert_array_equal(data['t'], self.times)
        np.testing.assert_array_equal(data['original']['x'], self.values)
        np.testing.assert_array_equal(data['original']['y'], self.other)
        np.testing.assert_allclose(data['filtered']['x'], uniform_filter1d(self.values, 11, mode='nearest'), atol=1e-14)
        np.testing.assert_allclose(data['filtered']['y'], uniform_filter1d(self.other, 19, mode='nearest'), atol=1e-13)
        self.assertEqual(data['source'], self.snapshot['source'])
        self.assertEqual(data['range'], {'start': self.start, 'end': self.start + 1.2})
        for version in ('original', 'filtered'):
            self.assertEqual(data['summary'][version], dict(finite_pairs=1200, missing_pairs=0,
                                                          native_finite_pairs=1200, native_missing_pairs=0))
        self.assertEqual(self.fingerprint(), before)
        self.assertEqual([entry['name'] for entry in store.list_tests()], [self.name])

    def test_xy_swapped_same_and_time_axes_use_exact_native_rows(self):
        self.fixture(both=True)
        before = self.fingerprint()
        originals = {self.column: self.values, 'other': self.other, 'time': self.times}
        filtered = {self.column: uniform_filter1d(self.values, 11, mode='nearest'),
                    'other': uniform_filter1d(self.other, 19, mode='nearest'), 'time': self.times}
        for x, y in [('other', self.column), (self.column, self.column), ('time', 'other'),
                     (self.column, 'time'), ('time', 'time')]:
            with self.subTest(x=x, y=y):
                response = self.xy(x=x, y=y, t0=13.2, t1=13.5)
                self.assertEqual(response.status_code, 200, response.text)
                data = response.json()
                lo, hi = store.window_bounds(self.meta, 13.2, 13.5)
                self.assertEqual(data['indices'], list(range(lo, hi)))
                for version, expected in [('original', originals), ('filtered', filtered)]:
                    np.testing.assert_allclose(data[version]['x'], expected[x][lo:hi], atol=1e-13)
                    np.testing.assert_allclose(data[version]['y'], expected[y][lo:hi], atol=1e-13)
        self.assertEqual(self.fingerprint(), before)

    def test_xy_gaps_and_independent_nonfinite_pairs_are_masked_together_per_version(self):
        self.fixture(gaps=True)
        ox, oy, fx, fy = (value.copy() for value in [self.values, self.other, self.values, self.other])
        ox[2], oy[3], fx[4], fy[5] = np.nan, np.inf, -np.inf, np.nan
        fx[2], fy[3] = 4.25, -3.5
        self.write_xy(ox, oy, fx, fy)
        before = self.fingerprint()
        data = self.xy().json()
        for version, rows in [('original', [2, 3, 33, 61]), ('filtered', [4, 5, 33, 61])]:
            for axis in ('x', 'y'):
                for row in rows:
                    self.assertIsNone(data[version][axis][row])
                self.assertEqual(data[version][axis][300:350], [None] * 50)
            self.assertEqual(data['summary'][version]['finite_pairs'], 1146)
            self.assertEqual(data['summary'][version]['missing_pairs'], 54)
        self.assertEqual(data['filtered']['x'][2], 4.25)
        self.assertEqual(data['original']['x'][4], ox[4])
        self.assertEqual(data['gaps'], [{'start': 13.3, 'end': 13.35}])
        gap = self.xy(x='time', y='time', t0=13.31, t1=13.32).json()
        self.assertEqual(gap['summary']['original']['finite_pairs'], 0)
        self.assertTrue(all(value is None for value in gap['original']['x']))
        self.assertEqual(self.fingerprint(), before)

    def test_xy_large_sampling_preserves_native_pairs_across_different_row_groups(self):
        self.fixture(20000, both=True)
        fx = uniform_filter1d(self.values, 11, mode='nearest')
        fy = uniform_filter1d(self.other, 19, mode='nearest')
        self.write_xy(self.values, self.other, fx, fy, row_groups=(311, 509))
        before = self.fingerprint()
        response = self.xy(max_points=100, t0=13.127, t1=31.713)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        lo, hi = store.window_bounds(self.meta, 13.127, 31.713)
        indices = np.arange(lo, hi, int(np.ceil((hi - lo) / 100)))
        self.assertEqual(data['indices'], indices.tolist())
        self.assertEqual(data['mode'], 'sampled')
        self.assertEqual(data['n_raw'], hi - lo)
        self.assertEqual(data['n_sampled'], len(indices))
        self.assertLessEqual(data['n_sampled'], 100)
        np.testing.assert_array_equal(data['t'], self.times[indices])
        for version, xv, yv in [('original', self.values, self.other), ('filtered', fx, fy)]:
            np.testing.assert_array_equal(data[version]['x'], xv[indices])
            np.testing.assert_array_equal(data[version]['y'], yv[indices])
            self.assertEqual(data['summary'][version]['native_finite_pairs'], hi - lo)
        self.assertTrue(any('does not load additional samples' in warning for warning in data['warnings']))
        self.assertEqual(self.xy(max_points=100, t0=13.127, t1=31.713).json(), data)
        self.assertEqual(self.fingerprint(), before)

    def test_xy_sparse_fallback_keeps_common_rows_within_budget_and_other_last_point(self):
        self.fixture(20000)
        empty = np.full(20000, np.nan)
        ox, oy, fx, fy = (empty.copy() for _ in range(4))
        ox[3], oy[3], fx[19800], fy[19800] = 1.2, 2.3, 4.5, 5.6
        self.write_xy(ox, oy, fx, fy)
        data = self.xy(max_points=100).json()
        self.assertEqual(data['n_sampled'], 100)
        self.assertEqual(data['fallback_indices'], [3])
        self.assertIn(19800, data['indices'])
        self.assertEqual(data['indices'], sorted(set(data['indices'])))
        for version in ('original', 'filtered'):
            self.assertEqual(data['summary'][version]['finite_pairs'], 1)
            self.assertEqual(data['summary'][version]['native_finite_pairs'], 1)
        for row, version, x, y in [(3, 'original', 1.2, 2.3), (19800, 'filtered', 4.5, 5.6)]:
            index = data['indices'].index(row)
            self.assertEqual((data[version]['x'][index], data[version]['y'][index]), (x, y))
            other = 'filtered' if version == 'original' else 'original'
            self.assertIsNone(data[other]['x'][index])
            self.assertIsNone(data[other]['y'][index])
        fx[19800] = fy[19800] = np.nan
        fx[5], fy[5] = 6.7, 7.8
        self.write_xy(ox, oy, fx, fy)
        both = self.xy(max_points=100).json()
        self.assertEqual(both['fallback_indices'], [3, 5])
        self.assertEqual(both['n_sampled'], 100)
        self.assertEqual(both['summary']['original']['finite_pairs'], 1)
        self.assertEqual(both['summary']['filtered']['finite_pairs'], 1)

    def test_xy_tiny_coordinate_changes_and_time_steps_keep_float64_precision(self):
        self.fs = 10_000_000.0
        self.fixture()
        tiny = np.arange(1200, dtype=float) * 1e-12
        self.write_xy(tiny, tiny * 2, tiny + 1e-13, tiny * 2 - 1e-14)
        data = self.xy().json()
        np.testing.assert_array_equal(data['t'], self.times)
        self.assertEqual(len(set(data['t'])), 1200)
        np.testing.assert_array_equal(data['original']['x'], tiny)
        np.testing.assert_array_equal(data['filtered']['x'], tiny + 1e-13)
        np.testing.assert_array_equal(data['filtered']['y'], tiny * 2 - 1e-14)

    def test_xy_validation_empty_windows_stale_busy_legacy_and_readonly_identity(self):
        self.fixture()
        before = self.fingerprint()
        for params, code in [({'x': 'missing'}, 400), ({'y': 'missing'}, 400),
                             ({'t0': 14, 't1': 13}, 400), ({'t0': 13, 't1': 13}, 400),
                             ({'t0': 'nan'}, 422), ({'t1': 'inf'}, 422),
                             ({'max_points': 99}, 422), ({'max_points': 12001}, 422),
                             ({'source_id': 'invalid'}, 422), ({'source_id': str(uuid4())}, 409),
                             ({'source_revision': 'old'}, 409)]:
            with self.subTest(params=params):
                self.assertEqual(self.xy(**params).status_code, code)
        for params in ({'t0': 1, 't1': 2}, {'t0': 99, 't1': 100}):
            data = self.xy(**params).json()
            self.assertEqual(data['n_raw'], 0)
            self.assertEqual(data['indices'], [])
            self.assertEqual(data['t'], [])
            self.assertEqual(data['summary']['original']['finite_pairs'], 0)
        self.assertEqual(self.fingerprint(), before)
        store.write_json_atomic(self.directory / 'status.json', {'status': 'rebuilding'})
        with patch.object(preprocess_compare, 'data_read') as lock:
            self.assertEqual(self.xy().status_code, 409)
            lock.assert_not_called()
        store.write_json_atomic(self.directory / 'status.json', {'status': 'ready'})
        meta = store.get_meta(self.name)
        for recipe in (None, {'version': 1, 'filters': [{}]}, {'version': 2, 'mode': 'in_place', 'filters': []}):
            store.write_json_atomic(self.directory / 'meta.json', {**meta, 'preprocessing': recipe})
            self.assertEqual(self.xy().status_code, 409)
        store.write_json_atomic(self.directory / 'meta.json', meta)
        identity = self.directory / analysis_sources.IDENTITY_FILE
        for content in (None, '{broken'):
            if content is None:
                identity.unlink()
            else:
                identity.write_text(content, encoding='utf-8')
            before = self.fingerprint()
            self.assertEqual(self.xy().status_code, 409)
            self.assertEqual(self.fingerprint(), before)

    def test_xy_corrupt_omitted_native_time_and_cross_batch_time_order_fail_closed(self):
        self.fixture(20000)
        original = self.directory / preprocess.ORIGINAL
        # Row 3 is not part of a stride-200 display but must still be aligned.
        corrupt = self.times.copy()
        corrupt[3] += .0001
        pq.write_table(pa.table({'time': corrupt, self.column: self.values, 'other': self.other}),
                       original / 'data.parquet', row_group_size=311)
        before = self.fingerprint()
        self.assertEqual(self.xy(max_points=100).status_code, 409)
        self.assertEqual(self.fingerprint(), before)
        # Equal grids can still be non-monotonic precisely across a batch seam.
        corrupt = self.times.copy()
        corrupt[311:] -= 1
        for directory in (original, self.directory):
            pq.write_table(pa.table({'time': corrupt, self.column: self.values, 'other': self.other}),
                           directory / 'data.parquet', row_group_size=311)
        self.params['source_revision'] = preprocess.get_preprocess(self.name)['source']['revision']
        self.assertEqual(self.xy(max_points=100).status_code, 409)

    def test_xy_missing_corrupt_mismatched_linked_original_and_gap_metadata_fail_closed(self):
        self.fixture()
        original = self.directory / preprocess.ORIGINAL
        path = original / 'data.parquet'
        content = path.read_bytes()
        for problem in ('missing', 'corrupt', 'schema', 'count'):
            with self.subTest(problem=problem):
                if problem == 'missing':
                    path.unlink()
                elif problem == 'corrupt':
                    path.write_bytes(b'corrupt')
                elif problem == 'schema':
                    pq.write_table(pa.table({'time': self.times, self.column: self.values}), path)
                else:
                    pq.write_table(pa.table({'time': self.times[:2], self.column: self.values[:2], 'other': self.other[:2]}), path)
                before = self.fingerprint()
                self.assertEqual(self.xy().status_code, 409)
                self.assertEqual(self.fingerprint(), before)
                path.write_bytes(content)
        with patch.object(preprocess, '_safe_tree', side_effect=ValueError('linked original')):
            self.assertEqual(self.xy().status_code, 409)
        active, baseline = store.get_meta(self.name), store._read_json(original / 'meta.json')
        for directory, metadata in ((self.directory, active), (original, baseline)):
            store.write_json_atomic(directory / 'meta.json', {**metadata, 'time_gap_ranges': [[2, 9000]]})
        self.params['source_revision'] = preprocess.get_preprocess(self.name)['source']['revision']
        self.assertEqual(self.xy().status_code, 409)

    def test_xy_lock_covers_both_readers_and_rechecks_ready_after_wait(self):
        self.fixture()
        held = False
        real_iterator = store._iter_parquet_slice

        @contextmanager
        def lock(name):
            nonlocal held
            self.assertEqual(name, self.name)
            held = True
            try:
                yield
            finally:
                held = False

        def checked_iterator(*args):
            self.assertTrue(held)
            try:
                for entry in real_iterator(*args):
                    self.assertTrue(held)
                    yield entry
            finally:
                self.assertTrue(held)

        with patch.object(preprocess_compare, 'data_read', lock), patch.object(
                store, '_iter_parquet_slice', side_effect=checked_iterator):
            self.assertEqual(self.xy().status_code, 200)
        first = preprocess_compare._available(self.name)
        with patch.object(preprocess_compare, '_available', side_effect=[first, HTTPException(409, 'busy')]):
            self.assertEqual(self.xy().status_code, 409)

    def test_native_pairs_match_independent_filter_and_exact_difference(self):
        self.fixture()
        before = self.fingerprint()
        response = self.get()
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        expected = uniform_filter1d(self.values, 11, mode='nearest')
        np.testing.assert_array_equal(data['t'], self.times)
        np.testing.assert_array_equal(data['original'], self.values)
        np.testing.assert_allclose(data['filtered'], expected, atol=1e-14)
        delta = expected - self.values
        self.assertEqual(data['summary']['finite_pairs'], 1200)
        self.assertEqual(data['summary']['missing_pairs'], 0)
        self.assertAlmostEqual(data['summary']['rms_difference'], np.sqrt(np.mean(delta ** 2)))
        self.assertAlmostEqual(data['summary']['max_abs_difference'], np.max(np.abs(delta)))
        self.assertEqual(data['range'], {'start': self.start, 'end': self.start + 1.2})
        self.assertEqual(data['source'], self.snapshot['source'])
        self.assertEqual(data['mode'], 'raw')
        self.assertEqual(self.fingerprint(), before)
        self.assertEqual([entry['name'] for entry in store.list_tests()], [self.name])

    def test_native_window_uses_same_rows_nonzero_time_and_unmodified_parameter(self):
        self.fixture()
        response = self.get(column='other', t0=13.2, t1=13.5).json()
        lo, hi = store.window_bounds(self.meta, 13.2, 13.5)
        self.assertEqual((response['i0'], response['i1']), (lo, hi))
        np.testing.assert_array_equal(response['t'], self.times[lo:hi])
        np.testing.assert_array_equal(response['original'], self.other[lo:hi])
        self.assertEqual(response['original'], response['filtered'])
        self.assertEqual(response['summary']['rms_difference'], 0)
        self.assertEqual(response['summary']['max_abs_difference'], 0)

    def test_nonfinite_and_known_previously_filled_gaps_remain_unavailable(self):
        self.fixture(gaps=True)
        data = self.get().json()
        self.assertEqual(data['gaps'], [{'start': 13.3, 'end': 13.35}])
        for key in ('original', 'filtered'):
            self.assertIsNone(data[key][33])
            self.assertIsNone(data[key][61])
            self.assertEqual(data[key][300:350], [None] * 50)
        self.assertEqual(data['summary']['missing_pairs'], 52)
        self.assertEqual(data['summary']['finite_pairs'], 1148)
        orig = np.array(data['original'], dtype=float)
        filt = np.array(data['filtered'], dtype=float)
        delta = (filt - orig)[np.isfinite(orig) & np.isfinite(filt)]
        self.assertAlmostEqual(data['summary']['rms_difference'], np.sqrt(np.mean(delta ** 2)))
        all_gap = self.get(t0=13.31, t1=13.32).json()
        self.assertEqual(all_gap['summary']['finite_pairs'], 0)
        self.assertIsNone(all_gap['summary']['rms_difference'])
        self.assertIsNone(all_gap['summary']['max_abs_difference'])

    def test_large_window_uses_paired_peak_preserving_pyramids_and_zoom_is_native(self):
        self.fixture(20000)
        before = self.fingerprint()
        with patch.object(preprocess_compare, '_raw', wraps=preprocess_compare._raw) as raw:
            result = self.get(px=100).json()
        self.assertEqual(result['mode'], 'envelope')
        self.assertIsNone(result['summary'])
        self.assertLessEqual(len(result['t']), 1000)
        self.assertEqual(result['level'], 256)
        self.assertLessEqual(raw.call_count, 2)  # Only final partial buckets, never broad native scans.
        expected = uniform_filter1d(self.values, 11, mode='nearest')
        for key, values in [('original', self.values), ('filtered', expected)]:
            independent = [values[i:i + 256] for i in range(0, len(values), 256)]
            np.testing.assert_allclose(result[key]['min'], [np.min(v) for v in independent], atol=1e-14)
            np.testing.assert_allclose(result[key]['max'], [np.max(v) for v in independent], atol=1e-14)
        zoom = self.get(t0=15.001, t1=15.15).json()
        self.assertEqual(zoom['mode'], 'raw')
        np.testing.assert_array_equal(zoom['t'], self.times[zoom['i0']:zoom['i1']])
        np.testing.assert_array_equal(zoom['original'], self.values[zoom['i0']:zoom['i1']])
        self.assertEqual(self.fingerprint(), before)

    def test_envelope_edge_buckets_do_not_include_outside_window_extrema(self):
        self.fixture(20000)
        data = self.get(t0=13.127, t1=27.153, px=100).json()
        self.assertEqual(data['mode'], 'envelope')
        level = data['level']
        lo, hi = data['i0'], data['i1']
        independent = [self.values[max(lo, row):min(hi, row + level)]
                       for row in range(lo // level * level, hi, level)]
        np.testing.assert_allclose(data['original']['min'], [np.min(v) for v in independent])
        np.testing.assert_allclose(data['original']['max'], [np.max(v) for v in independent])
        self.assertEqual(data['t'][0], self.times[lo])

    def test_envelope_gap_buckets_masked_and_merge_preserves_extrema(self):
        self.fixture(20000, gaps=True)
        data = self.get(px=100).json()
        self.assertIsNone(data['original']['min'][1])
        self.assertIsNone(data['filtered']['max'][1])
        self.assertTrue(any('acquisition gaps' in warning for warning in data['warnings']))
        # Force the ordinary merge-over-cap branch on a moderate fixture.
        with patch.object(store, 'plot_budget', return_value=7):
            merged = self.get().json()
        self.assertLessEqual(len(merged['t']), 7)
        self.assertEqual(merged['level'], 4096)
        self.assertIsNone(merged['original']['min'][0])
        with patch.object(store, 'plot_budget', return_value=2):
            reduced = self.get().json()
        self.assertEqual(reduced['level'], 12288)
        self.assertEqual(len(reduced['t']), 2)
        self.assertIsNone(reduced['original']['min'][0])
        self.assertAlmostEqual(reduced['original']['max'][1], np.max(self.values[12288:]), places=6)

    def test_invalid_and_outside_bounds_columns_and_query_validation(self):
        self.fixture()
        before = self.fingerprint()
        for kwargs, code in [({'t0': 14, 't1': 13}, 400), ({'t0': 13, 't1': 13}, 400),
                             ({'column': 'time'}, 400), ({'column': 'missing'}, 400),
                             ({'t0': 'nan'}, 422), ({'t1': 'inf'}, 422),
                             ({'px': 1}, 422), ({'px': 4001}, 422),
                             ({'source_id': 'invalid'}, 422)]:
            with self.subTest(kwargs=kwargs):
                self.assertEqual(self.get(**kwargs).status_code, code)
        for kwargs in ({'t0': 1, 't1': 2}, {'t0': 99, 't1': 100}):
            data = self.get(**kwargs).json()
            self.assertEqual(data['n_raw'], 0)
            self.assertEqual(data['t'], [])
            self.assertEqual(data['summary']['finite_pairs'], 0)
        self.assertEqual(self.fingerprint(), before)

    def test_stale_identity_revision_busy_legacy_and_missing_original_rejected(self):
        self.fixture()
        self.assertEqual(self.get(source_id=str(uuid4())).status_code, 409)
        self.assertEqual(self.get(source_revision='old').status_code, 409)
        store.write_json_atomic(self.directory / 'status.json', {'status': 'rebuilding'})
        with patch.object(preprocess_compare, 'data_read') as lock:
            self.assertEqual(self.get().status_code, 409)
            lock.assert_not_called()
        store.write_json_atomic(self.directory / 'status.json', {'status': 'ready'})
        meta = store.get_meta(self.name)
        for recipe in (None, {'version': 1, 'filters': [{}]}, {'version': 2, 'mode': 'in_place', 'filters': []}):
            store.write_json_atomic(self.directory / 'meta.json', {**meta, 'preprocessing': recipe})
            self.assertEqual(self.get().status_code, 409)
        store.write_json_atomic(self.directory / 'meta.json', meta)
        (self.directory / preprocess.ORIGINAL / 'data.parquet').unlink()
        before = self.fingerprint()
        self.assertEqual(self.get().status_code, 409)
        self.assertEqual(self.fingerprint(), before)

    def test_missing_identity_is_not_migrated_or_repaired(self):
        self.fixture()
        identity = self.directory / analysis_sources.IDENTITY_FILE
        identity.unlink()
        before = self.fingerprint()
        self.assertEqual(self.get().status_code, 409)
        self.assertFalse(identity.exists())
        self.assertEqual(self.fingerprint(), before)
        identity.write_text('{broken', encoding='utf-8')
        before = self.fingerprint()
        self.assertEqual(self.get().status_code, 409)
        self.assertEqual(self.fingerprint(), before)

    def test_mismatched_times_and_corrupt_pyramids_are_not_presented_as_pairs(self):
        self.fixture(20000)
        original = self.directory / preprocess.ORIGINAL
        frame = pl.read_parquet(original / 'data.parquet')
        frame.with_columns((pl.col('time') + .1).alias('time')).write_parquet(original / 'data.parquet')
        self.assertEqual(self.get(t0=13, t1=13.1).status_code, 409)
        (original / 'pyramid' / 'L16.parquet').write_bytes(b'corrupt')
        self.assertEqual(self.get().status_code, 409)
        baseline = store._read_json(original / 'meta.json')
        store.write_json_atomic(original / 'meta.json', {**baseline, 'n_rows': 1})
        self.assertEqual(self.get().status_code, 409)

    def test_links_rejected_without_accessing_linked_native_data(self):
        self.fixture(20000)
        before = self.fingerprint()
        original_check = preprocess_compare.is_link_or_junction
        paths = [self.directory / 'status.json', self.directory / 'pyramid',
                 self.directory / 'pyramid' / 'L16.parquet']
        for linked in paths:
            with self.subTest(path=linked), patch.object(preprocess_compare, 'is_link_or_junction',
                    side_effect=lambda path: path == linked or original_check(path)):
                self.assertEqual(self.get().status_code, 409)
        with patch.object(preprocess, '_safe_tree', side_effect=ValueError('linked original')):
            self.assertEqual(self.get().status_code, 409)
        self.assertEqual(self.fingerprint(), before)

    def test_lock_covers_both_sources_and_rechecks_readiness_after_acquisition(self):
        self.fixture()
        active = False
        original_raw = preprocess_compare._raw

        @contextmanager
        def lock(name):
            nonlocal active
            self.assertEqual(name, self.name)
            active = True
            try:
                yield
            finally:
                active = False

        def checked_read(*args):
            self.assertTrue(active)
            return original_raw(*args)

        with patch.object(preprocess_compare, 'data_read', lock), patch.object(
                preprocess_compare, '_raw', side_effect=checked_read):
            self.assertEqual(self.get().status_code, 200)
        initial = preprocess_compare._available(self.name)
        with patch.object(preprocess_compare, '_available', side_effect=[initial, HTTPException(409, 'busy')]):
            self.assertEqual(self.get().status_code, 409)

    def test_large_finite_differences_do_not_overflow_rms_and_unrepresentable_is_null(self):
        data = preprocess_compare._summary(np.array([0., 0.]), np.array([1e200, -1e200]))
        self.assertEqual(data['rms_difference'], 1e200)
        self.assertEqual(data['max_abs_difference'], 1e200)
        data = preprocess_compare._summary(np.array([-1e308]), np.array([1e308]))
        self.assertEqual(data['finite_pairs'], 1)
        self.assertIsNone(data['rms_difference'])
        self.assertIsNone(data['max_abs_difference'])
        self.fixture()
        for directory, value in [(self.directory / preprocess.ORIGINAL, -1e308),
                                 (self.directory, 1e308)]:
            pq.write_table(pa.table({'time': self.times, self.column: np.full(1200, value),
                                    'other': self.other}), directory / 'data.parquet')
        self.params.update(source_revision=preprocess.get_preprocess(self.name)['source']['revision'])
        result = self.get()
        self.assertEqual(result.status_code, 200, result.text)
        data = result.json()
        self.assertEqual(data['summary']['finite_pairs'], 1200)
        self.assertIsNone(data['summary']['rms_difference'])
        self.assertIsNone(data['summary']['max_abs_difference'])
        self.assertEqual(data['original'][0], -1e308)
        self.assertEqual(data['filtered'][0], 1e308)
        self.assertTrue(any('exceed the supported numeric range' in message for message in data['warnings']))

    def test_tiny_native_changes_and_submicrosecond_times_keep_full_precision(self):
        self.fs = 10_000_000.0
        self.fixture()
        original = self.directory / preprocess.ORIGINAL
        tiny = np.arange(1200, dtype=float) * 1e-12
        for directory, values in [(original, tiny), (self.directory, tiny + 1e-13)]:
            pq.write_table(pa.table({'time': self.times, self.column: values, 'other': self.other}),
                           directory / 'data.parquet')
        self.params.update(source_revision=preprocess.get_preprocess(self.name)['source']['revision'])
        data = self.get().json()
        np.testing.assert_array_equal(data['t'], self.times)
        self.assertEqual(len(set(data['t'])), 1200)
        np.testing.assert_array_equal(data['original'], tiny)
        np.testing.assert_array_equal(data['filtered'], tiny + 1e-13)
        self.assertGreater(data['summary']['max_abs_difference'], 0)
        np.testing.assert_allclose(np.array(data['filtered']) - data['original'], 1e-13, rtol=1e-11)

    def test_permanent_status_permission_error_is_actionable(self):
        self.fixture()
        with patch.object(store, 'get_status', side_effect=PermissionError('denied')):
            result = self.get()
        self.assertEqual(result.status_code, 409)
