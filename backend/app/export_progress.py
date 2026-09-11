"""Ephemeral progress for the existing staged export requests (one process).

Trackers own no files or detached jobs. The POST/response owns every spool;
canceling, disconnecting or losing the polling lease stops at a checkpoint.
Native library calls cannot be interrupted safely and finish their current block.
"""
import asyncio
from contextvars import ContextVar
from threading import Event, Lock
import time
import uuid

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

router = APIRouter()
LEASE_SECONDS = 45
TERMINAL_SECONDS = 120
MAX_ACTIVE = 8
MAX_TRACKERS = 64
TERMINAL = {'completed', 'canceled', 'failed'}


class ExportCanceled(Exception):
    pass


class Progress:
    def __init__(self, tracked=False):
        self.id = uuid.uuid4().hex
        self.tracked = tracked
        self.lock = Lock()
        self.canceled = Event()
        self.state = 'pending'
        self.stage = 'Waiting to start'
        self.completed = 0
        self.total = None
        self.unit = None
        self.plot = None
        self.source = None
        self.error = None
        self.last_seen = time.monotonic()
        self.finished_at = None
        self.uploading = False

    def snapshot(self, touch=False):
        with self.lock:
            if touch:
                self.last_seen = time.monotonic()
            return {key: getattr(self, key) for key in (
                'id', 'state', 'stage', 'completed', 'total', 'unit', 'plot', 'source', 'error')}

    def cancel(self):
        with self.lock:
            if self.state in TERMINAL:
                return
            self.canceled.set()
            if self.state == 'pending':
                self.state = 'canceled'
                self.finished_at = time.monotonic()
            else:
                self.state = 'canceling'

    def check(self):
        if self.tracked and time.monotonic() - self.last_seen > LEASE_SECONDS:
            self.cancel()
        if self.canceled.is_set():
            raise ExportCanceled()

    def update(self, stage, *, completed=0, total=None, unit=None, **context):
        self.check()
        with self.lock:
            self.stage, self.completed, self.total, self.unit = stage, completed, total, unit
            for key in ('plot', 'source'):
                if key in context:
                    setattr(self, key, context[key])

    def finish(self, state, error=None):
        with self.lock:
            if self.state in TERMINAL:
                return
            self.state = 'canceled' if self.canceled.is_set() else state
            self.error = error if self.state == 'failed' else None
            self.finished_at = time.monotonic()


_current = ContextVar('export_progress', default=None)
_registry = {}
_guard = Lock()


def current():
    return _current.get()


def checkpoint():
    if job := current():
        job.check()


def update(stage, **kwargs):
    if job := current():
        job.update(stage, **kwargs)


def copy_file(source, destination, *, stage='Packaging export'):
    start = source.tell()
    source.seek(0, 2)
    total = source.tell() - start
    source.seek(start)
    completed = 0
    update(stage, total=total, unit='bytes')
    while True:
        checkpoint()
        chunk = source.read(256 * 1024)
        if not chunk:
            break
        destination.write(chunk)
        completed += len(chunk)
        update(stage, completed=completed, total=total, unit='bytes')


async def upload_chunks(request):
    """Cancel a stalled receive without abandoning a spool write in a thread."""
    iterator = request.stream()
    try:
        while True:
            pending = asyncio.create_task(anext(iterator))
            try:
                while True:
                    done, _ = await asyncio.wait({pending}, timeout=.15)
                    checkpoint()
                    if done:
                        break
                try:
                    chunk = pending.result()
                except StopAsyncIteration:
                    return
            finally:
                if not pending.done():
                    pending.cancel()
                    try:
                        await pending
                    except asyncio.CancelledError:
                        pass
                elif not pending.cancelled():
                    pending.exception()  # consume a receive error racing Cancel
            yield chunk
    finally:
        await iterator.aclose()


def _prune():
    now = time.monotonic()
    for key, job in list(_registry.items()):
        if job.state == 'pending' and now - job.last_seen > LEASE_SECONDS:
            job.cancel()
        if job.finished_at is not None and now - job.finished_at > TERMINAL_SECONDS:
            del _registry[key]


def _find(identifier):
    with _guard:
        _prune()
        job = _registry.get(identifier)
    if job is None:
        raise HTTPException(404, 'Export progress expired or the server restarted. Try the export again.')
    return job


@router.post('/api/export-progress', status_code=201)
async def create_progress():
    with _guard:
        _prune()
        if sum(job.state not in TERMINAL for job in _registry.values()) >= MAX_ACTIVE:
            raise HTTPException(429, 'Too many exports are running. Cancel an export or wait for it to finish.')
        while len(_registry) >= MAX_TRACKERS:
            key = next((key for key, job in _registry.items() if job.state in TERMINAL), None)
            if key is None:
                raise HTTPException(429, 'Too many exports are running.')
            del _registry[key]
        job = Progress(tracked=True)
        _registry[job.id] = job
    return job.snapshot()


@router.get('/api/export-progress/{identifier}')
async def get_progress(identifier: str):
    return _find(identifier).snapshot(touch=True)


@router.delete('/api/export-progress/{identifier}')
async def cancel_progress(identifier: str):
    job = _find(identifier)
    job.cancel()
    return job.snapshot()


async def run(request: Request, work, *, async_work=False, uploading=False):
    identifier = request.headers.get('x-export-id')
    job = _find(identifier) if identifier else Progress()
    with job.lock:
        if job.state != 'pending':
            raise HTTPException(409, 'This export was already started or canceled. Start a new export.')
        job.state = 'running'
        job.uploading = uploading
    token = _current.set(job)
    stopped = asyncio.Event()

    async def monitor():
        while not stopped.is_set():
            # Never consume ASGI receive concurrently with an image upload.
            if not job.uploading and await request.is_disconnected():
                job.cancel()
            if job.tracked and time.monotonic() - job.last_seen > LEASE_SECONDS:
                job.cancel()
            await asyncio.sleep(.15)

    watcher = asyncio.create_task(monitor())
    worker = None
    try:
        job.check()
        worker = asyncio.create_task(work() if async_work else run_in_threadpool(work))
        # Cancellation cannot abandon a worker holding a temporary file/lock.
        response = await asyncio.shield(worker)
        try:
            job.check()
            job.update('Sending file')
        except ExportCanceled:
            response._output.close()
            raise
        return response
    except ExportCanceled:
        job.finish('canceled')
        raise HTTPException(409, 'Export canceled. No file was prepared.') from None
    except ClientDisconnect:
        job.cancel()
        job.finish('canceled')
        raise HTTPException(409, 'Export canceled during image upload.') from None
    except asyncio.CancelledError:
        job.cancel()
        if worker:
            try:
                response = await asyncio.shield(worker)
                response._output.close()
            except Exception:
                pass
        job.finish('canceled')
        raise
    except Exception as exc:
        job.finish('failed', str(exc.detail) if isinstance(exc, HTTPException) else 'Export failed. Try again.')
        raise
    finally:
        # is_disconnected uses its own cancel scope and may consume a task
        # cancellation at that instant. An explicit stop also ends the loop.
        stopped.set()
        watcher.cancel()
        try:
            await watcher
        except asyncio.CancelledError:
            pass
        _current.reset(token)
