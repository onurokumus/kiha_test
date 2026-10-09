"""Saved native filtering, preservation, lifecycle, and independent DSP oracles."""
import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi import BackgroundTasks
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

    def request(self, **settings):
        return dict(request_id=str(uuid4()), source_id=self.snapshot['source']['id'],
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
        self.assertEqual(store.get_status('original')['status'], 'ready',
                         store.get_status('original'))
        self.assertEqual(store.get_status('original')['preprocessing_operation']['state'], 'completed')
        return pl.read_parquet(self.directory / 'data.parquet')

    def fingerprint(self):
        return {str(p.relative_to(self.directory)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.directory.rglob('*') if p.is_file()}

    def refresh(self):
        self.snapshot = preprocess.get_preprocess('original')

    def assert_failed(self, expected):
        status = store.get_status('original')
        self.assertEqual(status['status'], 'ready', status)
        self.assertEqual(status['preprocessing_operation']['state'], 'failed', status)
        self.assertIn(expected, status['preprocessing_operation']['error'])

    def test_same_name_identity_and_catalog_preserve_original_and_metadata(self):
        before = self.fingerprint()
        out = self.build()
        np.testing.assert_allclose(out['signal'].to_numpy(), uniform_filter1d(self.values, 11, mode='nearest'))
        np.testing.assert_array_equal(out['time'].to_numpy(), self.times)
        np.testing.assert_array_equal(out['other'].to_numpy(), self.other)
        self.assertEqual([row['name'] for row in store.list_tests()], ['original'])
        self.assertEqual(analysis_sources.read_identity(self.directory), self.snapshot['source']['id'])
        original = self.directory / preprocess.ORIGINAL
        for artifact in ('data.parquet', 'meta.json'):
            self.assertEqual(hashlib.sha256((original / artifact).read_bytes()).hexdigest(), before[artifact])
        for artifact in ('raw.csv', 'testpoints.json', analysis_sources.IDENTITY_FILE):
            self.assertEqual(self.fingerprint()[artifact], before[artifact])
        meta = store.get_meta('original')
        self.assertEqual(meta['notes'], 'Original notes')
        self.assertEqual(meta['preprocessing']['source'], self.snapshot['source'])
        self.assertEqual(meta['preprocessing']['mode'], 'in_place')
        self.assertFalse((self.directory / preprocess.WORK).exists())

    def test_reapply_replaces_recipe_from_original_and_clear_restores_bytes(self):
        initial_data = (self.directory / 'data.parquet').read_bytes()
        self.build()
        self.refresh()
        req = self.request(window_s=.031)
        out = self.build(req)
        np.testing.assert_allclose(out['signal'].to_numpy(), uniform_filter1d(self.values, 31, mode='nearest'))
        self.refresh()
        req = self.request()
        req['filters'] = [{'column': 'other', 'filter': {'kind': 'detrend'}}]
        out = self.build(req)
        np.testing.assert_array_equal(out['signal'].to_numpy(), self.values)
        np.testing.assert_allclose(out['other'].to_numpy(), signal.detrend(self.other))
        meta = store.get_meta('original')
        meta.update(notes='New findings', component_rpm_column='signal', components_revision=3)
        store.write_json_atomic(self.directory / 'meta.json', meta)
        self.refresh()
        req = self.request()
        req['filters'] = []
        self.build(req)
        self.assertEqual((self.directory / 'data.parquet').read_bytes(), initial_data)
        self.assertNotIn('preprocessing', store.get_meta('original'))
        self.assertEqual(store.get_meta('original')['notes'], 'New findings')
        self.assertEqual(store.get_meta('original')['components_revision'], 3)
        self.assertFalse((self.directory / preprocess.ORIGINAL).exists())
        self.assertEqual(len(store.list_tests()), 1)

    def test_stale_identity_revision_and_old_client_copy_payload_rejected(self):
        for field, value in [('source_id', str(uuid4())), ('source_revision', 'obsolete')]:
            req = self.request()
            req[field] = value
            self.assertEqual(self.client.post('/api/tests/original/preprocess', json=req).status_code, 409)
        req = {**self.request(), 'name': 'old-copy-client'}
        self.assertEqual(self.client.post('/api/tests/original/preprocess', json=req).status_code, 422)
        self.build()
        self.assertEqual(self.client.post('/api/tests/original/preprocess', json=self.request()).status_code, 409)
        self.assertEqual(len(store.list_tests()), 1)

    def test_duplicate_request_is_idempotent_running_completed_failed_and_payload_checked(self):
        req = self.request()
        background = self.reserve(req)
        for state in ('running', 'completed'):
            duplicate = BackgroundTasks()
            result = preprocess.create_preprocess('original', preprocess.PreprocessRequest(**req), duplicate)
            self.assertEqual(result['preprocessing_operation']['state'], state)
            self.assertEqual(duplicate.tasks, [])
            changed = {**req, 'filters': []}
            self.assertEqual(self.client.post('/api/tests/original/preprocess', json=changed).status_code, 409)
            if state == 'running':
                asyncio.run(background())
        self.refresh()
        req = self.request()
        with patch.object(preprocess, 'build_pyramid', side_effect=OSError('full disk')):
            asyncio.run(self.reserve(req)())
        duplicate = BackgroundTasks()
        result = preprocess.create_preprocess('original', preprocess.PreprocessRequest(**req), duplicate)
        self.assertEqual(result['preprocessing_operation']['state'], 'failed')
        self.assertEqual(duplicate.tasks, [])
        info = store.list_tests()[0]['preprocessing_operation']
        self.assertEqual(set(info), {'request_id', 'state', 'error'})

    def test_failure_restores_last_good_and_first_failure_cleans_unused_baseline(self):
        for prior_filter in (False, True):
            with self.subTest(prior_filter=prior_filter):
                if prior_filter:
                    self.build()
                data = (self.directory / 'data.parquet').read_bytes()
                meta = (self.directory / 'meta.json').read_bytes()
                self.refresh()
                with patch.object(preprocess, 'build_pyramid', side_effect=OSError('disk is full')):
                    asyncio.run(self.reserve()())
                self.assert_failed('disk is full')
                self.assertEqual((self.directory / 'data.parquet').read_bytes(), data)
                self.assertEqual((self.directory / 'meta.json').read_bytes(), meta)
                self.assertEqual((self.directory / preprocess.ORIGINAL).exists(), prior_filter)
                self.assertFalse((self.directory / preprocess.WORK).exists())

    def test_crash_at_every_publication_move_recovers_prior_data_and_metadata(self):
        self.build()
        for crash_at in range(1, 7):
            with self.subTest(crash_at=crash_at):
                self.refresh()
                before = {p: (self.directory / p).read_bytes() for p in ('data.parquet', 'meta.json')}
                pyramid = {p.name: p.read_bytes() for p in (self.directory / 'pyramid').iterdir()}
                replace = preprocess.os.replace
                calls = 0
                def interrupted(source, destination):
                    nonlocal calls
                    source, destination = Path(source), Path(destination)
                    swap = 'rollback' in destination.parts or 'next' in source.parts
                    result = replace(source, destination)
                    if swap:
                        calls += 1
                        if calls == crash_at:
                            raise KeyboardInterrupt('simulated process termination')
                    return result
                with patch.object(preprocess.os, 'replace', side_effect=interrupted):
                    with self.assertRaises(KeyboardInterrupt):
                        asyncio.run(self.reserve(self.request(window_s=.021))())
                self.assertEqual(store.get_status('original')['status'], 'rebuilding')
                main._recover_interrupted_ingests()
                self.assert_failed('interrupted')
                for p, contents in before.items():
                    self.assertEqual((self.directory / p).read_bytes(), contents)
                self.assertEqual({p.name: p.read_bytes() for p in (self.directory / 'pyramid').iterdir()}, pyramid)
                self.assertFalse((self.directory / preprocess.WORK).exists())
                main._recover_interrupted_ingests()  # startup recovery remains idempotent

    def test_ordinary_exception_during_publication_rolls_back_and_keeps_ready(self):
        self.build()
        self.refresh()
        before = (self.directory / 'data.parquet').read_bytes()
        replace = preprocess.os.replace
        def failed(source, destination):
            if Path(source).parent.name == 'next' and Path(source).name == 'pyramid':
                raise OSError('publication failure')
            return replace(source, destination)
        with patch.object(preprocess.os, 'replace', side_effect=failed):
            asyncio.run(self.reserve(self.request(window_s=.031))())
        self.assert_failed('publication failure')
        self.assertEqual((self.directory / 'data.parquet').read_bytes(), before)
        self.assertTrue((self.directory / 'pyramid' / 'L16.parquet').is_file())

    def test_restart_queued_job_and_completed_cleanup_do_not_lose_good_data(self):
        before = (self.directory / 'data.parquet').read_bytes()
        self.reserve()
        main._recover_interrupted_ingests()
        self.assert_failed('interrupted')
        self.assertEqual((self.directory / 'data.parquet').read_bytes(), before)
        self.refresh()
        actual = preprocess._cleanup
        with patch.object(preprocess, '_cleanup', wraps=actual) as cleanup:
            # Let reservation cleanup work, then simulate a crash after Ready.
            cleanup.side_effect = lambda directory: actual(directory) if cleanup.call_count == 1 else (_ for _ in ()).throw(KeyboardInterrupt())
            with self.assertRaises(KeyboardInterrupt):
                asyncio.run(self.reserve()())
        active = (self.directory / 'data.parquet').read_bytes()
        self.assertNotEqual(active, before)
        main._recover_interrupted_ingests()
        self.assertEqual((self.directory / 'data.parquet').read_bytes(), active)
        self.assertEqual(store.get_status('original')['preprocessing_operation']['state'], 'completed')

    def test_legacy_copy_becomes_own_baseline_without_merging_and_clear_keeps_provenance(self):
        legacy = {'version': 1, 'source': {'name': 'unavailable-original'}, 'filters': []}
        meta = {**self.meta, 'preprocessing': legacy}
        store.write_json_atomic(self.directory / 'meta.json', meta)
        self.refresh()
        self.assertIsNone(self.snapshot['preprocessing'])
        self.assertEqual(self.snapshot['legacy_preprocessing'], legacy)
        self.build()
        self.assertTrue(store.get_meta('original')['preprocessing']['legacy_copy'])
        self.refresh()
        req = self.request()
        req['filters'] = []
        self.build(req)
        self.assertEqual(store.get_meta('original')['preprocessing'], legacy)

    def test_short_saved_points_do_not_control_whole_record_processing(self):
        points = store.read_testpoints('original')
        points['test_points'] = [{'id': 1, 'start_s': 99, 'end_s': 99}]
        store.write_json_atomic(self.directory / 'testpoints.json', points)
        before = (self.directory / 'testpoints.json').read_bytes()
        self.refresh()
        self.build()
        self.assertEqual((self.directory / 'testpoints.json').read_bytes(), before)

    def test_invalid_filters_reserve_nothing_but_empty_recipe_completes_noop(self):
        cases = [([{'column': 'time', 'filter': {'kind': 'detrend'}}], 400),
            ([{'column': 'absent', 'filter': {'kind': 'detrend'}}], 400),
            ([{'column': 'signal', 'filter': {'kind': 'lowpass', 'f1': 500}}], 400),
            ([{'column': 'signal', 'filter': {'kind': 'moving_avg'}}], 400),
            ([{'column': 'signal', 'filter': {'kind': 'despike', 'window_s': .01, 'max_spike_s': .01}}], 400),
            ([{'column': 'signal', 'filter': {'kind': 'lowpass', 'f1': 20, 'order': 11}}], 422)]
        for filters, status in cases:
            req = {**self.request(), 'filters': filters}
            self.assertEqual(self.client.post('/api/tests/original/preprocess', json=req).status_code, status)
            self.assertEqual(store.get_status('original')['status'], 'ready')
        req = self.request()
        req['filters'] *= 2
        self.assertEqual(self.client.post('/api/tests/original/preprocess', json=req).status_code, 400)
        req['filters'] = []
        response = self.client.post('/api/tests/original/preprocess', json=req)
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()['preprocessing_operation']['state'], 'completed')
        self.assertFalse((self.directory / preprocess.ORIGINAL).exists())

    def test_plot_filters_and_export_provenance_use_active_data(self):
        out = self.build()
        before = (self.directory / 'data.parquet').read_bytes()
        result = dsp.filtered_samples('original', ['signal'], 'moving_avg', None, None, px=1000, window_s=.021)
        np.testing.assert_allclose(result.filtered['signal'], uniform_filter1d(out['signal'].to_numpy(), 21, mode='nearest'))
        self.assertEqual((self.directory / 'data.parquet').read_bytes(), before)
        record = analysis_metadata.source_context('original', store.get_meta('original'), ['signal'], 0, self.count)
        self.assertEqual(record['source']['preprocessing']['mode'], 'in_place')
        exported = self.client.get('/api/tests/original/export')
        self.assertEqual(exported.status_code, 200, exported.text[:200])
        self.assertEqual(len(exported.text.splitlines()), self.count + 1)

    def test_busy_reservation_rejects_other_save_rename_and_worker_rechecks_revision(self):
        background = self.reserve()
        response = self.client.post('/api/tests/original/preprocess', json=self.request())
        self.assertEqual(response.status_code, 409)
        response = self.client.post('/api/tests/original/rename?new_name=renamed')
        self.assertEqual(response.status_code, 409)
        self.values[:] = 99
        self.write_data()
        asyncio.run(background())
        self.assert_failed('Source changed')
        np.testing.assert_array_equal(pl.read_parquet(self.directory / 'data.parquet')['signal'], self.values)

    def test_short_continuous_regions_fail_without_changing_active_samples(self):
        self.values[1:20] = np.nan
        self.write_data()
        self.meta['time_gap_ranges'] = [[1, 20]]
        store.write_json_atomic(self.directory / 'meta.json', self.meta)
        self.refresh()
        before = (self.directory / 'data.parquet').read_bytes()
        asyncio.run(self.reserve()())
        self.assert_failed('fewer than two finite')
        self.assertEqual((self.directory / 'data.parquet').read_bytes(), before)

    def test_missing_retained_original_refuses_reapply_and_clear_without_touching_active(self):
        self.build()
        self.refresh()
        (self.directory / preprocess.ORIGINAL / 'data.parquet').unlink()
        before = (self.directory / 'data.parquet').read_bytes()
        self.assertEqual(self.client.get('/api/tests/original/preprocess').status_code, 409)
        for filters in ([], self.request()['filters']):
            req = {**self.request(), 'filters': filters}
            self.assertEqual(self.client.post('/api/tests/original/preprocess', json=req).status_code, 409)
        self.assertEqual((self.directory / 'data.parquet').read_bytes(), before)

    def test_rename_and_trash_restore_keep_hidden_original_with_identity_and_clear(self):
        before = (self.directory / 'data.parquet').read_bytes()
        source_id = self.snapshot['source']['id']
        self.build()
        response = self.client.post('/api/tests/original/rename?new_name=renamed')
        self.assertEqual(response.status_code, 200, response.text)
        response = self.client.delete('/api/tests/renamed')
        self.assertEqual(response.status_code, 200, response.text)
        response = self.client.post(f"/api/trash/{response.json()['trash_id']}/restore", json={'name': 'restored'})
        self.assertEqual(response.status_code, 200, response.text)
        snapshot = self.client.get('/api/tests/restored/preprocess').json()
        self.assertEqual(snapshot['source']['id'], source_id)
        request = dict(request_id=str(uuid4()), source_id=source_id,
                       source_revision=snapshot['source']['revision'], filters=[])
        response = self.client.post('/api/tests/restored/preprocess', json=request)
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual((self.tests / 'restored' / 'data.parquet').read_bytes(), before)
        self.assertEqual(store.get_meta('restored')['name'], 'restored')
        self.assertEqual([row['name'] for row in store.list_tests()], ['restored'])

    def test_recovery_failure_preserves_backups_and_retry_after_permissions_fixed_restores(self):
        self.build()
        self.refresh()
        before = (self.directory / 'data.parquet').read_bytes()
        publish = preprocess._publish
        def crash(directory, staging):
            publish(directory, staging)
            raise KeyboardInterrupt()
        with patch.object(preprocess, '_publish', side_effect=crash):
            with self.assertRaises(KeyboardInterrupt):
                asyncio.run(self.reserve(self.request(window_s=.031))())
        with patch.object(preprocess, '_rollback', side_effect=PermissionError('locked')):
            main._recover_interrupted_ingests()
        self.assertEqual(store.get_status('original')['status'], 'error')
        self.assertTrue((self.directory / preprocess.WORK / 'rollback' / 'data.parquet').is_file())
        main._recover_interrupted_ingests()
        self.assert_failed('interrupted')
        self.assertEqual((self.directory / 'data.parquet').read_bytes(), before)

    def test_status_reads_retry_transient_windows_replace_conflicts_but_raise_real_permission_errors(self):
        path = self.directory / 'status.json'
        with patch.object(Path, 'read_text', side_effect=[PermissionError('sharing'), PermissionError('sharing'), '{"status":"ready"}']) as read:
            with patch.object(store.time, 'sleep') as sleep:
                self.assertEqual(store._read_json(path), {'status': 'ready'})
                self.assertEqual(read.call_count, 3)
                self.assertEqual(sleep.call_count, 2)
        with patch.object(Path, 'read_text', side_effect=PermissionError('no access')) as read:
            with patch.object(store.time, 'sleep'):
                with self.assertRaises(PermissionError):
                    store._read_json(path)
                self.assertEqual(read.call_count, 6)

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


    def test_butterworth_and_detrend_match_independent_whole_record_scipy_oracles(self):
        for kind, band in [('lowpass', 30), ('highpass', 30),
                           ('bandpass', [5, 30]), ('bandstop', [5, 30]), ('detrend', None)]:
            with self.subTest(kind=kind):
                settings = dict(kind=kind, order=3, f1=5 if isinstance(band, list) else band,
                                f2=30 if isinstance(band, list) else None)
                self.snapshot = preprocess.get_preprocess('original')
                out = self.build(self.request(**settings))
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
        self.assertEqual(len(store.get_meta('original')['preprocessing']['warnings']), 2)


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
        self.assertIn('acquisition gap', store.get_meta('original')['preprocessing']['warnings'][0])


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


    def test_disk_and_whole_record_budget_fail_before_reservation(self):
        with patch.object(preprocess.shutil, 'disk_usage', return_value=SimpleNamespace(free=0)):
            response = self.client.post('/api/tests/original/preprocess', json=self.request())
            self.assertEqual(response.status_code, 507)
        with patch.object(preprocess, 'MAX_FILTER_SAMPLES', 100):
            response = self.client.post('/api/tests/original/preprocess', json=self.request())
            self.assertEqual(response.status_code, 400)
        self.assertFalse((self.tests / 'processed').exists())
