"""Cancellation stops real export work and frees resources; trackers are bounded."""
import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import io
import json
from threading import BoundedSemaphore, Event
import time
from unittest.mock import patch

from app import dsp, export_progress as progress, image_export, locks, plot_export, store
from .test_plot_export import PlotExportFixture


class ExportProgressTests(PlotExportFixture):
    def setUp(self):
        super().setUp()
        self.addCleanup(self.client.close)
        self.registry = patch.object(progress, '_registry', {})
        self.registry.start(); self.addCleanup(self.registry.stop)

    def create(self):
        result = self.client.post('/api/export-progress')
        self.assertEqual(result.status_code, 201, result.text)
        return result.json()['id']

    def status(self, identifier):
        return self.client.get('/api/export-progress/' + identifier).json()

    def cancel(self, identifier):
        return self.client.delete('/api/export-progress/' + identifier).json()

    def post(self, identifier, **kwargs):
        return self.client.post('/api/plot-export', headers={'X-Export-ID': identifier}, json=self.payload(**kwargs))

    def test_completed_failure_precancel_reuse_and_legacy_compatibility(self):
        identifier = self.create()
        response = self.post(identifier, data='original', filter=None)
        self.assertEqual(response.content, self.export(data='original', filter=None).content)
        self.assertEqual(self.status(identifier)['state'], 'completed')
        self.assertEqual(self.post(identifier).status_code, 409)
        self.assertEqual(self.cancel(identifier)['state'], 'completed')
        identifier = self.create(); self.cancel(identifier)
        self.assertEqual(self.post(identifier).status_code, 409)
        self.assertEqual(self.status(identifier)['state'], 'canceled')
        identifier = self.create()
        response = self.post(identifier, x_range=[99, 100])
        self.assertEqual(response.status_code, 400)
        self.assertNotIn('content-disposition', response.headers)
        self.assertEqual(self.status(identifier)['state'], 'failed')

    def test_cancel_between_native_csv_batches_closes_files_and_releases_source(self):
        identifier = self.create(); entered = Event(); release = Event(); seen = []; files = []
        original = store._iter_parquet_slice
        factory = plot_export.tempfile.SpooledTemporaryFile
        def track(*args, **kwargs):
            result = factory(*args, **kwargs); files.append(result); return result
        def batches(*args, **kwargs):
            with closing(original(*args, **kwargs)) as iterator:
                for item in iterator:
                    seen.append(item[0]); yield item
                    entered.set(); release.wait(3)
        with patch.object(store, '_iter_parquet_slice', batches), patch.object(plot_export.tempfile, 'SpooledTemporaryFile', track), ThreadPoolExecutor() as pool:
            future = pool.submit(self.post, identifier, data='original', filter=None)
            try:
                self.assertTrue(entered.wait(2))
                live = self.status(identifier)
                self.assertEqual(live['state'], 'running'); self.assertEqual(live['unit'], 'rows')
                self.assertGreater(live['completed'], 0)
                self.assertEqual(self.cancel(identifier)['state'], 'canceling')
            finally: release.set()
            response = future.result(3)
        self.assertEqual(response.status_code, 409)
        self.assertNotIn('content-disposition', response.headers)
        self.assertEqual(len(seen), 1)
        self.assertTrue(files and all(file.closed for file in files))
        self.assertEqual(self.status(identifier)['state'], 'canceled')
        with locks.test_write('alpha'): pass
        self.assertEqual(self.post(self.create()).status_code, 200)

    def test_cancel_during_native_filter_waits_for_block_then_skips_csv(self):
        identifier = self.create(); entered = Event(); release = Event()
        original = dsp.filtered_samples
        def native(*args, **kwargs):
            result = original(*args, **kwargs)
            entered.set(); release.wait(3)
            return result
        with patch.object(dsp, 'filtered_samples', native), patch.object(plot_export.pa_csv, 'write_csv') as write, ThreadPoolExecutor() as pool:
            future = pool.submit(self.post, identifier)
            try:
                self.assertTrue(entered.wait(2)); self.cancel(identifier)
                self.assertFalse(future.done())
                self.assertEqual(self.status(identifier)['state'], 'canceling')
            finally: release.set()
            self.assertEqual(future.result(3).status_code, 409)
        write.assert_not_called()
        self.assertEqual(self.status(identifier)['state'], 'canceled')

    def test_cancel_during_zip_copy_stops_later_slots_and_cleans_every_spool(self):
        identifier = self.create(); entered = Event(); release = Event(); files = []
        original = progress.copy_file; factory = plot_export.tempfile.SpooledTemporaryFile
        def copy(*args, **kwargs):
            entered.set(); release.wait(3); return original(*args, **kwargs)
        def track(*args, **kwargs):
            result = factory(*args, **kwargs); files.append(result); return result
        payload = {'layout': '2x2', 'include_metadata': True, 'plots': [{'slot': slot, 'request': self.payload()} for slot in (1, 4)]}
        with patch.object(progress, 'copy_file', copy), patch.object(plot_export.tempfile, 'SpooledTemporaryFile', track), ThreadPoolExecutor() as pool:
            future = pool.submit(self.client.post, '/api/plot-export/bundle', headers={'X-Export-ID': identifier}, json=payload)
            try:
                self.assertTrue(entered.wait(2)); self.assertEqual(self.status(identifier)['plot'], 1)
                self.cancel(identifier)
            finally: release.set()
            response = future.result(3)
        self.assertEqual(response.status_code, 409); self.assertNotIn('content-disposition', response.headers)
        self.assertEqual(len(files), 2); self.assertTrue(all(file.closed for file in files))

    def test_waiting_for_writer_and_native_slot_is_cancellable_without_leaking_lock(self):
        for waiting_on in ('writer', 'slot'):
            with self.subTest(waiting_on=waiting_on), ThreadPoolExecutor() as pool:
                identifier = self.create()
                gate = locks.test_write('alpha') if waiting_on == 'writer' else patch.object(locks, '_data_read_slots', BoundedSemaphore(0))
                with gate:
                    future = pool.submit(self.post, identifier)
                    deadline = time.monotonic() + 2
                    while self.status(identifier)['state'] != 'running' and time.monotonic() < deadline: time.sleep(.01)
                    self.cancel(identifier)
                    self.assertEqual(future.result(2).status_code, 409)
                with locks.test_write('alpha'): pass

    def test_image_cancel_during_package_closes_upload_and_output(self):
        identifier = self.create(); entered = Event(); release = Event(); files = []
        original = progress.copy_file; factory = image_export.tempfile.SpooledTemporaryFile
        def copy(*args, **kwargs):
            entered.set(); release.wait(3); return original(*args, **kwargs)
        def track(*args, **kwargs):
            result = factory(*args, **kwargs); files.append(result); return result
        png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a8f8AAAAASUVORK5CYII=')
        header = {'schema':'kiha-analysis-v1','format':'png','plots':[{'sources':[{'test':'alpha'}]}], 'filename':'plot.png'}
        with patch.object(progress, 'copy_file', copy), patch.object(image_export.tempfile, 'SpooledTemporaryFile', track), ThreadPoolExecutor() as pool:
            future = pool.submit(self.client.post, '/api/plot-image-export', headers={'X-Export-ID': identifier}, content=json.dumps(header).encode()+b'\n'+png)
            try:
                self.assertTrue(entered.wait(2)); self.cancel(identifier)
            finally: release.set()
            self.assertEqual(future.result(3).status_code, 409)
        self.assertEqual(len(files), 2); self.assertTrue(all(file.closed for file in files))

    def test_tracker_expiration_capacity_and_lost_lease(self):
        with patch.object(progress, 'MAX_ACTIVE', 1):
            identifier = self.create()
            self.assertEqual(self.client.post('/api/export-progress').status_code, 429)
            self.cancel(identifier)
            second = self.create()
        job = progress._find(second)
        job.state = 'running'; job.last_seen -= progress.LEASE_SECONDS + 1
        with self.assertRaises(progress.ExportCanceled): job.check()
        job.finish('canceled')
        job.finished_at -= progress.TERMINAL_SECONDS + 1
        self.assertEqual(self.client.get('/api/export-progress/'+second).status_code, 404)

    def test_disconnect_during_staging_cancels_without_abandoning_worker(self):
        class Request:
            headers = {}
            async def is_disconnected(self): return True
        opened = []
        def work():
            output = io.BytesIO(); opened.append(output)
            try:
                for _ in range(50):
                    time.sleep(.01); progress.checkpoint()
                self.fail('disconnected work continued')
            finally: output.close()
        async def exercise():
            from fastapi import HTTPException
            with self.assertRaises(HTTPException) as caught:
                await progress.run(Request(), work)
            self.assertEqual(caught.exception.status_code, 409)
        asyncio.run(exercise())
        self.assertTrue(all(file.closed for file in opened))

    def test_interrupted_image_upload_is_canceled_and_closes_incoming_spool(self):
        from fastapi import HTTPException
        from starlette.requests import ClientDisconnect
        files = []; factory = image_export.tempfile.SpooledTemporaryFile
        def track(*args, **kwargs):
            file = factory(*args, **kwargs); files.append(file); return file
        class Request:
            headers = {}
            async def stream(self):
                yield b'{"schema":"kiha-analysis-v1"}\n'
                raise ClientDisconnect()
            async def is_disconnected(self):
                raise AssertionError('monitor must not consume the upload')
        async def exercise():
            request = Request()
            with self.assertRaises(HTTPException) as caught:
                await progress.run(request, lambda: image_export.receive_image(request), async_work=True, uploading=True)
            self.assertEqual(caught.exception.status_code, 409)
        with patch.object(image_export.tempfile, 'SpooledTemporaryFile', track):
            asyncio.run(exercise())
        self.assertEqual(len(files), 1); self.assertTrue(files[0].closed)

    def test_disconnect_probe_consuming_cancel_does_not_hang_finished_export(self):
        async def exercise():
            entered = asyncio.Event()
            class Request:
                headers = {}
                async def is_disconnected(self):
                    entered.set()
                    try:
                        await asyncio.sleep(10)
                    except asyncio.CancelledError:
                        return False  # reproduce Starlette's nested cancel scope
            async def work():
                await entered.wait()
                return plot_export._temporary_response(io.BytesIO(b'done'), 4, 'plot.csv', 'text/csv', {})
            response = await asyncio.wait_for(progress.run(Request(), work, async_work=True), timeout=2)
            response._output.close()
        asyncio.run(exercise())

    def test_cancel_stalled_image_upload_does_not_wait_for_another_chunk(self):
        from fastapi import HTTPException
        identifier = self.create(); job = progress._find(identifier)
        files = []; factory = image_export.tempfile.SpooledTemporaryFile
        def track(*args, **kwargs):
            file = factory(*args, **kwargs); files.append(file); return file
        async def exercise():
            waiting = asyncio.Event()
            class Request:
                headers = {'x-export-id': identifier}
                async def stream(self):
                    yield b'partial image'
                    waiting.set()
                    await asyncio.Event().wait()
                async def is_disconnected(self):
                    raise AssertionError('monitor must not consume the upload')
            request = Request()
            task = asyncio.create_task(progress.run(request, lambda: image_export.receive_image(request), async_work=True, uploading=True))
            await asyncio.wait_for(waiting.wait(), 1)
            job.cancel()
            with self.assertRaises(HTTPException) as caught:
                await asyncio.wait_for(task, 2)
            self.assertEqual(caught.exception.status_code, 409)
        with patch.object(image_export.tempfile, 'SpooledTemporaryFile', track):
            asyncio.run(exercise())
        self.assertEqual(job.state, 'canceled')
        self.assertEqual(len(files), 1); self.assertTrue(files[0].closed)
