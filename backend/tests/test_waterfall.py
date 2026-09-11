import csv
import io
import json
import zipfile
from unittest.mock import patch

import numpy as np
from fastapi.testclient import TestClient

from app import main, store, waterfall
from ._base import DataDirTestCase
from . import test_spectrum_method as reference
from .test_spectrum_method import direct_dft


class WaterfallTests(DataDirTestCase):
    def write_signal(self, *args, **kwargs):
        meta = reference.SpectrumMethodTests.write_signal(self, *args, **kwargs)
        store.write_json_atomic(self.tests / kwargs.get('name', 'method') / 'status.json', {'status': 'ready'})
        return meta

    def setUp(self):
        super().setUp()
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)

    def get(self, **params):
        response = self.client.get('/api/tests/method/waterfall', params={'col': 'signal', 'nperseg': 128, **params})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_peak_amplitude_hann_and_nyquist(self):
        n = np.arange(1024)
        self.write_signal(7 + 3*np.sin(2*np.pi*16*n/128) + 2*(-1.)**n, 128)
        data = self.get()
        grid = np.array(data['magnitude'])
        np.testing.assert_allclose(grid[:, 16], 3, atol=1e-12)
        np.testing.assert_allclose(grid[:, -1], 2, atol=1e-12)
        self.assertEqual(grid.shape, (15, 65))
        self.assertEqual(data['method']['trailing_samples'], 0)
        self.assertEqual(data['method']['step_seconds'], .5)
        self.assertEqual(data['frequency_edges_hz'][0], 0)
        self.assertEqual(data['frequency_edges_hz'][-1], 64)

    def test_off_bin_matches_independent_direct_dft(self):
        n = np.arange(128)
        values = 4 + np.cos(2*np.pi*12.3*n/128)
        self.write_signal(values, 128)
        window = .5 - .5*np.cos(2*np.pi*n/128)
        centered = (values-values.mean())*window
        expected = [abs(direct_dft(centered,k))/sum(window)*(1 if k in (0,64) else 2) for k in range(65)]
        np.testing.assert_allclose(self.get()['magnitude'][0], expected, atol=3e-14)

    def test_chirp_ridge_moves_with_time(self):
        fs=256; t=np.arange(4096)/fs
        self.write_signal(np.sin(2*np.pi*(10*t+2*t*t)), fs)
        result=self.get(nperseg=256)
        peaks=np.argmax(result['magnitude'],axis=1)
        self.assertTrue(np.all(np.diff(peaks)>=1))
        self.assertGreater(peaks[-1]-peaks[0], 50)

    def test_reduction_is_exact_maximum_of_all_native_windows(self):
        rng=np.random.default_rng(42); self.write_signal(rng.normal(size=1024),128)
        native=self.get(overlap=75)
        with patch.object(waterfall,'MAX_TIMES',4), patch.object(waterfall,'MAX_FREQS',10):
            reduced=self.get(overlap=75)
        a=np.array(native['magnitude']); r=reduced['reduction']
        tf,ff=r['time_factor'],r['frequency_factor']
        expected=[[a[y:y+tf,x:x+ff].max() for x in range(0,a.shape[1],ff)] for y in range(0,len(a),tf)]
        np.testing.assert_array_equal(reduced['magnitude'],expected)
        self.assertEqual(reduced['time_edges_s'][-1],native['time_edges_s'][-1])
        self.assertEqual(reduced['frequency_edges_hz'][-1],64)

    def test_tp_rows_nonzero_time_origin_and_trailing_samples(self):
        self.write_signal(np.arange(1024,dtype=float),128,start=1000)
        store.write_json_atomic(self.tests/'method'/'testpoints.json',{'test_points':[
            {'id':7,'name':'Scope','start_s':1.,'end_s':3.,'start_idx':128,'end_idx':391}]})
        result=self.get(tp_id=7)
        self.assertEqual((result['i0'],result['i1']),(128,391))
        self.assertEqual(result['time_start_s'],1001)
        self.assertEqual(result['method']['frame_count'],3)
        self.assertEqual(result['method']['trailing_samples'],7)
        self.assertAlmostEqual(result['source_frame_start_s'][0],1001+63.5/128)
        self.assertAlmostEqual(result['time_edges_s'][0],31.5/128)

    def test_missing_values_preserve_time_and_match_index_interpolation(self):
        values=np.sin(np.arange(512)*.4); values[[0,17,511]]=np.nan
        self.write_signal(values,128); result=self.get()
        clean=np.interp(np.arange(512),np.flatnonzero(np.isfinite(values)),values[np.isfinite(values)])
        self.write_signal(clean,128)
        np.testing.assert_array_equal(result['magnitude'],self.get()['magnitude'])
        self.assertEqual(result['nan_count'],3)

    def test_rejects_invalid_scope_gap_short_and_all_missing(self):
        self.write_signal(np.ones(512),128)
        for params in ({'nperseg':100}, {'overlap':99}, {'nperseg':1024}, {'t0':3,'t1':1}, {'t0':0,'tp_id':3}, {'col':'absent'}):
            with self.subTest(params=params):
                response=self.client.get('/api/tests/method/waterfall',params={'col':'signal',**params})
                self.assertEqual(response.status_code,400,response.text)
        self.write_signal(np.full(512,np.nan),128)
        self.assertEqual(self.client.get('/api/tests/method/waterfall',params={'col':'signal','nperseg':128}).status_code,400)
        self.write_signal(np.ones(512),128,gaps=[[100,110]])
        self.assertEqual(self.client.get('/api/tests/method/waterfall',params={'col':'signal','nperseg':128}).status_code,400)

    def export_request(self):
        result=self.get()
        return {'kind':'waterfall','column':'signal','method_version':waterfall.VERSION,
            'nperseg':128,'overlap':50,'sources':[{'test':'method','expected_i0':result['i0'],
            'expected_i1':result['i1'],'expected_fs_hz':128}]}

    def test_csv_grid_and_metadata_match_loaded_values_and_crops(self):
        self.write_signal(np.sin(np.arange(1024)*.4),128)
        loaded=self.get(); payload=self.export_request(); payload['include_metadata']=True
        response=self.client.post('/api/waterfall-export',json=payload)
        self.assertEqual(response.status_code,200,response.text[:400] if response.status_code!=200 else '')
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            rows=list(csv.DictReader(io.StringIO(archive.read(next(n for n in archive.namelist() if n.endswith('.csv'))).decode())))
            metadata=json.loads(archive.read('analysis.json'))
        self.assertEqual(len(rows),15*65)
        np.testing.assert_array_equal([float(r['magnitude_U']) for r in rows], np.array(loaded['magnitude']).ravel())
        self.assertIn(waterfall.VERSION,json.dumps(metadata))
        payload.update(include_metadata=False,x_range=[10,12],y_range=[1,2])
        response=self.client.post('/api/waterfall-export',json=payload)
        self.assertEqual(response.status_code,200,response.text)
        cropped=list(csv.DictReader(io.StringIO(response.text)))
        self.assertTrue(0<len(cropped)<len(rows))
        self.assertTrue(all(float(r['frequency_start_hz'])<=12 and float(r['frequency_end_hz'])>=10 for r in cropped))

    def test_export_stale_bounds_rate_invalid_options_and_bundle(self):
        self.write_signal(np.arange(512,dtype=float),128)
        payload=self.export_request()
        for field,value in [('expected_i1',500),('expected_fs_hz',256)]:
            modified={**payload,'sources':[{**payload['sources'][0],field:value}]}
            self.assertEqual(self.client.post('/api/waterfall-export',json=modified).status_code,409)
        self.assertEqual(self.client.post('/api/waterfall-export',json={**payload,'overlap':90}).status_code,422)
        response=self.client.post('/api/waterfall-export/bundle',json={'layout':'2x2','include_metadata':True,
            'plots':[{'slot':1,'request':payload},{'slot':2,'request':payload}]})
        self.assertEqual(response.status_code,200,response.text[:400] if response.status_code!=200 else '')
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertEqual(sum(n.endswith('.csv') for n in archive.namelist()),2)
            self.assertIn('analysis.json',archive.namelist())

    def test_long_signal_grid_is_bounded_and_retains_late_activity(self):
        n=524288
        values=np.zeros(n)
        values[-8192:]=3*np.sin(2*np.pi*128*np.arange(8192)/2048)
        self.write_signal(values,2048)
        data=self.get(nperseg=2048,overlap=75)
        grid=np.asarray(data['magnitude'])
        self.assertLessEqual(grid.shape[0],waterfall.MAX_TIMES)
        self.assertLessEqual(grid.shape[1],waterfall.MAX_FREQS)
        self.assertEqual(data['method']['frame_count'],1+(n-2048)//512)
        self.assertAlmostEqual(grid[-1].max(),3,places=10)
        self.assertEqual(grid[0].max(),0)

    def test_legacy_settings_and_waterfall_default_roundtrip(self):
        from app.main import AppSettingsDefaults
        self.assertEqual(AppSettingsDefaults().specMode,'fft')
        self.assertEqual(AppSettingsDefaults(specMode='waterfall').model_dump()['specMode'],'waterfall')

    def test_detail_point_one_hz_preserves_close_tones_and_low_band_native_bins(self):
        fs = 2048
        t = np.arange(fs * 20) / fs
        self.write_signal(np.sin(2*np.pi*50*t) + .8*np.sin(2*np.pi*50.5*t), fs)
        result = self.get(high_detail=True, resolution_hz=.1, overlap=75,
                          frequency_min_hz=0, frequency_max_hz=200)
        method = result['method']
        self.assertEqual(method['version'], waterfall.DETAIL_VERSION)
        self.assertEqual(method['nperseg'], 20480)
        self.assertEqual(method['nfft'], 20480)
        self.assertEqual(method['window_seconds'], 10)
        self.assertEqual(method['step_seconds'], 2.5)
        self.assertEqual(method['bin_spacing_hz'], .1)
        self.assertEqual(method['requested_spacing_hz'], .1)
        self.assertEqual(method['padding'], 'none')
        self.assertEqual(result['reduction']['frequency_factor'], 1)
        self.assertEqual(result['reduction']['selected_native_bins'], 2001)
        grid = np.asarray(result['magnitude'])
        self.assertEqual(grid.shape, (5, 2001))
        np.testing.assert_allclose(grid[:, 500], 1., atol=1e-11)
        np.testing.assert_allclose(grid[:, 505], .8, atol=1e-11)
        self.assertLess(float(grid[:, 502:504].max()), 1e-11)

    def test_detail_rounds_sample_window_up_and_reports_executed_spacing(self):
        self.write_signal(np.sin(np.arange(5000) * .3), 1000.1)
        result = self.get(high_detail=True, resolution_hz=.5)
        self.assertEqual(result['method']['nperseg'], 2002)
        self.assertLessEqual(result['method']['bin_spacing_hz'], .5)
        self.assertEqual(result['method']['bin_spacing_hz'], 1000.1 / 2002)
        self.assertEqual(result['method']['window_seconds'], 2002 / 1000.1)

    def test_detail_clipped_band_preserves_interior_bin_amplitude(self):
        fs = 128
        t = np.arange(2048) / fs
        self.write_signal(3*np.sin(2*np.pi*12*t), fs)
        result = self.get(high_detail=True, resolution_hz=.5,
                          frequency_min_hz=11.9, frequency_max_hz=12.1)
        self.assertEqual(result['grid']['first_bin_index'], 24)
        self.assertEqual(result['grid']['stop_bin_index'], 25)
        np.testing.assert_allclose(np.asarray(result['magnitude'])[:, 0], 3, atol=1e-12)

    def test_detail_refines_original_frames_and_bins_before_exact_max_reduction(self):
        self.write_signal(np.random.default_rng(63).normal(size=4096), 128, start=1000)
        native = self.get(high_detail=True, overlap=75)
        f, t = np.asarray(native['frequency_edges_hz']), np.asarray(native['time_edges_s'])
        fbound, tbound = (8.2, 38.4), (5.1, 11.7)
        fi = np.flatnonzero((f[1:] >= fbound[0]) & (f[:-1] <= fbound[1]))
        ti = np.flatnonzero((t[1:] >= tbound[0]) & (t[:-1] <= tbound[1]))
        with patch.object(waterfall, 'DETAIL_MAX_TIMES', 4), patch.object(waterfall, 'DETAIL_MAX_FREQS', 10):
            result = self.get(high_detail=True, overlap=75,
                              frequency_min_hz=fbound[0], frequency_max_hz=fbound[1],
                              elapsed_min_s=tbound[0], elapsed_max_s=tbound[1])
        tf, ff = result['reduction']['time_factor'], result['reduction']['frequency_factor']
        selected = np.asarray(native['magnitude'])[np.ix_(ti, fi)]
        expected = [[selected[y:y+tf, x:x+ff].max()
                     for x in range(0, len(fi), ff)] for y in range(0, len(ti), tf)]
        np.testing.assert_array_equal(result['magnitude'], expected)
        np.testing.assert_array_equal(result['frequency_edges_hz'], f[np.r_[fi[::ff], fi[-1]+1]])
        np.testing.assert_array_equal(result['time_edges_s'], t[np.r_[ti[::tf], ti[-1]+1]])
        np.testing.assert_array_equal(result['source_frame_start_s'],
                                      np.asarray(native['source_frame_start_s'])[ti[::tf]])
        np.testing.assert_array_equal(result['source_frame_end_s'],
                                      np.asarray(native['source_frame_end_s'])[ti[np.minimum(np.arange(0, len(ti), tf)+tf, len(ti))-1]])
        self.assertEqual(result['grid']['full_time_range_s'], [t[0], t[-1]])
        self.assertEqual(result['grid']['full_frequency_range_hz'], [0, 64])
        self.assertEqual(result['grid']['first_frame_index'], int(ti[0]))
        self.assertGreater(result['grid']['first_frame_index'], 0)
        self.assertEqual(result['i0'], native['i0'])
        self.assertEqual(result['i1'], native['i1'])
        self.assertEqual(result['method']['frame_count'], native['method']['frame_count'])

    def test_detail_empty_viewports_retain_full_domains_and_skip_fft(self):
        self.write_signal(np.sin(np.arange(4096) * .3), 128)
        native = self.get(high_detail=True)
        for bounds in ({'frequency_min_hz': 100, 'frequency_max_hz': 200},
                       {'frequency_min_hz': -10, 'frequency_max_hz': -1},
                       {'elapsed_min_s': 100, 'elapsed_max_s': 200},
                       {'elapsed_min_s': -10, 'elapsed_max_s': -1}):
            with self.subTest(bounds=bounds), patch.object(waterfall.np.fft, 'rfft') as fft:
                result = self.get(high_detail=True, **bounds)
                fft.assert_not_called()
                for key in ('magnitude', 'frequency_edges_hz', 'time_edges_s',
                            'source_frame_start_s', 'source_frame_end_s'):
                    self.assertEqual(result[key], [])
                self.assertEqual(result['grid']['full_time_range_s'], native['grid']['full_time_range_s'])
                self.assertEqual(result['grid']['full_frequency_range_hz'], [0, 64])

    def test_detail_validation_short_source_and_window_limit(self):
        self.write_signal(np.ones(2048), 2048)
        cases = [({'resolution_hz': .5}, 'require high_detail'),
                 ({'frequency_min_hz': 0, 'frequency_max_hz': 200}, 'require high_detail'),
                 ({'high_detail': True, 'frequency_min_hz': 0}, 'both minimum and maximum'),
                 ({'high_detail': True, 'elapsed_max_s': 5}, 'both minimum and maximum'),
                 ({'high_detail': True, 'resolution_hz': .3}, 'supported frequency spacing'),
                 ({'high_detail': True, 'frequency_min_hz': 200, 'frequency_max_hz': 0}, 'finite and increasing'),
                 ({'high_detail': True, 'elapsed_min_s': 'nan', 'elapsed_max_s': 10}, 'finite and increasing'),
                 ({'high_detail': True, 'resolution_hz': .1}, 'requires 10 seconds')]
        for params, message in cases:
            with self.subTest(params=params):
                response = self.client.get('/api/tests/method/waterfall', params={'col': 'signal', **params})
                self.assertEqual(response.status_code, 400, response.text)
                self.assertIn(message, response.json()['detail'])
        with patch.object(waterfall, 'MAX_WINDOW_SAMPLES', 1024):
            response = self.client.get('/api/tests/method/waterfall', params={
                'col': 'signal', 'high_detail': True, 'resolution_hz': .5})
            self.assertEqual(response.status_code, 400)
            self.assertIn('more than 1024 samples', response.json()['detail'])

    def test_detail_fft_workspace_obeys_sample_budget(self):
        self.write_signal(np.sin(np.arange(16384) * .3), 128)
        original = waterfall.np.fft.rfft
        calls = []
        def observed_fft(values, *args, **kwargs):
            calls.append(values.shape)
            return original(values, *args, **kwargs)
        with patch.object(waterfall, 'MAX_BATCH_SAMPLES', 4096), patch.object(waterfall.np.fft, 'rfft', side_effect=observed_fft):
            self.get(high_detail=True, resolution_hz=.1, overlap=75)
        self.assertGreater(len(calls), 1)
        self.assertTrue(all(rows * columns <= 4096 and rows <= 64 for rows, columns in calls))
        self.assertTrue(all(columns == 1280 for _, columns in calls))

    def test_detail_csv_matches_refined_grid_actual_window_and_metadata(self):
        fs = 128
        self.write_signal(np.sin(np.arange(4096) * .3), fs, start=1000)
        result = self.get(high_detail=True, resolution_hz=.1, overlap=75,
                          frequency_min_hz=0, frequency_max_hz=20, elapsed_min_s=10, elapsed_max_s=20)
        payload = {'kind': 'waterfall', 'column': 'signal', 'method_version': waterfall.DETAIL_VERSION,
                   'nperseg': 1024, 'resolution_hz': .1, 'overlap': 75,
                   'grid_frequency_range_hz': [0, 20], 'grid_time_range_s': [10, 20], 'include_metadata': True,
                   'sources': [{'test': 'method', 'expected_i0': result['i0'], 'expected_i1': result['i1'],
                                'expected_fs_hz': fs}]}
        response = self.client.post('/api/waterfall-export', json=payload)
        self.assertEqual(response.status_code, 200, response.text[:400] if response.status_code != 200 else '')
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            rows = list(csv.DictReader(io.StringIO(archive.read(next(n for n in archive.namelist() if n.endswith('.csv'))).decode())))
            metadata = json.loads(archive.read('analysis.json'))
        np.testing.assert_array_equal([float(row['magnitude_U']) for row in rows], np.asarray(result['magnitude']).ravel())
        self.assertEqual({int(row['nperseg']) for row in rows}, {1280})
        self.assertEqual({row['method_version'] for row in rows}, {waterfall.DETAIL_VERSION})
        self.assertIn('grid_frequency_range_hz', json.dumps(metadata))
        self.assertIn('requested_spacing_hz', json.dumps(metadata))
        self.assertIn('first_frame_index', json.dumps(metadata))
        payload.update(include_metadata=False, x_range=[4, 5], y_range=[12, 16])
        response = self.client.post('/api/waterfall-export', json=payload)
        self.assertEqual(response.status_code, 200, response.text[:400])
        cropped = list(csv.DictReader(io.StringIO(response.text)))
        expected = [row for row in rows if float(row['frequency_start_hz']) <= 5 and float(row['frequency_end_hz']) >= 4
                    and float(row['elapsed_start_s']) <= 16 and float(row['elapsed_end_s']) >= 12]
        self.assertEqual(cropped, expected)
        for modified in ({**payload, 'method_version': waterfall.VERSION},
                         {**payload, 'grid_time_range_s': [20, 10]},
                         {**payload, 'resolution_hz': .3}):
            self.assertEqual(self.client.post('/api/waterfall-export', json=modified).status_code, 422)
        response = self.client.post('/api/waterfall-export', json={**payload, 'grid_frequency_range_hz': [100, 200]})
        self.assertEqual(response.status_code, 400)
        self.assertIn('viewport contains no cells', response.json()['detail'])
