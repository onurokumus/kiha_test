"""In-process coordination for test data and the test directory catalog.

The backend is intentionally run as a single process.  Within that process,
FastAPI executes synchronous endpoints and background ingestion in worker
threads, so filesystem moves must be coordinated with Parquet readers.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from functools import wraps
import os
import sys
from threading import BoundedSemaphore, Condition, Lock
from typing import Callable, Iterator, ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")


class ReaderWriterLock:
    """A small writer-priority reader/writer lock.

    Writer priority matters here: once delete or rename is waiting, a stream of
    plot refreshes must not starve it indefinitely.
    """

    def __init__(self) -> None:
        self._condition = Condition(Lock())
        self._readers = 0
        self._writer = False
        self._waiting_writers = 0

    @contextmanager
    def read(self) -> Iterator[None]:
        with self._condition:
            while self._writer or self._waiting_writers:
                self._condition.wait()
            self._readers += 1
        try:
            yield
        finally:
            with self._condition:
                self._readers -= 1
                if self._readers == 0:
                    self._condition.notify_all()

    @contextmanager
    def write(self) -> Iterator[None]:
        with self._condition:
            self._waiting_writers += 1
            try:
                while self._writer or self._readers:
                    self._condition.wait()
                self._writer = True
            finally:
                self._waiting_writers -= 1
        try:
            yield
        finally:
            with self._condition:
                self._writer = False
                self._condition.notify_all()


_registry_guard = Lock()
_test_locks: dict[str, ReaderWriterLock] = {}
_catalog_lock = ReaderWriterLock()

# Polars/Arrow calls release the GIL and run native work.  On Windows with
# Python 3.14, concurrent collects have produced reproducible native access
# violations (the whole API process exits, so no exception handler can help).
# Keep Windows conservative by default; other platforms retain parallel reads.
_default_read_slots = (
    1 if sys.platform == "win32" and sys.version_info >= (3, 14) else 4)
_data_read_slots = BoundedSemaphore(max(
    1, int(os.environ.get("KIHA_MAX_CONCURRENT_READS", _default_read_slots))))


def _test_key(name: str) -> str:
    # Match the host filesystem's name semantics (case-insensitive on Windows,
    # case-sensitive on Linux).
    return os.path.normcase(name)


def _test_lock(name: str) -> ReaderWriterLock:
    name = _test_key(name)
    with _registry_guard:
        return _test_locks.setdefault(name, ReaderWriterLock())


def test_read(name: str):
    return _test_lock(name).read()


def test_write(name: str):
    return _test_lock(name).write()


def drop_test_lock(*names: str) -> None:
    """Forget the per-test RW lock(s) for tests that no longer exist under their
    old name (deleted or renamed), so the registry does not grow without bound
    for the life of the process (bug 4.8).

    Safe: a thread already holding — or mid-acquire on — the lock keeps its own
    reference to the object, and a later request for the same name simply lazily
    creates a fresh lock. Call only after the directory is gone/renamed under
    catalog_write, where delete/rename/restore are already serialized, so there
    is no live structural operation on the old name to protect."""
    with _registry_guard:
        for name in names:
            _test_locks.pop(_test_key(name), None)


@contextmanager
def tests_write(*names: str) -> Iterator[None]:
    """Lock several names in stable order so rename cannot deadlock."""
    with ExitStack() as stack:
        for name in sorted({_test_key(name) for name in names}):
            stack.enter_context(test_write(name))
        yield


def catalog_read():
    return _catalog_lock.read()


def catalog_write():
    return _catalog_lock.write()


@contextmanager
def data_read(name: str) -> Iterator[None]:
    """Per-test read lock + process-wide read slot as one context manager, for
    code that cannot use the with_test_read decorator (e.g. a StreamingResponse
    body, which runs after its endpoint returned and released decorator-held
    locks).

    Ordering is the per-test read lock FIRST, then the global slot. A read of a
    test whose writer is active (a rebuild/ingest holding test_write) therefore
    blocks on test_read WITHOUT holding a slot, so it can no longer starve reads
    of every OTHER test by parking on the semaphore while blocked (bug 2.1).
    This cannot deadlock: writers never take a slot, so no thread ever holds a
    slot while waiting for a per-test lock — there is no cycle. The slot still
    bounds concurrent NATIVE reads (it wraps the yield where the caller collects)
    which is its only job (the Windows py3.14 crash gate).
    """
    with test_read(name):
        with _data_read_slots:
            yield


def with_test_read(function: Callable[P, R]) -> Callable[P, R]:
    """Wrap a FastAPI endpoint whose first argument is the test name."""
    @wraps(function)
    def wrapped(name: str, *args, **kwargs):
        with data_read(name):
            return function(name, *args, **kwargs)

    return wrapped


def with_test_write(function: Callable[P, R]) -> Callable[P, R]:
    """Wrap a FastAPI endpoint whose first argument is the test name."""
    @wraps(function)
    def wrapped(name: str, *args, **kwargs):
        with test_write(name):
            return function(name, *args, **kwargs)

    return wrapped
