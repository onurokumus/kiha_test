import json
import os
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app import ingest, main, store
# Alias: the bare name `test_read` would be collected by pytest as a test.
from app.locks import test_read as acquire_test_read
from ._base import DataDirTestCase


class LifecycleTests(DataDirTestCase):
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

    def test_startup_marks_interrupted_rebuild_as_error(self):
        directory = self.make_test(status="rebuilding")
        main._recover_interrupted_ingests()
        status = json.loads((directory / "status.json").read_text())
        self.assertEqual(status["status"], "error")
        self.assertIn("rebuild", status["error"])

    def test_mutations_reject_a_busy_test_with_409(self):
        # A rebuild holds test_write for minutes; PATCH /meta, PUT and POST
        # /testpoints must 409 up front instead of parking on the lock (1.12).
        self.make_test(status="rebuilding")
        cases = [
            lambda: main.api_patch_meta(
                "alpha", main.UserMetaPatch(user_meta={"prop": "X"})),
            lambda: main.api_put_testpoints(
                "alpha", main.TestPointsFile(test="alpha")),
        ]
        for operation in cases:
            with self.assertRaises(HTTPException) as caught:
                operation()
            self.assertEqual(caught.exception.status_code, 409)

    def test_put_testpoints_rejects_duplicate_ids(self):
        self.make_test(status="ready")
        payload = main.TestPointsFile(test="alpha", test_points=[
            main.TestPoint(id=1, name="a", start_s=0.0),
            main.TestPoint(id=1, name="b", start_s=1.0),
        ])
        with self.assertRaises(HTTPException) as caught:
            main.api_put_testpoints("alpha", payload)
        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("duplicate", caught.exception.detail)
        # nothing was written: the pre-existing empty list stays
        points = json.loads(
            (self.tests / "alpha" / "testpoints.json").read_text())
        self.assertEqual(points["test_points"], [])

    def test_put_testpoints_accepts_unique_ids(self):
        self.make_test(status="ready")
        payload = main.TestPointsFile(test="alpha", test_points=[
            main.TestPoint(id=1, name="a", start_s=0.0),
            main.TestPoint(id=2, name="b", start_s=1.0),
        ])
        result = main.api_put_testpoints("alpha", payload)
        self.assertEqual(result, {"ok": True, "n": 2})

    def test_patch_meta_succeeds_on_a_ready_test(self):
        self.make_test(status="ready")
        result = main.api_patch_meta(
            "alpha", main.UserMetaPatch(user_meta={"prop": "20x10"}))
        self.assertEqual(result["user_meta"], {"prop": "20x10"})
        meta = json.loads((self.tests / "alpha" / "meta.json").read_text())
        self.assertEqual(meta["user_meta"], {"prop": "20x10"})

    def test_delete_evicts_the_test_lock(self):
        from app import locks
        self.make_test()
        locks._test_lock("alpha")  # register it
        self.assertIn(locks._test_key("alpha"), locks._test_locks)
        main.api_delete_test("alpha")
        self.assertNotIn(locks._test_key("alpha"), locks._test_locks)

    def test_rename_evicts_the_old_test_lock(self):
        from app import locks
        self.make_test()
        locks._test_lock("alpha")
        main.api_rename_test("alpha", "beta")
        self.assertNotIn(locks._test_key("alpha"), locks._test_locks)

    def test_dir_size_cached_for_ready_until_the_dir_changes(self):
        store._size_cache.clear()
        self.make_test("alpha", status="ready")
        first = next(t for t in store.list_tests()
                     if t["name"] == "alpha")["size_bytes"]
        # a second poll must NOT re-walk the tree (cache hit on the dir mtime)
        with patch.object(store, "_dir_size",
                          side_effect=AssertionError("recomputed")):
            cached = next(t for t in store.list_tests()
                          if t["name"] == "alpha")["size_bytes"]
        self.assertEqual(cached, first)
        # a new file bumps the dir mtime -> the cache misses and recomputes
        (self.tests / "alpha" / "extra.bin").write_bytes(b"z" * 64)
        grown = next(t for t in store.list_tests()
                     if t["name"] == "alpha")["size_bytes"]
        self.assertEqual(grown, first + 64)

    def test_dir_size_recomputed_every_poll_while_not_ready(self):
        store._size_cache.clear()
        self.make_test("beta", status="ingesting")
        # a mid-write test's size must be live (it can grow without a dir-mtime
        # bump), so _dir_size is always called and never cached
        with patch.object(store, "_dir_size", return_value=123) as walk:
            size = next(t for t in store.list_tests()
                        if t["name"] == "beta")["size_bytes"]
        self.assertEqual(size, 123)
        self.assertTrue(walk.called)
        self.assertNotIn("beta", store._size_cache)

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
