"""Full-flight overlays preserve native rows while exporting displayed alignment."""

import csv
import hashlib
import io
import json
from unittest.mock import patch
import zipfile

import numpy as np

from app import dsp, plot_export, xy_export
from .test_plot_export import PlotExportFixture


class FullFlightExportTests(PlotExportFixture):
    def setUp(self):
        super().setUp()
        self.addCleanup(self.client.close)
        self.torque = np.arange(240, dtype=np.float64) ** 2 / 71
        self.write_test('alpha', self.time, self.signal, self.points,
                        extra={'torque': self.torque, 'position': np.arange(240) % 7})
        self.beta_time = 12.25 + np.arange(41, dtype=np.float64) / 250
        self.beta_signal = np.arange(41, dtype=np.float64) * 1.234567890123456e-10
        self.write_test('beta', self.beta_time, self.beta_signal, [], fs=250,
                        extra={'position': np.arange(41)[::-1] % 7})

    def flight_sources(self):
        return [
            {'test': 'alpha', 'expected_i0': 0, 'expected_i1': 240,
             'time_offset': -float(self.time[0]), 'columns': ['torque', 'signal'],
             'px': 800, 'display': 'line'},
            {'test': 'beta', 'expected_i0': 0, 'expected_i1': 41,
             'time_offset': -float(self.beta_time[0]), 'columns': ['signal'],
             'px': 800, 'display': 'line'},
        ]

    def flight_payload(self, **overrides):
        return {'column': 'torque', 'columns': ['torque', 'signal'], 'data': 'original',
                'filter': None, 'sources': self.flight_sources(), 'x_range': [.04, .12], **overrides}

    def xy_payload(self, **overrides):
        sources = [{key: value for key, value in source.items()
                    if key not in {'columns', 'px', 'display'}} for source in self.flight_sources()]
        for source in sources:
            source['expected_time_column'] = 'clock'
        return {'kind': 'xy', 'column': 'signal', 'x_column': 'clock',
                'method_version': 'kiha-xy-v2', 'sources': sources,
                'x_range': [.04, .12], 'y_range': None, **overrides}

    def post_time(self, **overrides):
        return self.client.post('/api/plot-export', json=self.flight_payload(**overrides))

    def post_xy(self, **overrides):
        return self.client.post('/api/xy-export', json=self.xy_payload(**overrides))

    def grouped(self, rows):
        return {name: [row for row in rows if row['source_test'] == name] for name in ('alpha', 'beta')}

    def test_elapsed_crop_preserves_independent_rates_native_times_and_missing_variables(self):
        rows = self.rows(self.post_time())
        self.assertEqual(len(rows), 81 + 21)
        expected = {'alpha': (self.time, range(40, 121)), 'beta': (self.beta_time, range(10, 31))}
        for name, selected in self.grouped(rows).items():
            times, indices = expected[name]
            indices = np.array(list(indices))
            self.assertEqual([int(row['sample_index']) for row in selected], indices.tolist())
            np.testing.assert_array_equal([float(row['time_s']) for row in selected], times[indices])
            np.testing.assert_array_equal([float(row['displayed_time_s']) for row in selected], times[indices] - times[0])
            self.assertTrue(all(float(row['time_offset_s']) == -times[0] for row in selected))
            self.assertTrue(all(row['tp_time_s'] == '' for row in selected))
        self.assertTrue(all(row['torque [original]'] == '' for row in self.grouped(rows)['beta']))
        np.testing.assert_array_equal([float(row['torque [original]']) for row in self.grouped(rows)['alpha']], self.torque[40:121])
        np.testing.assert_array_equal([float(row['signal [original]']) for row in self.grouped(rows)['beta']], self.beta_signal[10:31])
        self.assertNotIn(None, rows[-1])  # fixed union headers across every source

    def test_filtered_columns_use_each_native_source_context_then_display_crop(self):
        spec = {'kind': 'moving_avg', 'window_s': .017}
        with patch.object(dsp, 'filtered_samples', wraps=dsp.filtered_samples) as process:
            rows = self.rows(self.post_time(data='both', filter=spec))
        self.assertEqual([call.args[:2] for call in process.call_args_list],
                         [('alpha', ['torque', 'signal']), ('beta', ['signal'])])
        for name, selected in self.grouped(rows).items():
            columns = ['torque', 'signal'] if name == 'alpha' else ['signal']
            expected = dsp.filtered_samples(name, columns, t0=None, t1=None, px=800, display='line', **spec)
            indices = np.array([int(row['sample_index']) for row in selected])
            for column in columns:
                np.testing.assert_allclose([float(row[f'{column} [filtered]'] or 'nan') for row in selected],
                    expected.filtered[column][indices - expected.s0], rtol=1e-14, equal_nan=True)
        self.assertTrue(all(row['torque [filtered]'] == '' for row in self.grouped(rows)['beta']))

    def test_source_bounds_stay_native_while_crop_uses_an_additional_manual_offset(self):
        sources = self.flight_sources()
        for source, times in zip(sources, [self.time, self.beta_time]):
            # Interior fractions avoid ambiguity in the existing approximate
            # time-to-row resolver's floor/ceil arithmetic.
            source.update(t0=float(times[5] + (times[6] - times[5]) / 4),
                          t1=float(times[35] - (times[35] - times[34]) / 4),
                          expected_i0=5, expected_i1=36)
            source['time_offset'] += .5
        rows = self.rows(self.post_time(sources=sources, x_range=[.52, .55]))
        self.assertEqual([int(row['sample_index']) for row in self.grouped(rows)['alpha']], list(range(20, 36)))
        self.assertEqual([int(row['sample_index']) for row in self.grouped(rows)['beta']], list(range(5, 13)))

    def test_mixed_offset_presence_has_one_csv_schema_and_absent_means_zero(self):
        sources = self.flight_sources()
        del sources[1]['time_offset']
        rows = self.rows(self.post_time(sources=sources, x_range=None))
        self.assertEqual(len(rows), 281)
        beta = self.grouped(rows)['beta']
        self.assertTrue(all(float(row['time_offset_s']) == 0 for row in beta))
        self.assertTrue(all(row['displayed_time_s'] == row['time_s'] for row in beta))
        for source in sources:
            source.pop('time_offset', None)
        legacy = self.rows(self.post_time(sources=sources, x_range=None))
        self.assertNotIn('displayed_time_s', legacy[0])
        self.assertNotIn('time_offset_s', legacy[0])

    def test_time_bundle_matches_single_csv_and_records_per_source_provenance(self):
        before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in self.tests.rglob('*') if path.is_file()}
        expected = self.post_time().content
        response = self.client.post('/api/plot-export/bundle', json={
            'include_metadata': True, 'layout': '2x2', 'plots': [{'slot': 2, 'request': self.flight_payload()}]})
        self.assertEqual(response.status_code, 200, response.text if response.status_code != 200 else '')
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            record = json.loads(archive.read('analysis.json'))['plots'][0]
            self.assertEqual(archive.read(record['file']), expected)
            alpha, beta = record['sources']
            self.assertEqual([variable['column'] for variable in beta['variables']], ['signal'])
            self.assertEqual(beta['unavailable_variables'], ['torque'])
            self.assertEqual(alpha['exported_centers']['first_time_s'], self.time[40])
            self.assertEqual(beta['exported_centers']['first_time_s'], self.beta_time[10])
            self.assertEqual(beta['display_alignment']['time_offset_s'], -12.25)
            self.assertEqual(beta['display_alignment']['resampling'], 'none')
        self.assertEqual(before, {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                                 for path in self.tests.rglob('*') if path.is_file()})

    def test_invalid_column_subsets_and_offsets_fail_before_native_reads(self):
        for subset in ([], ['signal', 'signal'], ['absent'], ['clock'], ['signal', 'extra']):
            with self.subTest(subset=subset), patch.object(dsp, 'filtered_samples') as process:
                sources = self.flight_sources(); sources[1]['columns'] = subset
                response = self.post_time(sources=sources)
                self.assertEqual(response.status_code, 422, response.text)
                process.assert_not_called()
        sources = self.flight_sources(); sources[1]['columns'] = ['torque']
        response = self.post_time(sources=sources)
        self.assertEqual(response.status_code, 400)
        self.assertNotIn('content-disposition', response.headers)
        for value in (float('inf'), float('nan')):
            payload = self.flight_payload(); payload['sources'][0]['time_offset'] = value
            with self.assertRaises(ValueError):
                plot_export.PlotExportRequest.model_validate(payload)
            payload = self.xy_payload(); payload['sources'][0]['time_offset'] = value
            with self.assertRaises(ValueError):
                xy_export.XYExportRequest.model_validate(payload)
        payload = self.payload(); payload['sources'][0]['time_offset'] = 1
        self.assertEqual(self.client.post('/api/plot-export', json=payload).status_code, 422)

    def test_xy_time_x_y_and_shared_axes_shift_only_display_values_and_crop(self):
        for x_column, column in [('clock', 'signal'), ('position', 'clock'), ('clock', 'clock')]:
            with self.subTest(x=x_column, y=column):
                changes = {'x_column': x_column, 'column': column,
                           'x_range': [.04, .12] if x_column == 'clock' else None,
                           'y_range': [.04, .12] if column == 'clock' else None}
                rows = self.rows(self.post_xy(**changes))
                expected_counts = (80, 21) if column == 'signal' else (81, 21)
                self.assertEqual(tuple(len(part) for part in self.grouped(rows).values()), expected_counts)
                role = 'X/Y' if x_column == column else 'X' if x_column == 'clock' else 'Y'
                for name, selected in self.grouped(rows).items():
                    times = self.time if name == 'alpha' else self.beta_time
                    indices = np.array([int(row['sample_index']) for row in selected])
                    np.testing.assert_array_equal([float(row['time_s']) for row in selected], times[indices])
                    np.testing.assert_array_equal([float(row[f'clock [{role}]']) for row in selected], times[indices] - times[0])
                    self.assertTrue(all(row[f'clock [{role}]'] == row['displayed_time_s'] for row in selected))

    def test_xy_signal_axes_keep_native_unsorted_pairs_and_tiny_crops(self):
        payload = self.xy_payload(x_column='position', x_range=[1, 5], y_range=[0, 2e-9])
        sources = payload['sources']; sources[0]['time_offset'] = 1e9
        sources[1]['time_offset'] = -1e9
        rows = self.rows(self.client.post('/api/xy-export', json=payload))
        expected = [index for index in range(41) if 1 <= (40 - index) % 7 <= 5 and self.beta_signal[index] <= 2e-9]
        self.assertEqual([int(row['sample_index']) for row in rows], expected)
        self.assertTrue(all(row['source_test'] == 'beta' for row in rows))
        np.testing.assert_array_equal([float(row['signal [Y]']) for row in rows], self.beta_signal[expected])

    def test_xy_time_detection_is_per_flight_and_does_not_shift_same_named_sensor(self):
        clock_sensor = np.arange(41, dtype=np.float64) / 250
        self.write_test('beta', self.beta_time, self.beta_signal, [], fs=250,
                        time_column='elapsed', extra={'clock': clock_sensor})
        payload = self.xy_payload(); payload['sources'][1]['expected_time_column'] = 'elapsed'
        rows = self.rows(self.client.post('/api/xy-export', json=payload))
        beta = self.grouped(rows)['beta']
        self.assertEqual([int(row['sample_index']) for row in beta], list(range(10, 31)))
        np.testing.assert_array_equal([float(row['clock [X]']) for row in beta], clock_sensor[10:31])

    def test_xy_bundle_and_sidecar_preserve_native_scope_and_displayed_axes(self):
        expected = self.post_xy().content
        response = self.client.post('/api/xy-export/bundle', json={'include_metadata': True,
            'layout': '2x2', 'plots': [{'slot': 3, 'request': self.xy_payload()}]})
        self.assertEqual(response.status_code, 200, response.text if response.status_code != 200 else '')
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            record = json.loads(archive.read('analysis.json'))['plots'][0]
            self.assertEqual(archive.read(record['file']), expected)
            self.assertEqual(record['sources'][1]['display_alignment']['time_axes'], ['x'])
            self.assertEqual(record['sources'][1]['exported_centers']['first_time_s'], self.beta_time[10])
            self.assertEqual(record['sources'][1]['exported_rows'], 21)
            rows = list(csv.DictReader(io.StringIO(archive.read(record['file']).decode())))
            self.assertNotIn(None, rows[-1])
