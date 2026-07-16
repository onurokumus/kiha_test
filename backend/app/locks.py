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


def with_test_read(function: Callable[P, R]) -> Callable[P, R]:
    """Wrap a FastAPI endpoint whose first argument is the test name."""
    @wraps(function)
    def wrapped(name: str, *args, **kwargs):
        # The process-wide slot protects native dataframe readers. Acquire it
        # before the per-test lock so every request uses one stable ordering.
        with _data_read_slots:
            with test_read(name):
                return function(name, *args, **kwargs)

    return wrapped


def with_test_write(function: Callable[P, R]) -> Callable[P, R]:
    """Wrap a FastAPI endpoint whose first argument is the test name."""
    @wraps(function)
    def wrapped(name: str, *args, **kwargs):
        with test_write(name):
            return function(name, *args, **kwargs)

    return wrapped
