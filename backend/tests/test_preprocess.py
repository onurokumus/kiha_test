"""Saved native filtering, preservation, lifecycle, and independent DSP oracles."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import shutil
from types import SimpleNamespace
from threading import Event
from unittest.mock import patch
from uuid import uuid4

from fastapi import BackgroundTasks, HTTPException
from fastapi.testclient import TestClient
import numpy as np
import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
from scipy import signal
from scipy.ndimage import uniform_filter1d

from app import analysis_metadata, analysis_sources, dsp, main, preprocess, store
from ._base import DataDirTestCase


class PreprocessTests(DataDirTestCase):
    def setUp(self):
        super().setUp()
        self.client = TestClient(main.app)
        self.directory = self.tests / 'original'
        self.directory.mkdir()
        self.fs = 1000.0
        self.count = 1200
        self.times = 10 + np.arange(self.count) / self.fs
        self.values = np.sin(2 * np.pi * 10 * self.times) + .4 * np.sin(2 * np.pi * 200 * self.times)
        self.other = np.arange(self.count) * 0.123456789123
        self.write_data()
        self.meta = dict(name='original', columns=['time', 'signal', 'other'],
                         time_column='time', fs_hz=self.fs, n_rows=self.count,
                         n_columns=3, t_start=10., duration_s=self.count / self.fs,
                         time_gap_ranges=[], acquisition_gap_ranges=[],
                         source_file='uploaded.csv', notes='Original notes',
                         derived_variables=[])
        store.write_json_atomic(self.directory / 'meta.json', self.meta)
        store.write_json_atomic(self.directory / 'status.json', {'status': 'ready'})
        store.write_json_atomic(self.directory / 'testpoints.json', {'version': 1,
            'test': 'original', 'fs_hz': self.fs, 'test_points': [
            {'id': 7, 'name': 'Saved point', 'start_idx': 101, 'end_idx': 701,
             'start_s': 10.101, 'end_s': 10.701}]})
        (self.directory / 'raw.csv').write_text('uploaded original bytes\r\n', encoding='utf-8')
        self.snapshot = preprocess.get_preprocess('original')

    def write_data(self):
        pq.write_table(pa.table({'time': self.times, 'signal': self.values, 'other': self.other}),
                       self.directory / 'data.parquet', row_group_size=311)

    def request(self, name='processed', **settings):
        return dict(name=name, source_id=self.snapshot['source']['id'],
                    source_revision=self.snapshot['source']['revision'],
                    filters=[{'column': 'signal', 'filter': {'kind': 'moving_avg',
                                                            'window_s': .011, **settings}}])

    def reserve(self, request=None):
        background = BackgroundTasks()
        result = preprocess.create_preprocess('original',
            preprocess.PreprocessRequest(**(request or self.request())), background)
        self.assertEqual(result['status'], 'rebuilding')
        return background

    def build(self, request=None):
        request = request or self.request()
        asyncio.run(self.reserve(request)())
        self.assertEqual(store.get_status(request['name'])['status'], 'ready',
                         store.get_status(request['name']))
        return pl.read_parquet(self.tests / request['name'] / 'data.parquet')

    def fingerprint(self):
        return {str(p.relative_to(self.directory)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.directory.rglob('*') if p.is_file()}

    def test_short_record_with_saved_empty_points_opens_and_filters_whole_record(self):
        # Supported point imports can retain markers at/past the last sample.
        # Whole-record filtering must not require resolving these point ranges.
        self.fs, self.count = 1., 15
        self.times = np.arange(self.count, dtype=float)
        self.values = np.sin(self.times)
        self.other = self.times * 2
        self.write_data()
        self.meta.update(fs_hz=self.fs, n_rows=self.count, t_start=0., duration_s=15.)
        store.write_json_atomic(self.directory / 'meta.json', self.meta)
        expected_source = self.client.get('/api/tests/original/preprocess').json()['source']
        points = [
            {'id': 1, 'name': 'End marker', 'start_s': 15, 'end_s': 15,
             'start_idx': 15, 'end_idx': 15},
            {'id': 2, 'name': 'Old range', 'start_s': 10, 'end_s': 4,
             'start_idx': 10, 'end_idx': 4},
            {'id': 3, 'name': 'Past recording', 'start_s': 20, 'end_s': 25},
        ]
        result = self.client.put('/api/tests/original/testpoints', json={
            'test': 'original', 'fs_hz': self.fs, 'test_points': points})
        self.assertEqual(result.status_code, 200, result.text)
        saved_points = store.read_testpoints('original')['test_points']
        before = self.fingerprint()
        response = self.client.get('/api/tests/original/preprocess')
        self.assertEqual(response.status_code, 200, response.text)
        self.snapshot = response.json()
        self.assertEqual(self.snapshot['source'], expected_source)
        out = self.build(self.request(window_s=3.))
        np.testing.assert_allclose(out['signal'].to_numpy(),
                                   uniform_filter1d(self.values, 3, mode='nearest'))
        np.testing.assert_array_equal(out['time'].to_numpy(), self.times)
        self.assertEqual(store.read_testpoints('processed')['test_points'], saved_points)
        self.assertEqual(self.fingerprint(), before)
        # Session recovery still refuses points it cannot resolve safely.
        catalog = self.client.get('/api/analysis-sources').json()['sources']
        self.assertTrue(all(entry.get('error') for entry in catalog))

    def test_damaged_identity_is_an_actionable_conflict_for_get_and_post(self):
        path = self.directory / analysis_sources.IDENTITY_FILE
        path.write_text('damaged identity', encoding='utf-8')
        before = self.fingerprint()
        for method in ('GET', 'POST'):
            with self.subTest(method=method):
                response = self.client.request(method, '/api/tests/original/preprocess',
                    **({'json': self.request()} if method == 'POST' else {}))
                self.assertEqual(response.status_code, 409, response.text)
                self.assertIn('source identity', response.json()['detail'])
                self.assertIn('backend log', response.json()['detail'])
        self.assertEqual(self.fingerprint(), before)
        self.assertFalse((self.tests / 'processed').exists())

    def test_legacy_identity_permission_failure_explains_service_account_access(self):
        path = self.directory / analysis_sources.IDENTITY_FILE
        path.unlink()
        before = self.fingerprint()
        with patch.object(store, 'write_json_atomic', side_effect=PermissionError('read only')):
            response = self.client.get('/api/tests/original/preprocess')
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn('service account', response.json()['detail'])
        self.assertIn('permissions', response.json()['detail'])
        self.assertEqual(self.fingerprint(), before)
        self.assertFalse(path.exists())

    def test_unreadable_source_metadata_explains_access_and_preserves_source(self):
        before = self.fingerprint()
        with patch.object(store, 'get_meta', side_effect=PermissionError('read only')):
            response = self.client.get('/api/tests/original/preprocess')
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn('permissions', response.json()['detail'])
        self.assertEqual(self.fingerprint(), before)

    def test_missing_samples_or_damaged_metadata_do_not_create_legacy_identity(self):
        identity = self.directory / analysis_sources.IDENTITY_FILE
        identity.unlink()
        data = (self.directory / 'data.parquet').read_bytes()
        for failure in ('missing_samples', 'invalid_json', 'invalid_revision'):
            with self.subTest(failure=failure):
                (self.directory / 'data.parquet').write_bytes(data)
                store.write_json_atomic(self.directory / 'meta.json', self.meta)
                if failure == 'missing_samples':
                    (self.directory / 'data.parquet').unlink()
                elif failure == 'invalid_json':
                    (self.directory / 'meta.json').write_text('broken metadata', encoding='utf-8')
                else:
                    store.write_json_atomic(self.directory / 'meta.json', {**self.meta, 'fs_hz': float('nan')})
                before = self.fingerprint()
                response = self.client.get('/api/tests/original/preprocess')
                self.assertEqual(response.status_code, 409, response.text)
                self.assertIn('metadata', response.json()['detail'])
                self.assertFalse(identity.exists())
                self.assertEqual(self.fingerprint(), before)

    def test_native_copy_preserves_every_source_byte_time_other_parameter_and_saved_rows(self):
        before = self.fingerprint()
        out = self.build()
        expected = uniform_filter1d(self.values, size=11, mode='nearest')
        np.testing.assert_array_equal(out['time'].to_numpy(), self.times)
        np.testing.assert_array_equal(out['other'].to_numpy(), self.other)
        np.testing.assert_allclose(out['signal'].to_numpy(), expected, rtol=0, atol=1e-14)
        self.assertEqual(self.fingerprint(), before)
        points = store.read_testpoints('processed')
        self.assertEqual(points['test'], 'processed')
        self.assertEqual(points['test_points'], store.read_testpoints('original')['test_points'])
        self.assertNotEqual(analysis_sources.read_identity(self.tests / 'processed'),
                            self.snapshot['source']['id'])
        meta = store.get_meta('processed')
        self.assertEqual(meta['preprocessing']['source'], self.snapshot['source'])
        self.assertEqual(meta['notes'], 'Original notes')
        self.assertEqual(meta['n_rows'], self.count)
        self.assertTrue(meta['preprocessing']['original_raw_available'])
        self.assertEqual((self.tests / 'processed' / 'raw.csv').read_bytes(),
                         (self.directory / 'raw.csv').read_bytes())
        self.assertTrue((self.tests / 'processed' / 'pyramid' / 'L16.parquet').is_file())
        self.assertFalse((self.tests / 'processed' / '.preprocess').exists())

    def test_butterworth_and_detrend_match_independent_whole_record_scipy_oracles(self):
        for kind, band in [('lowpass', 30), ('highpass', 30),
                           ('bandpass', [5, 30]), ('bandstop', [5, 30]), ('detrend', None)]:
            with self.subTest(kind=kind):
                settings = dict(kind=kind, order=3, f1=5 if isinstance(band, list) else band,
                                f2=30 if isinstance(band, list) else None)
                out = self.build(self.request(name=kind, **settings))
                expected = (signal.detrend(self.values) if kind == 'detrend' else
                            signal.sosfiltfilt(signal.butter(3, band, btype=kind,
                                               fs=self.fs, output='sos'), self.values))
                np.testing.assert_allclose(out['signal'].to_numpy(), expected, rtol=0, atol=1e-14)

    def test_despike_preserves_plateau_and_replaces_only_short_spikes(self):
        self.values[:] = 20
        self.values[100:103] = 400
        self.values[600:800] = 200
        self.write_data()
        self.snapshot = preprocess.get_preprocess('original')
        out = self.build(self.request(kind='despike', window_s=.025, max_spike_s=.005,
                                      threshold=3.5, abs_floor=10))
        np.testing.assert_array_equal(out['signal'].to_numpy()[100:103], [20] * 3)
        np.testing.assert_array_equal(out['signal'].to_numpy()[600:800], [200] * 200)

    def test_missing_samples_and_acquisition_boundaries_are_preserved(self):
        self.values[:500] = 10
        self.values[500:550] = np.nan
        self.values[550:] = 1000
        self.values[99] = np.nan
        self.values[800] = np.inf
        self.write_data()
        self.meta['time_gap_ranges'] = [[500, 550]]
        store.write_json_atomic(self.directory / 'meta.json', self.meta)
        self.snapshot = preprocess.get_preprocess('original')
        out = self.build()['signal'].to_numpy()
        self.assertTrue(np.isnan(out[500:550]).all())
        self.assertTrue(np.isnan(out[99]))
        self.assertTrue(np.isposinf(out[800]))
        self.assertEqual(out[499], 10)
        self.assertEqual(out[550], 1000)
        self.assertEqual(len(store.get_meta('processed')['preprocessing']['warnings']), 2)

    def test_arrow_nulls_stay_null_in_filtered_and_unfiltered_columns(self):
        values = self.values.tolist()
        values[10] = None
        other = self.other.tolist()
        other[20] = None
        pq.write_table(pa.table({'time': self.times, 'signal': values, 'other': other}),
                       self.directory / 'data.parquet')
        self.snapshot = preprocess.get_preprocess('original')
        out = self.build()
        self.assertIsNone(out['signal'][10])
        self.assertIsNone(out['other'][20])
        self.assertEqual(out['signal'].null_count(), 1)

    def test_previously_filled_acquisition_gaps_remain_hard_boundaries(self):
        self.values[:500] = 10
        self.values[500:550] = 0
        self.values[550:] = 1000
        self.write_data()
        self.meta.update(time_gap_ranges=[], acquisition_gap_ranges=[[500, 550]],
                         nan_policy='zero_fill')
        store.write_json_atomic(self.directory / 'meta.json', self.meta)
        self.snapshot = preprocess.get_preprocess('original')
        out = self.build()['signal'].to_numpy()
        np.testing.assert_array_equal(out[:500], [10] * 500)
        np.testing.assert_array_equal(out[500:550], [0] * 50)
        np.testing.assert_array_equal(out[550:], [1000] * (self.count - 550))
        self.assertIn('acquisition gap', store.get_meta('processed')['preprocessing']['warnings'][0])

    def test_filters_multiple_columns_without_row_group_seams(self):
        # More than two output row groups; source row groups deliberately differ.
        self.count = 2 * preprocess.ROW_GROUP_SIZE + 53
        self.times = 10 + np.arange(self.count) / self.fs
        self.values = np.sin(np.arange(self.count) / 10)
        self.other = np.cos(np.arange(self.count) / 17)
        self.write_data()
        self.meta.update(n_rows=self.count, duration_s=self.count / self.fs)
        store.write_json_atomic(self.directory / 'meta.json', self.meta)
        self.snapshot = preprocess.get_preprocess('original')
        req = self.request()
        req['filters'].append({'column': 'other', 'filter': {'kind': 'detrend'}})
        out = self.build(req)
        self.assertEqual(len(out), self.count)
        np.testing.assert_array_equal(out['time'].to_numpy(), self.times)
        np.testing.assert_allclose(out['signal'].to_numpy(), uniform_filter1d(self.values, 11, mode='nearest'))
        np.testing.assert_allclose(out['other'].to_numpy(), signal.detrend(self.other))

    def test_original_and_processed_csv_remain_available_after_original_deletion(self):
        self.build()
        raw = self.client.get('/api/tests/processed/raw')
        self.assertEqual(raw.status_code, 200)
        self.assertEqual(raw.content, (self.directory / 'raw.csv').read_bytes())
        shutil.rmtree(self.directory)
        self.assertEqual(self.client.get('/api/tests/processed/raw').content, raw.content)
        exported = self.client.get('/api/tests/processed/export')
        self.assertEqual(exported.status_code, 200)
        self.assertEqual(len(exported.text.splitlines()), self.count + 1)

    def test_parameter_and_name_validation_reserves_nothing(self):
        cases = [([], 422),
            ([{'column': 'time', 'filter': {'kind': 'detrend'}}], 400),
            ([{'column': 'absent', 'filter': {'kind': 'detrend'}}], 400),
            ([{'column': 'signal', 'filter': {'kind': 'lowpass', 'f1': 500}}], 400),
            ([{'column': 'signal', 'filter': {'kind': 'moving_avg'}}], 400),
            ([{'column': 'signal', 'filter': {'kind': 'despike', 'window_s': .01, 'max_spike_s': .01}}], 400),
            ([{'column': 'signal', 'filter': {'kind': 'lowpass', 'f1': 20, 'order': 11}}], 422)]
        for filters, status in cases:
            with self.subTest(filters=filters):
                req = self.request()
                req['filters'] = filters
                response = self.client.post('/api/tests/original/preprocess', json=req)
                self.assertEqual(response.status_code, status, response.text)
                self.assertFalse((self.tests / 'processed').exists())
        req = self.request()
        req['filters'] *= 2
        self.assertEqual(self.client.post('/api/tests/original/preprocess', json=req).status_code, 400)
        for name in ('../escape', 'NUL', 'trailing.', '---'):
            with self.subTest(name=name):
                self.assertEqual(self.client.post('/api/tests/original/preprocess',
                    json=self.request(name=name)).status_code, 400)

    def test_stale_identity_or_revision_and_processed_sources_are_rejected(self):
        for field, value in [('source_id', str(uuid4())), ('source_revision', 'obsolete')]:
            req = self.request()
            req[field] = value
            self.assertEqual(self.client.post('/api/tests/original/preprocess', json=req).status_code, 409)
        self.build()
        snapshot = preprocess.get_preprocess('processed')
        req = self.request(name='second')
        req.update(source_id=snapshot['source']['id'], source_revision=snapshot['source']['revision'])
        response = self.client.post('/api/tests/processed/preprocess', json=req)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertFalse((self.tests / 'second').exists())

    def test_duplicate_destination_rejected_before_acquiring_source_writer(self):
        self.reserve()
        with patch.object(preprocess, 'test_read', side_effect=AssertionError('must reject before locks')):
            response = self.client.post('/api/tests/original/preprocess', json=self.request())
            self.assertEqual(response.status_code, 409)

    def test_another_copy_can_be_reserved_while_source_is_being_read(self):
        ready, release = Event(), Event()

        def hold_source():
            with preprocess.data_read('original'):
                ready.set()
                release.wait(5)

        with ThreadPoolExecutor(max_workers=2) as workers:
            holder = workers.submit(hold_source)
            self.assertTrue(ready.wait(2))
            reservation = workers.submit(self.reserve)
            try:
                background = reservation.result(timeout=2)
            finally:
                release.set()
            holder.result(timeout=2)
        asyncio.run(background())
        self.assertEqual(store.get_status('processed')['status'], 'ready')

    def test_failure_and_restart_keep_source_untouched_and_no_ready_partial_copy(self):
        before = self.fingerprint()
        with patch.object(preprocess, 'build_pyramid', side_effect=OSError('disk is full')):
            asyncio.run(self.reserve()())
        status = store.get_status('processed')
        self.assertEqual(status['status'], 'error')
        self.assertIn('original test is unchanged', status['error'])
        self.assertFalse((self.tests / 'processed' / 'meta.json').exists())
        self.assertFalse((self.tests / 'processed' / '.preprocess').exists())
        self.assertEqual(self.fingerprint(), before)
        self.reserve(self.request(name='interrupted'))
        main._recover_interrupted_ingests()
        interrupted = store.get_status('interrupted')
        self.assertEqual(interrupted['status'], 'error')
        self.assertIn('original test is unchanged', interrupted['error'])
        self.assertEqual(self.fingerprint(), before)

    def test_worker_rechecks_source_before_writing_samples(self):
        background = self.reserve()
        self.values[:] = 99
        self.write_data()
        asyncio.run(background())
        self.assertEqual(store.get_status('processed')['status'], 'error')
        self.assertIn('Source changed', store.get_status('processed')['error'])
        self.assertFalse((self.tests / 'processed' / 'data.parquet').exists())

    def test_short_continuous_regions_fail_instead_of_saving_partial_filter(self):
        self.values[1:20] = np.nan
        self.write_data()
        self.meta['time_gap_ranges'] = [[1, 20]]
        store.write_json_atomic(self.directory / 'meta.json', self.meta)
        self.snapshot = preprocess.get_preprocess('original')
        asyncio.run(self.reserve()())
        self.assertEqual(store.get_status('processed')['status'], 'error')
        self.assertIn('fewer than two finite', store.get_status('processed')['error'])

    def test_disk_and_whole_record_budget_fail_before_reservation(self):
        with patch.object(preprocess.shutil, 'disk_usage', return_value=SimpleNamespace(free=0)):
            response = self.client.post('/api/tests/original/preprocess', json=self.request())
            self.assertEqual(response.status_code, 507)
        with patch.object(preprocess, 'MAX_FILTER_SAMPLES', 100):
            response = self.client.post('/api/tests/original/preprocess', json=self.request())
            self.assertEqual(response.status_code, 400)
        self.assertFalse((self.tests / 'processed').exists())

    def test_saved_processing_provenance_and_additional_plot_filter_are_independent(self):
        out = self.build()
        before = (self.tests / 'processed' / 'data.parquet').read_bytes()
        processed = dsp.filtered_samples('processed', ['signal'], 'moving_avg', None, None,
                                         px=1000, window_s=.021)
        expected = uniform_filter1d(out['signal'].to_numpy(), size=21, mode='nearest')
        np.testing.assert_allclose(processed.filtered['signal'], expected)
        self.assertEqual((self.tests / 'processed' / 'data.parquet').read_bytes(), before)
        record = analysis_metadata.source_context('processed', store.get_meta('processed'),
                                                   ['signal'], 0, self.count)
        self.assertEqual(record['source']['preprocessing']['source'], self.snapshot['source'])
        self.assertEqual(store.list_tests()[1]['preprocessing']['method'], 'whole_native_recording')

    def test_http_accepts_async_copy_and_busy_copy_cannot_be_renamed(self):
        with patch.object(preprocess, 'build_copy') as worker:
            response = self.client.post('/api/tests/original/preprocess', json=self.request())
            self.assertEqual(response.status_code, 202, response.text)
            worker.assert_called_once()
        response = self.client.post('/api/tests/processed/rename?new_name=renamed')
        self.assertEqual(response.status_code, 409, response.text)
