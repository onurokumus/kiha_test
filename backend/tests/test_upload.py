"""Raw-body upload endpoint: streaming receive, status lifecycle, guards.

The endpoint takes the CSV as the RAW request body (?name= required); with
TestClient the BackgroundTasks ingest runs synchronously before .post()
returns, so a 200 response means the test is already 'ready'.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import ingest, main, store


def small_csv(rows: int = 64, fs: float = 10.0) -> bytes:
    lines = ["time,thrust,rpm"]
    for i in range(rows):
        lines.append(f"{i / fs},{i * 0.5},{1000 + i}")
    return ("\n".join(lines) + "\n").encode()


class UploadTests(unittest.TestCase):
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
        self.client = TestClient(main.app)

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def test_upload_stores_raw_and_ingests(self):
        body = small_csv()
        r = self.client.post("/api/tests/upload?name=alpha", content=body)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"name": "alpha", "status": "ingesting"})

        # background ingest already ran under TestClient
        self.assertEqual(store.get_status("alpha")["status"], "ready")
        self.assertEqual((self.tests / "alpha" / "raw.csv").read_bytes(), body)
        meta = store.get_meta("alpha")
        self.assertEqual(meta["n_rows"], 64)
        self.assertEqual(meta["time_column"], "time")
        self.assertEqual(meta["fs_hz"], 10.0)

    def test_list_tests_reports_upload_history_fields(self):
        r = self.client.post(
            "/api/tests/upload?name=alpha&source=My Test (1).csv",
            content=small_csv())
        self.assertEqual(r.status_code, 200)

        row = next(t for t in self.client.get("/api/tests").json()
                   if t["name"] == "alpha")
        self.assertEqual(row["status"], "ready")
        self.assertEqual(row["source_file"], "My Test (1).csv")
        self.assertRegex(row["created_at"], r"^\d{4}-\d{2}-\d{2}T")
        self.assertGreater(row["size_bytes"], 0)
        self.assertIsNotNone(row["ingest_seconds"])

    def test_list_tests_covers_tests_without_meta(self):
        # A 'receiving' test has no meta.json yet: created_at must fall back
        # to the directory timestamp and size_bytes must count raw.csv.
        directory = self.tests / "beta"
        directory.mkdir()
        (directory / "raw.csv").write_bytes(b"x" * 100)
        store.write_json_atomic(directory / "status.json",
                                {"status": "receiving"})

        row = next(t for t in self.client.get("/api/tests").json()
                   if t["name"] == "beta")
        self.assertEqual(row["status"], "receiving")
        self.assertIsNone(row["source_file"])
        self.assertRegex(row["created_at"], r"^\d{4}-\d{2}-\d{2}T")
        self.assertGreaterEqual(row["size_bytes"], 100)

    def test_upload_requires_valid_name(self):
        for bad in ("", "has space", "semi;colon", "..", "___"):
            r = self.client.post(f"/api/tests/upload?name={bad}",
                                 content=b"time,a\n0,1\n")
            self.assertEqual(r.status_code, 400, bad)
        self.assertEqual(list(self.tests.iterdir()), [])

    def test_upload_duplicate_name_is_409_and_keeps_original(self):
        r = self.client.post("/api/tests/upload?name=alpha",
                             content=small_csv())
        self.assertEqual(r.status_code, 200)
        marker = (self.tests / "alpha" / "meta.json").read_bytes()

        r = self.client.post("/api/tests/upload?name=alpha",
                             content=b"time,a\n0,1\n")
        self.assertEqual(r.status_code, 409)
        # the rejected upload must not have touched the existing test
        self.assertEqual((self.tests / "alpha" / "meta.json").read_bytes(),
                         marker)
        self.assertEqual(store.get_status("alpha")["status"], "ready")

    def test_delete_and_rename_reject_receiving_test(self):
        directory = self.tests / "alpha"
        directory.mkdir()
        store.write_json_atomic(directory / "status.json",
                                {"status": "receiving"})

        for operation in (
            lambda: main.api_delete_test("alpha"),
            lambda: main.api_rename_test("alpha", "beta"),
        ):
            with self.assertRaises(HTTPException) as caught:
                operation()
            self.assertEqual(caught.exception.status_code, 409)
            self.assertIn("receiving", caught.exception.detail)
        self.assertTrue(directory.is_dir())

    def test_restart_recovery_marks_receiving_as_error(self):
        directory = self.tests / "alpha"
        directory.mkdir()
        store.write_json_atomic(directory / "status.json",
                                {"status": "receiving"})

        main._recover_interrupted_ingests()
        status = store.get_status("alpha")
        self.assertEqual(status["status"], "error")
        self.assertIn("interrupted", status["error"])

    def test_discard_partial_upload_removes_directory(self):
        directory = self.tests / "alpha"
        directory.mkdir()
        (directory / "raw.csv").write_bytes(b"partial")
        store.write_json_atomic(directory / "status.json",
                                {"status": "receiving"})

        main._discard_partial_upload("alpha")
        self.assertFalse(directory.exists())


if __name__ == "__main__":
    unittest.main()
