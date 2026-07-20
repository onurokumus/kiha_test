"""Shared test base: an isolated data dir patched into every module that reads
`TESTS_DIR`, plus `main.TRASH_DIR` (bug 6.11).

Subclasses call `super().setUp()` and then build their own fixtures; there is no
per-file `TemporaryDirectory` + `patch.object(..., "TESTS_DIR", ...)` dance to
copy, and the easy-to-forget `TRASH_DIR` patch (whose omission is exactly how a
delete/rename test could silently touch the real trash dir) is now automatic.

Named `DataDirTestCase`, not `Test*`, so pytest does not try to collect the base
class itself.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import dsp, edit, ingest, main, split, store

# Every module that did `from .config import TESTS_DIR` holds its own binding, so
# each must be patched independently for a test to be fully isolated on disk.
_TESTS_DIR_MODULES = (main, store, ingest, edit, dsp, split)


class DataDirTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tests = self.root / "tests"
        self.trash = self.root / "trash"
        self.tests.mkdir()

        for module in _TESTS_DIR_MODULES:
            patcher = patch.object(module, "TESTS_DIR", self.tests)
            patcher.start()
            self.addCleanup(patcher.stop)
        trash_patcher = patch.object(main, "TRASH_DIR", self.trash)
        trash_patcher.start()
        self.addCleanup(trash_patcher.stop)
