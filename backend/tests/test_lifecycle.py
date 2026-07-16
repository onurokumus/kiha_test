import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app import ingest, main, store
# Alias: the bare name `test_read` would be collected by pytest as a test.
from app.locks import test_read as acquire_test_read


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.tests = self.root / "tests"
        self.trash = self.root / "trash"
        self.tests.mkdir()

        self.patchers = [
            patch.object(main, "TESTS_DIR", self.tests),
            patch.object(main, "TRASH_DIR", self.trash),
            patch.object(store, "TESTS_DIR", self.tests),
            patch.object(ingest, "TESTS_DIR", self.tests),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def make_test(self, name="alpha", status="ready"):
        directory = self.tests / name
        directory.mkdir()
        store.write_json_atomic(directory / "status.json", {"status": status})
        store.write_json_atomic(directory / "meta.json", {"name": name})
        store.write_json_atomic(
            directory / "testpoints.json",
            {"version": 1, "test": name, "test_points": []},
        )
        return directory

    def test_delete_restore_and_rename_update_embedded_names(self):
        self.make_test()

        self.assertEqual(main.api_delete_test("alpha")["deleted"], "alpha")
        self.assertFalse((self.tests / "alpha").exists())
        self.assertTrue((self.trash / "alpha").is_dir())

        self.assertEqual(main.api_restore_test("alpha")["restored"], "alpha")
        self.assertTrue((self.tests / "alpha").is_dir())
        self.assertFalse((self.trash / "alpha").exists())

        self.assertEqual(main.api_rename_test("alpha", "beta")["name"], "beta")
        meta = json.loads((self.tests / "beta" / "meta.json").read_text())
        points = json.loads(
            (self.tests / "beta" / "testpoints.json").read_text())
        self.assertEqual(meta["name"], "beta")
        self.assertEqual(points["test"], "beta")

    def test_delete_and_rename_reject_ingesting_test(self):
        self.make_test(status="ingesting")

        for operation in (
            lambda: main.api_delete_test("alpha"),
            lambda: main.api_rename_test("alpha", "beta"),
        ):
            with self.assertRaises(HTTPException) as caught:
                operation()
            self.assertEqual(caught.exception.status_code, 409)
        self.assertTrue((self.tests / "alpha").is_dir())

    def test_delete_waits_for_an_active_reader(self):
        self.make_test()
        started = threading.Event()
        finished = threading.Event()

        def delete():
            started.set()
            main.api_delete_test("alpha")
            finished.set()

        with acquire_test_read("alpha"):
            worker = threading.Thread(target=delete)
            worker.start()
            self.assertTrue(started.wait(1))
            time.sleep(0.05)
            self.assertFalse(finished.is_set())
            self.assertTrue((self.tests / "alpha").is_dir())

        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertTrue(finished.is_set())
        self.assertTrue((self.trash / "alpha").is_dir())

    def test_failed_rename_restores_metadata(self):
        self.make_test()

        with patch.object(Path, "rename", side_effect=OSError("simulated")):
            with self.assertRaises(HTTPException) as caught:
                main.api_rename_test("alpha", "beta")

        self.assertEqual(caught.exception.status_code, 409)
        self.assertTrue((self.tests / "alpha").is_dir())
        meta = json.loads((self.tests / "alpha" / "meta.json").read_text())
        points = json.loads(
            (self.tests / "alpha" / "testpoints.json").read_text())
        self.assertEqual(meta["name"], "alpha")
        self.assertEqual(points["test"], "alpha")

    def test_same_name_rename_still_requires_an_existing_test(self):
        with self.assertRaises(HTTPException) as caught:
            main.api_rename_test("missing", "missing")
        self.assertEqual(caught.exception.status_code, 404)

    @unittest.skipUnless(os.path.normcase("A") == os.path.normcase("a"),
                         "case-insensitive filesystem behavior")
    def test_case_only_rename_does_not_deadlock(self):
        self.make_test("alpha")
        with self.assertRaises(HTTPException) as caught:
            main.api_rename_test("alpha", "ALPHA")
        self.assertEqual(caught.exception.status_code, 409)

    def test_startup_marks_interrupted_ingestion_as_error(self):
        directory = self.make_test(status="ingesting")
        main._recover_interrupted_ingests()
        status = json.loads((directory / "status.json").read_text())
        self.assertEqual(status["status"], "error")
        self.assertIn("interrupted", status["error"])

    def test_small_csv_ingests_to_ready_dataset(self):
        fixture = Path(__file__).parent / "fixtures" / "small.csv"
        meta = ingest.ingest_csv(fixture, "small", copy_raw=True)

        directory = self.tests / "small"
        status = json.loads((directory / "status.json").read_text())
        self.assertEqual(status["status"], "ready")
        self.assertEqual(meta["name"], "small")
        self.assertEqual(meta["n_rows"], 20)
        self.assertTrue((directory / "data.parquet").is_file())
        for level in (16, 256, 4096):
            self.assertTrue((directory / "pyramid" / f"L{level}.parquet").is_file())


if __name__ == "__main__":
    unittest.main()
