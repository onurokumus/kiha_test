"""Downloaded native bins, independent reference estimators and failure atomicity."""
import csv
import hashlib
import io
import zipfile
from unittest.mock import patch

import numpy as np
from scipy import signal
from fastapi import HTTPException

from app import dsp, plot_export, spectrum_export, store
from .test_plot_export import PlotExportFixture


class SpectrumExportTests(PlotExportFixture):
    def setUp(self):
        super().setUp()
        self.n, self.fs = 16385, 2048.
        self.values = 7 + 3 * np.sin(2 * np.pi * 128.125 * np.arange(self.n) / self.fs)
        self.values[[0, 257, 258, self.n - 1]] = np.nan
        self.rpm = np.linspace(-1200., -2400., self.n)
        self.rpm[50] = np.nan
        self.write_test('native', 12.125 + np.arange(self.n) / self.fs, self.values,
            [{'id': 9, 'start_idx': 0, 'end_idx': self.n, 'start_s': 999.}],
            fs=self.fs, extra={'speed': self.rpm})

    def payload(self, **overrides):
        return {'kind': 'spectrum', 'column': 'signal', 'mode': 'fft', 'axis': 'hz',
            'method_version': 'kiha-spectrum-v2', 'sources': [{'test': 'native', 'tp_id': 9,
            'expected_i0': 0, 'expected_i1': self.n, 'expected_fs_hz': self.fs}],
            'x_range': None, **overrides}

    def export(self, **overrides):
        return self.client.post('/api/spectrum-export', json=self.payload(**overrides))

    def reference(self, mode='fft', nperseg=4096, values=None, fs=None):
        values = self.values if values is None else values
        fs = self.fs if fs is None else fs
        x = np.arange(len(values)); finite = np.isfinite(values)
        clean = np.interp(x, x[finite], values[finite]); clean -= clean.mean()
        if mode == 'welch':
            return signal.welch(clean, fs=fs, window='hann', nperseg=nperseg,
                noverlap=nperseg // 2, nfft=nperseg, detrend='constant',
                return_onesided=True, scaling='density', average='mean')
        magnitude = np.abs(np.fft.rfft(clean)) * 2 / len(clean)
        magnitude[0] /= 2
        if len(clean) % 2 == 0: magnitude[-1] /= 2
        return np.fft.rfftfreq(len(clean), 1 / fs), magnitude

    def check_values(self, rows, mode='fft', **reference):
        frequency, magnitude = self.reference(mode, **reference)
        bins = [int(row['bin_index']) for row in rows]
        np.testing.assert_allclose([float(row['frequency_hz']) for row in rows], frequency[bins], rtol=1e-14)
        column = 'signal [amplitude U]' if mode == 'fft' else 'signal [PSD U^2/Hz]'
        np.testing.assert_allclose([float(row[column]) for row in rows], magnitude[bins], rtol=1e-10, atol=2e-14)

    def test_complete_native_fft_including_endpoints_and_exact_metadata(self):
        with patch.object(dsp, 'spectrum', side_effect=AssertionError('reduced display path forbidden')):
            rows = self.rows(self.export())
        self.assertEqual(len(rows), self.n // 2 + 1)
        self.assertEqual([int(row['bin_index']) for row in rows], list(range(len(rows))))
        self.check_values(rows)
        row = rows[0]
        for key, value in {'test_point_id': '9', 'source_i0': '0', 'source_i1': str(self.n),
                'nan_count': '4', 'finite_count': str(self.n - 4), 'method_version': 'kiha-spectrum-v2',
                'method_window': 'rectangular', 'method_prefilter': 'none',
                'method_scaling': 'peak_amplitude', 'method_units': 'U'}.items():
            self.assertEqual(row[key], value)
        self.assertEqual(float(row['time_start_s']), 12.125)
        self.assertNotIn('speed', row)
        self.assertNotIn('order_cycles_per_rev', row)

    def test_welch_odd_segments_native_density_settings_and_trailing_samples(self):
        sources = self.payload()['sources']; sources[0]['nperseg'] = 4095
        rows = self.rows(self.export(mode='welch', sources=sources))
        self.assertEqual(len(rows), 2048); self.check_values(rows, 'welch', nperseg=4095)
        method = rows[0]
        self.assertEqual(method['method_noverlap'], '2047')
        self.assertEqual(method['method_window'], 'hann_periodic')
        self.assertEqual(method['method_units'], 'U²/Hz')
        self.assertEqual(method['method_segment_count'], '7')
        self.assertEqual(method['method_trailing_samples'], '2')

    def test_order_crop_uses_each_source_mean_without_density_rescaling(self):
        mean = float(np.nanmean(np.abs(self.rpm)))
        sources = self.payload()['sources']; sources[0].update(rpm_col='speed', expected_mean_rpm=mean)
        rows = self.rows(self.export(mode='welch', axis='per_rev', sources=sources, x_range=[3, 7]))
        self.check_values(rows, 'welch')
        self.assertTrue(all(3 <= float(row['order_cycles_per_rev']) <= 7 for row in rows))
        np.testing.assert_allclose([float(row['order_cycles_per_rev']) for row in rows],
            np.array([float(row['frequency_hz']) for row in rows]) * 60 / mean, rtol=1e-14)
        self.assertEqual(rows[0]['rpm_col'], 'speed'); self.assertEqual(rows[0]['rpm_nan_count'], '1')

    def test_hz_crop_is_native_centers_after_complete_estimation(self):
        frequencies, _ = self.reference(); lo, hi = frequencies[101], frequencies[150]
        rows = self.rows(self.export(x_range=[lo, hi]))
        self.assertEqual([int(row['bin_index']) for row in rows], list(range(101, 151)))
        self.check_values(rows)
        response = self.export(x_range=[1e6, 2e6])
        self.assertEqual(response.status_code, 400); self.assertNotIn('content-disposition', response.headers)

    def test_cross_test_native_grids_rates_bounds_and_large_ids_stay_independent(self):
        large = 2**70 + 17
        values = np.cos(np.arange(120))
        self.write_test('other', np.arange(120) / 500, values,
                        [{'id': large, 'start_idx': 0, 'end_idx': 120}], fs=500.)
        sources = self.payload()['sources'] + [{'test': 'other', 'tp_id': large,
            'expected_i0': 0, 'expected_i1': 120, 'expected_fs_hz': 500.}]
        rows = self.rows(self.export(sources=sources))
        first = [row for row in rows if row['source_test'] == 'native']
        other = [row for row in rows if row['source_test'] == 'other']
        self.check_values(first); self.check_values(other, values=values, fs=500.)
        self.assertEqual(len(other), 61); self.assertEqual(other[0]['test_point_id'], str(large))
        self.assertAlmostEqual(float(other[-1]['frequency_hz']), 250.)

    def test_full_interval_uses_loaded_nominal_bounds_and_actual_times(self):
        sources = [{'test': 'native', 't0': 12.375, 't1': 12.875,
            'expected_i0': 512, 'expected_i1': 1537, 'expected_fs_hz': self.fs}]
        rows = self.rows(self.export(sources=sources))
        self.check_values(rows, values=self.values[512:1537])
        self.assertEqual(rows[0]['test_point_id'], '')
        self.assertEqual(float(rows[0]['time_start_s']), 12.375)

    def test_zero_values_remain_linear_and_present(self):
        self.write_test('zero', np.arange(32) / 16, np.zeros(32), [], fs=16.)
        rows = self.rows(self.export(sources=[{'test': 'zero', 'expected_i0': 0,
            'expected_i1': 32, 'expected_fs_hz': 16.}]))
        self.assertEqual(len(rows), 17)
        self.assertTrue(all(float(row['signal [amplitude U]']) == 0 for row in rows))

    def test_order_overflow_is_an_error_not_infinite_csv_coordinates(self):
        self.write_test('tiny-speed', np.arange(32) / 16, np.sin(np.arange(32)), [],
                        fs=16., extra={'speed': np.full(32, 1e-310)})
        response = self.export(axis='per_rev', sources=[{'test': 'tiny-speed',
            'expected_i0': 0, 'expected_i1': 32, 'expected_fs_hz': 16.,
            'rpm_col': 'speed', 'expected_mean_rpm': 1e-310}])
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn('nonfinite frequency axis', response.text)

    def test_validation_and_stale_context_fail_before_attachments(self):
        cases = [({'mode': 'invalid'}, 422), ({'method_version': 'old'}, 422),
            ({'axis': 'per_rev'}, 422), ({'column': 'clock'}, 400),
            ({'x_range': [2, 1]}, 422), ({'log_y': True}, 422)]
        for key, value, status in [('expected_i0', 1, 409), ('expected_fs_hz', 1000., 409),
                ('test', '../native', 400), ('tp_id', 999, 404), ('t0', 0, 422),
                ('rpm_col', 'absent', 400), ('expected_mean_rpm', 100., 409)]:
            sources = self.payload()['sources']; sources[0][key] = value
            cases.append(({'sources': sources}, status))
        for changes, status in cases:
            with self.subTest(changes=changes):
                response = self.export(**changes)
                self.assertEqual(response.status_code, status, response.text)
                self.assertNotIn('content-disposition', response.headers)

    def test_gap_and_missing_restrictions_and_ready_guard_are_shared(self):
        directory = self.tests / 'native'
        meta = store.get_meta('native'); meta['time_gap_ranges'] = [[20, 30]]
        store.write_json_atomic(directory / 'meta.json', meta)
        response = self.export(); self.assertEqual(response.status_code, 400)
        self.assertIn('gap', response.text)
        store.write_json_atomic(directory / 'status.json', {'status': 'rebuilding'})
        self.assertEqual(self.export().status_code, 409)

    def bundle(self, plots=None, layout='2x2'):
        return self.client.post('/api/spectrum-export/bundle', json={'layout': layout,
            'plots': plots or [{'slot': 2, 'request': self.payload()},
                             {'slot': 4, 'request': self.payload(mode='welch')}]})

    def test_bundle_native_csvs_slot_identity_headers_and_source_preservation(self):
        before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.tests.rglob('*') if p.is_file()}
        response = self.bundle(); self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers['x-export-plots'], '2')
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            names = archive.namelist(); self.assertTrue(names[0].startswith('01_slot-2_'))
            self.assertTrue(names[1].startswith('02_slot-4_'))
            for name, mode in zip(names, ['fft', 'welch']):
                self.check_values(list(csv.DictReader(io.StringIO(archive.read(name).decode()))), mode)
        self.assertEqual(before, {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.tests.rglob('*') if p.is_file()})

    def test_aggregate_budget_and_invalid_bundle_stop_before_native_work(self):
        with patch.object(plot_export, 'MAX_EXPORT_ROWS', self.n + 1), patch.object(dsp, 'spectrum_samples') as native:
            response = self.bundle(); self.assertEqual(response.status_code, 400); native.assert_not_called()
        for slots in ([2, 2], [4, 2], [1, 2, 3, 4, 5]):
            with self.subTest(slots=slots):
                response = self.bundle([{'slot': slot, 'request': self.payload()} for slot in slots])
                self.assertEqual(response.status_code, 422)

    def test_late_failure_closes_staging_and_never_returns_partial_archive(self):
        factory = plot_export.tempfile.SpooledTemporaryFile; files = []
        def temporary(*args, **kwargs):
            result = factory(*args, **kwargs); files.append(result); return result
        native = dsp.spectrum_samples; count = 0
        def fail(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 2: raise ValueError('isolated later source failure')
            return native(*args, **kwargs)
        with patch.object(plot_export.tempfile, 'SpooledTemporaryFile', temporary), patch.object(dsp, 'spectrum_samples', fail):
            response = self.bundle()
        self.assertEqual(response.status_code, 400); self.assertIn('Plot 4:', response.text)
        self.assertNotIn('content-disposition', response.headers)
        self.assertEqual(len(files), 3); self.assertTrue(all(f.closed for f in files))

    def test_actual_locked_work_budget_revalidates_after_preflight(self):
        request = spectrum_export.SpectrumExportRequest.model_validate(self.payload())
        with patch.object(plot_export, 'MAX_EXPORT_ROWS', self.n):
            with self.assertRaises(HTTPException):
                spectrum_export.prepare_export(request, row_budget=plot_export.ExportRowBudget(1))
