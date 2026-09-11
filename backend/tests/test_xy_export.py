"""XY native-pair display/export scopes, precision, compatibility and staging."""
from contextlib import contextmanager
import csv
import hashlib
import io
import math
from unittest.mock import patch
import zipfile

import numpy as np
import polars as pl
from fastapi import HTTPException

from app import locks, plot_export, store, xy_export
from .test_plot_export import PlotExportFixture


class XYExportTests(PlotExportFixture):
    def setUp(self):
        super().setUp()
        self.addCleanup(self.client.close)
        self.n = 12017
        self.indices = np.arange(self.n)
        self.x = np.sin(self.indices / 71.)
        self.y = (self.indices % 29 - 14) * 1.234567890123456e-10
        self.x[[101, 113, 501]] = [np.nan, np.inf, -np.inf]
        self.y[[101, 150, 777]] = np.nan
        self.times = 100.125 + self.indices / 1024
        self.points = [{'id': 7, 'start_idx': 100, 'end_idx': 10010,
                        'start_s': 100.5, 'end_s': 102.5}]
        self.write_test('pairs', self.times, self.y, self.points, fs=1024.,
                        extra={'position': self.x, 'tiny, µV': self.y, 'secret': np.ones(self.n)})

    def payload(self, **overrides):
        return {'kind': 'xy', 'column': 'signal', 'x_column': 'position',
            'method_version': 'kiha-xy-v2', 'sources': [{'test': 'pairs', 'tp_id': 7,
                'expected_i0': 100, 'expected_i1': 10010, 'expected_time_column': 'clock'}],
            'x_range': None, 'y_range': None, **overrides}

    def export(self, **overrides):
        return self.client.post('/api/xy-export', json=self.payload(**overrides))

    def xy(self, **params):
        response = self.client.get('/api/tests/pairs/xy', params={
            'x': 'position', 'y_col': 'signal', 'tp_id': 7, **params})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def wanted(self, i0=100, i1=10010, x=None, y=None):
        x = self.x if x is None else x; y = self.y if y is None else y
        return [i for i in range(i0, i1) if math.isfinite(x[i]) and math.isfinite(y[i])]

    def check_rows(self, rows, indices, *, x=None, y=None, ycol='signal'):
        x = self.x if x is None else x; y = self.y if y is None else y
        self.assertEqual([int(row['sample_index']) for row in rows], indices)
        np.testing.assert_array_equal([float(row['position [X]']) for row in rows], x[indices])
        np.testing.assert_array_equal([float(row[f'{ycol} [Y]']) for row in rows], y[indices])
        np.testing.assert_array_equal([float(row['time_s']) for row in rows], self.times[indices])

    def test_display_uses_exact_tp_rows_native_precision_and_complete_pair_counts(self):
        result = self.xy(); pairs = result['series']['signal']
        self.assertEqual((result['i0'], result['i1'], result['tp_id']), (100, 10010, 7))
        self.assertEqual(result['method_version'], 'kiha-xy-v2')
        wanted = self.wanted(); stride = math.ceil(9910/3000)
        sampled = [i for i in wanted if (i-100) % stride == 0]
        self.assertEqual(pairs['sample_indices'], sampled)
        np.testing.assert_array_equal(pairs['x'], self.x[sampled])
        np.testing.assert_array_equal(pairs['y'], self.y[sampled])
        self.assertEqual(pairs['finite_count'], len(wanted))
        self.assertEqual(pairs['missing_pair_count'], 5)
        self.assertEqual(result['time_start_s'], self.times[100])
        self.assertEqual(result['time_end_s'], self.times[10009])

    def test_native_csv_never_uses_reduced_json_and_preserves_order_precision(self):
        with patch.object(store, 'read_xy', side_effect=AssertionError('display path forbidden')):
            rows = self.rows(self.export())
        self.check_rows(rows, self.wanted())
        self.assertNotIn('secret', ','.join(rows[0]))
        self.assertEqual(rows[0]['missing_values'], 'omit_nonfinite_pairs')
        self.assertEqual(rows[0]['source_i0'], '100'); self.assertEqual(rows[0]['source_i1'], '10010')

    def test_both_axis_rectangle_and_y_only_crop_keep_exact_native_pairs(self):
        lo, hi = -0.5, 0.7; bottom, top = self.y[105], self.y[110]
        for xb, yb in (([lo, hi], [bottom, top]), (None, [bottom, top]), ([lo, hi], None)):
            with self.subTest(x=xb, y=yb):
                expected = [i for i in self.wanted() if (xb is None or lo <= self.x[i] <= hi)
                            and (yb is None or bottom <= self.y[i] <= top)]
                rows = self.rows(self.export(x_range=xb, y_range=yb))
                self.check_rows(rows, expected)

    def test_tiny_axis_crop_does_not_use_a_unit_sized_tolerance_floor(self):
        values = np.array([1e-20, 2e-20, 3e-20, 4e-20])
        np.testing.assert_array_equal(xy_export._inside(values, (2e-20, 3e-20)), [False, True, True, False])
        rows = self.rows(self.export(y_range=[self.y[104], self.y[104]]))
        self.assertTrue(all(float(row['signal [Y]']) == self.y[104] for row in rows))

    def test_x_equals_y_deduplicates_storage_and_exports_one_axis_column(self):
        rows = self.rows(self.export(x_column='signal'))
        self.assertEqual(list(rows[0])[-1], 'signal [X/Y]')
        self.assertNotIn('signal [X]', rows[0]); self.assertNotIn('signal [Y]', rows[0])
        expected = self.wanted(x=self.y)
        self.assertEqual([int(row['sample_index']) for row in rows], expected)
        np.testing.assert_array_equal([float(row['signal [X/Y]']) for row in rows], self.y[expected])
        display = self.xy(x='signal')['series']['signal']
        self.assertEqual(display['x'], display['y'])

    def test_time_may_be_either_axis_and_retains_its_axis_role(self):
        for xcol, ycol in (('clock', 'signal'), ('position', 'clock'), ('clock', 'clock')):
            with self.subTest(x=xcol, y=ycol):
                display = self.xy(x=xcol, y_col=ycol)
                pairs = display['series'][ycol]
                expected_time = self.times[pairs['sample_indices']]
                # Stored time stays at the source origin, even for a later TP.
                if xcol == 'clock':
                    np.testing.assert_array_equal(pairs['x'], expected_time)
                if ycol == 'clock':
                    np.testing.assert_array_equal(pairs['y'], expected_time)
                self.assertEqual(display['time_column'], 'clock')
                self.assertEqual((display['i0'], display['i1']), (100, 10010))
                response = self.export(x_column=xcol, column=ycol)
                rows = self.rows(response)
                name = 'clock [X/Y]' if xcol==ycol else 'clock [X]' if xcol=='clock' else 'clock [Y]'
                self.assertTrue(all(float(row[name])==float(row['time_s']) for row in rows))

    def test_exact_y_column_supports_commas_unicode_and_legacy_y_list(self):
        result = self.xy(y_col='tiny, µV')
        self.assertIn('tiny, µV', result['series'])
        rows = self.rows(self.export(column='tiny, µV'))
        self.check_rows(rows, self.wanted(), ycol='tiny, µV')
        response = self.client.get('/api/tests/pairs/xy', params={'x':'position','y':'signal,position,signal'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(set(response.json()['series']), {'signal','position'})

    def test_stride_fallback_and_all_missing_sources_are_distinguished(self):
        x = np.full(16, np.nan); x[3] = 1.234567890123e-12
        self.write_test('sparse', np.arange(16), np.ones(16), [], fs=1., extra={'position':x})
        response = self.client.get('/api/tests/sparse/xy?x=position&y=signal&max_pts=4')
        pairs = response.json()['series']['signal']
        self.assertEqual(pairs['sample_indices'], [3]); self.assertTrue(pairs['fallback_first_finite'])
        self.assertEqual(pairs['finite_count'], 1); self.assertEqual(pairs['missing_pair_count'], 15)
        x[3] = np.nan
        self.write_test('sparse', np.arange(16), np.ones(16), [], fs=1., extra={'position':x})
        result = self.client.get('/api/tests/sparse/xy?x=position&y=signal').json()['series']['signal']
        self.assertEqual(result['x'], []); self.assertEqual(result['finite_count'], 0)
        response = self.export(sources=[{'test':'sparse','expected_i0':0,'expected_i1':16,'expected_time_column':'clock'}])
        self.assertEqual(response.status_code, 400); self.assertIn('no finite pairs', response.text)

    def test_full_and_legacy_open_tp_bounds_match_authoritative_rows(self):
        sources = [{'test':'pairs','t0':float(self.times[20]),'t1':float(self.times[80]),
                    'expected_i0':20,'expected_i1':81,'expected_time_column':'clock'}]
        rows = self.rows(self.export(sources=sources)); self.check_rows(rows, list(range(20,81)))
        self.assertTrue(all(row['test_point_id']=='' for row in rows))
        store.write_json_atomic(self.tests/'pairs'/'testpoints.json', {'test_points':[
            {'id':12,'start_s':float(self.times[20]),'end_s':None},
            {'id':13,'start_s':float(self.times[80]),'end_s':float(self.times[100])}]})
        result = self.xy(tp_id=12)
        self.assertEqual((result['i0'], result['i1']), (20,80))
        sources = [{'test':'pairs','tp_id':12,'expected_i0':20,'expected_i1':80,'expected_time_column':'clock'}]
        self.check_rows(self.rows(self.export(sources=sources)), list(range(20,80)))

    def test_cross_test_sources_remain_independent_including_large_ids(self):
        large = 2**70+17
        self.write_test('other', np.arange(79)/500, np.arange(79)*2,
                        [{'id':large,'start_s':0,'start_idx':2,'end_idx':79}], fs=500.,
                        time_column='elapsed', extra={'position':np.arange(79)[::-1]})
        sources=self.payload()['sources']+[{'test':'other','tp_id':large,'expected_i0':2,
            'expected_i1':79,'expected_time_column':'elapsed'}]
        rows=self.rows(self.export(sources=sources)); other=[r for r in rows if r['source_test']=='other']
        self.assertEqual(len(other),77); self.assertEqual(other[0]['test_point_id'],str(large))
        self.assertEqual([int(r['sample_index']) for r in other],list(range(2,79)))
        self.assertTrue(all(r['source_fs_hz']=='500' and r['time_column']=='elapsed' for r in other))

    def test_validation_readiness_stale_bounds_and_empty_view_fail_before_attachment(self):
        cases=[({'column':'absent'},400),({'x_column':'absent'},400),({'method_version':'old'},422),
            ({'x_range':[1,0]},422),({'y_range':[1,0]},422),({'filter':{}},422),({'x_range':[100,200]},400)]
        for key,value,status in [('expected_i1',10009,409),('expected_time_column','old',409),
                ('tp_id',999,404),('t0',0,422),('test','../pairs',400)]:
            sources=self.payload()['sources'];sources[0][key]=value;cases.append(({'sources':sources},status))
        for changes,status in cases:
            with self.subTest(changes=changes):
                response=self.export(**changes);self.assertEqual(response.status_code,status,response.text)
                self.assertNotIn('content-disposition',response.headers)
        store.write_json_atomic(self.tests/'pairs'/'status.json',{'status':'rebuilding'})
        self.assertEqual(self.export().status_code,409)

    def test_xy_query_rejects_ambiguous_invalid_or_unknown_context(self):
        for params,status in [({'tp_id':7,'t0':0},400),({'tp_id':999},404),({'t0':'nan'},400),
                ({'t0':2,'t1':1},400),({'y_col':'signal','y':'position'},400),({'y_col':'absent'},400)]:
            with self.subTest(params=params):
                query={'x':'position','y_col':'signal',**params}
                response=self.client.get('/api/tests/pairs/xy',params=query)
                self.assertEqual(response.status_code,status,response.text)

    def bundle(self, plots=None, layout='2x2'):
        return self.client.post('/api/xy-export/bundle',json={'layout':layout,'plots':plots or [
            {'slot':1,'request':self.payload()}, {'slot':4,'request':self.payload(y_range=[0,1e-9])}]})

    def test_zip_keeps_per_slot_pair_and_crop_source_files_unchanged(self):
        before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.tests.rglob('*') if p.is_file()}
        response=self.bundle();self.assertEqual(response.status_code,200,response.text)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            names=archive.namelist();self.assertTrue(names[0].startswith('01_slot-1_'));self.assertTrue(names[1].startswith('02_slot-4_'))
            for name,cropped in zip(names,[False,True]):
                rows=list(csv.DictReader(io.StringIO(archive.read(name).decode())))
                self.check_rows(rows,[i for i in self.wanted() if not cropped or 0<=self.y[i]<=1e-9])
        self.assertEqual(before,{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.tests.rglob('*') if p.is_file()})

    def test_bundle_preflight_rejects_bad_x_and_aggregate_budget_before_reads(self):
        with patch.object(store,'iter_xy_batches') as read:
            response=self.bundle([{'slot':1,'request':self.payload()}, {'slot':4,'request':self.payload(x_column='absent')}])
            self.assertEqual(response.status_code,400);self.assertIn('Plot 4',response.text);read.assert_not_called()
            with patch.object(plot_export,'MAX_EXPORT_ROWS',10000):
                self.assertEqual(self.bundle().status_code,400);read.assert_not_called()
        for slots in ([1,1],[4,1],[1,2,3,4,5]):
            with self.subTest(slots=slots):
                self.assertEqual(self.bundle([{'slot':s,'request':self.payload()} for s in slots]).status_code,422)

    def test_late_empty_slot_closes_all_files_and_no_partial_zip(self):
        factory=plot_export.tempfile.SpooledTemporaryFile;files=[]
        def temporary(*args,**kwargs):
            result=factory(*args,**kwargs);files.append(result);return result
        with patch.object(plot_export.tempfile,'SpooledTemporaryFile',temporary):
            response=self.bundle([{'slot':1,'request':self.payload()}, {'slot':4,'request':self.payload(y_range=[100,200])}])
        self.assertEqual(response.status_code,400);self.assertIn('Plot 4',response.text)
        self.assertNotIn('content-disposition',response.headers)
        self.assertEqual(len(files),3);self.assertTrue(all(f.closed for f in files))

    def test_revalidation_under_lock_and_actual_budget_cannot_be_bypassed(self):
        @contextmanager
        def changed(name, **kwargs):
            store.write_json_atomic(self.tests/name/'testpoints.json',{'test_points':[{**self.points[0],'end_idx':10009}]})
            with locks.data_read(name, **kwargs): yield
        with patch.object(plot_export,'data_read',changed):
            self.assertEqual(self.export().status_code,409)
        store.write_json_atomic(self.tests/'pairs'/'testpoints.json',{'test_points':self.points})
        with patch.object(plot_export,'MAX_EXPORT_ROWS',9910):
            with self.assertRaises(HTTPException):
                xy_export.prepare_export(xy_export.XYExportRequest.model_validate(self.payload()),row_budget=plot_export.ExportRowBudget(1))

    def test_parquet_handles_close_on_normal_early_and_failed_consumption(self):
        factory=store.pq.ParquetFile;handles=[]
        def tracked(*args,**kwargs):
            result=factory(*args,**kwargs);handles.append(result);return result
        with patch.object(store.pq,'ParquetFile',tracked):
            generator=store.iter_xy_batches('pairs',['clock','position','signal'],100,10010)
            next(generator);generator.close()
            self.rows(self.export())
            with patch.object(xy_export.pa_csv,'write_csv',side_effect=ValueError('isolated write failure')):
                with self.assertRaises(ValueError): self.export()
        self.assertTrue(handles and all(h.closed for h in handles))
