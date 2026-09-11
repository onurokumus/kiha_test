"""Sidecars match serialized rows/executed methods and loaded image snapshots."""
import base64
import csv
import hashlib
import io
import json
import zipfile
from unittest.mock import patch

from fastapi import HTTPException

from app import analysis_metadata as analysis, image_export, plot_export, store
from .test_plot_export import PlotExportFixture


class AnalysisMetadataTests(PlotExportFixture):
    def setUp(self):
        super().setUp()
        self.addCleanup(self.client.close)
        meta = store.get_meta('alpha')
        meta['derived_variables'] = [
            {'name': 'base', 'expression': '{raw µV} * 2', 'dependencies': ['raw µV'],
             'engine': 'safe-polars-v1', 'missing_dependencies': ['raw µV']},
            {'name': 'signal', 'expression': '{base} + 1', 'dependencies': ['base'],
             'engine': 'safe-polars-v1', 'replaced_existing': True},
            {'name': 'unrelated', 'expression': '17', 'dependencies': []}]
        store.write_json_atomic(self.tests / 'alpha/meta.json', meta)

    def time_request(self, **kwargs):
        return {'column': 'signal', 'data': 'both', 'include_metadata': True,
                'sources': [{'test': 'alpha', 'tp_id': 7}],
                'filter': {'kind': 'moving_avg', 'window_s': .0035}, **kwargs}

    def unpack(self, response):
        self.assertEqual(response.status_code, 200, response.text if response.status_code != 200 else '')
        self.assertIn('application/zip', response.headers['content-type'])
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        document = json.loads(archive.read('analysis.json'))
        self.assertEqual(document['schema'], analysis.SCHEMA)
        for plot in document.get('plots', []):
            if 'sha256' in plot:
                self.assertEqual(hashlib.sha256(archive.read(plot['file'])).hexdigest(), plot['sha256'])
        return archive, document

    def test_time_sidecar_matches_actual_rows_filter_counts_and_transitive_equations(self):
        archive, document = self.unpack(self.client.post('/api/plot-export', json=self.time_request(x_range=[.01, .019])))
        plot = document['plots'][0]; record = plot['sources'][0]
        rows = list(csv.DictReader(io.StringIO(archive.read(plot['file']).decode())))
        self.assertEqual([int(row['sample_index']) for row in rows], list(range(45, 55)))
        self.assertEqual(record['exported_rows'], len(rows))
        self.assertEqual(record['scope']['i0'], 35)
        self.assertEqual(record['scope']['i1'], 187)
        self.assertEqual(record['test_point_id'], '7')
        self.assertEqual(record['source_centers']['first_time_s'], self.time[35])
        self.assertEqual(record['exported_centers']['first_index'], 45)
        self.assertEqual(record['exported_centers']['last_time_s'], self.time[54])
        method = record['processing']
        self.assertEqual(method['filter']['window_samples'], 4)
        self.assertEqual(method['filter']['window_s'], .0035)
        self.assertEqual(method['processing_rows'], {'i0': 35, 'i1': 187, 'bounds': 'half_open'})
        self.assertEqual([eq['name'] for eq in record['equations']], ['base', 'signal'])
        self.assertEqual(record['equations'][0]['missing_dependencies'], ['raw µV'])
        self.assertIn('not_retained', record['edit_history_status'])
        self.assertIsNone(record['variables'][0]['unit'])
        self.assertIsNotNone(record['source']['file_observations']['data.parquet'])
        self.assertIn('scipy', record['runtime'])

    def test_original_does_not_claim_an_unused_filter_and_file_only_api_remains_csv(self):
        payload = self.time_request(data='original')
        archive, document = self.unpack(self.client.post('/api/plot-export', json=payload))
        self.assertIsNone(document['plots'][0]['sources'][0]['processing']['filter'])
        csv_bytes = archive.read(document['plots'][0]['file'])
        payload['include_metadata'] = False
        response = self.client.post('/api/plot-export', json=payload)
        self.assertEqual(response.content, csv_bytes)
        self.assertIn('text/csv', response.headers['content-type'])

    def test_bundle_manifest_has_ordered_slots_hashes_and_independent_settings(self):
        request = {'layout': '2x2', 'include_metadata': True, 'plots': [
            {'slot': 2, 'request': self.time_request(data='original')},
            {'slot': 8, 'request': self.time_request(filter={'kind': 'detrend'})}]}
        archive, document = self.unpack(self.client.post('/api/plot-export/bundle', json=request))
        self.assertEqual(len(archive.namelist()), 3)
        self.assertEqual([p['slot'] for p in document['plots']], [2, 8])
        self.assertEqual(document['layout'], '2x2')
        self.assertIsNone(document['plots'][0]['sources'][0]['processing']['filter'])
        self.assertEqual(document['plots'][1]['sources'][0]['processing']['filter']['type'], 'linear')

    def test_spectrum_records_resolved_welch_settings_not_just_requested_segment_size(self):
        source = {'test': 'alpha', 'tp_id': 7, 'expected_i0': 35, 'expected_i1': 187,
                  'expected_fs_hz': 1000., 'nperseg': 4096}
        payload = {'kind': 'spectrum', 'column': 'signal', 'mode': 'welch', 'axis': 'hz',
                   'sources': [source], 'method_version': 'kiha-spectrum-v2', 'include_metadata': True,
                   'x_range': [20, 100]}
        archive, document = self.unpack(self.client.post('/api/spectrum-export', json=payload))
        plot = document['plots'][0]; record = plot['sources'][0]; method = record['processing']['method']
        self.assertEqual(method['nperseg'], 152)
        self.assertEqual(method['noverlap'], 76)
        self.assertEqual(method['units'], 'U²/Hz')
        rows = list(csv.DictReader(io.StringIO(archive.read(plot['file']).decode())))
        self.assertEqual(record['exported_bins']['count'], len(rows))
        self.assertEqual(record['exported_bins']['first'], int(rows[0]['bin_index']))
        self.assertEqual(float(rows[0]['method_bin_spacing_hz']), method['bin_spacing_hz'])

    def test_xy_metadata_matches_finite_coordinate_crop_and_same_variable(self):
        payload = {'kind': 'xy', 'column': 'signal', 'x_column': 'signal',
                   'method_version': 'kiha-xy-v2', 'include_metadata': True,
                   'x_range': [399, 401], 'sources': [{'test': 'alpha', 'tp_id': 7,
                   'expected_i0': 35, 'expected_i1': 187, 'expected_time_column': 'clock'}]}
        archive, document = self.unpack(self.client.post('/api/xy-export', json=payload))
        plot = document['plots'][0]; record = plot['sources'][0]
        self.assertEqual(record['exported_rows'], 4)
        self.assertEqual(record['exported_centers']['first_index'], 70)
        self.assertEqual(record['processing']['finite_pairs'], 151)
        self.assertEqual(record['processing']['nonfinite_pairs'], 1)
        self.assertEqual(len(record['variables']), 1)
        rows = list(csv.DictReader(io.StringIO(archive.read(plot['file']).decode())))
        self.assertEqual(list(rows[0])[-1], 'signal [X/Y]')

    def test_display_responses_carry_equations_with_values_and_original_filter_separation(self):
        routes = ['testpoints/7/data?cols=signal', 'data?cols=signal&display=line',
                  'xy?x=signal&y_col=signal&tp_id=7', 'spectrum?col=signal&tp_id=7',
                  'filter?cols=signal&type=despike&tp_id=7']
        for route in routes:
            with self.subTest(route=route):
                response = self.client.get('/api/tests/alpha/' + route)
                self.assertEqual(response.status_code, 200, response.text)
                value = response.json()
                info = value['series']['signal']['analysis'] if 'testpoints/' in route else value['analysis']
                self.assertEqual([eq['name'] for eq in info['equations']], ['base', 'signal'])
                if route.startswith('filter'):
                    self.assertEqual(info['filter']['window_samples'], 25)
                    self.assertEqual(info['filter']['max_spike_samples'], 10)

    def test_legacy_and_cyclic_provenance_terminate_and_do_not_invent_history(self):
        meta = store.get_meta('alpha'); meta.pop('derived_variables')
        record = analysis.source_context('alpha', meta, ['signal'], 0, 2)
        self.assertEqual(record['equation_status'], 'not_recorded')
        self.assertEqual(record['equations'], [])
        meta['derived_variables'] = {'signal': {'expression': '{signal}', 'dependencies': ['signal']}}
        record = analysis.source_context('alpha', meta, ['signal'], 0, 2)
        self.assertEqual(len(record['equations']), 1)

    def test_metadata_is_captured_under_same_lock_and_not_reread_after_serializing(self):
        original = plot_export._write_source
        def mutate_after(output, source, column, data, filter_spec, x_range, meta, i0, i1, **kwargs):
            rows = original(output, source, column, data, filter_spec, x_range, meta, i0, i1, **kwargs)
            updated = dict(meta); updated['derived_variables'] = []
            store.write_json_atomic(self.tests / 'alpha/meta.json', updated)
            return rows
        with patch.object(plot_export, '_write_source', side_effect=mutate_after):
            _, document = self.unpack(self.client.post('/api/plot-export', json=self.time_request(data='original')))
        self.assertEqual(len(document['plots'][0]['sources'][0]['equations']), 2)

    def test_late_sidecar_failure_closes_staged_files_and_never_sends_attachment(self):
        opened = []; original = plot_export.tempfile.SpooledTemporaryFile
        def track(*args, **kwargs):
            result = original(*args, **kwargs); opened.append(result); return result
        with patch.object(plot_export.tempfile, 'SpooledTemporaryFile', side_effect=track), \
             patch.object(analysis, 'MAX_METADATA_BYTES', 100):
            for route, payload in (
                ('/api/plot-export', self.time_request()),
                ('/api/plot-export/bundle', {'layout': '2x2', 'include_metadata': True,
                                           'plots': [{'slot': 1, 'request': self.time_request()}]})):
                response = self.client.post(route, json=payload)
                self.assertEqual(response.status_code, 400)
                self.assertNotIn('content-disposition', response.headers)
        self.assertTrue(opened and all(item.closed for item in opened))

    def test_image_package_preserves_loaded_context_and_checks_hash_without_source_reread(self):
        png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a8f8AAAAASUVORK5CYII=')
        document = analysis.document('png', [{'kind': 'xy', 'sources': [{'test': 'deleted',
                                      'loaded': {'equations': [{'expression': '{old} * 2'}]}}]}])
        document['filename'] = '../plot µV.png'
        with patch.object(store, 'get_meta', side_effect=AssertionError('must not reread image sources')):
            archive, result = self.unpack(self.client.post('/api/plot-image-export',
                content=json.dumps(document).encode() + b'\n' + png))
        self.assertEqual(result['file']['sha256'], hashlib.sha256(png).hexdigest())
        self.assertEqual(archive.read(result['file']['name']), png)
        self.assertNotIn('/', result['file']['name'])
        self.assertEqual(result['plots'], document['plots'])
        self.assertIn('no_source_reread', result['provenance_origin'])

    def test_image_invalid_or_oversized_payloads_never_send_attachments(self):
        for content in (b'not json\nimage', b'{"format":"png"}\n', b'NaN\n', b'no newline'):
            with self.subTest(content=content):
                response = self.client.post('/api/plot-image-export', content=content)
                self.assertEqual(response.status_code, 400)
                self.assertNotIn('content-disposition', response.headers)
        document = analysis.document('png', [3])
        document['filename'] = 'plot.png'
        self.assertEqual(self.client.post('/api/plot-image-export', content=json.dumps(document).encode() + b'\n').status_code, 400)
        document['plots'] = [{'sources': [{'test': 'alpha'}]}]
        document['filename'] = '\ud800.png'
        self.assertEqual(self.client.post('/api/plot-image-export', content=json.dumps(document).encode() + b'\n').status_code, 400)
        with self.assertRaises(HTTPException) as caught:
            analysis.encode({'label': '\ud800'})
        self.assertEqual(caught.exception.status_code, 400)
        with patch.object(image_export, 'MAX_IMAGE_BYTES', 1), patch.object(analysis, 'MAX_METADATA_BYTES', 1):
            self.assertEqual(self.client.post('/api/plot-image-export', content=b'123').status_code, 413)
