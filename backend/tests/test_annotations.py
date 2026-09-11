"""Durable stored-time annotations, guarded replacement, and lifecycle safety."""
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from unittest.mock import patch

from fastapi.testclient import TestClient
from app import edit, main, store
from ._base import DataDirTestCase
from . import test_test_notes as note_fixtures


class AnnotationTests(DataDirTestCase):
    def setUp(self):
        super().setUp()
        self.client = TestClient(main.app, raise_server_exceptions=False)
        self.body = ("time,signal\n" + "".join(f"{i / 10},{i}\n" for i in range(40))).encode()
        self.init = {"name": "alpha", "source_file": "source.csv", "size_bytes": len(self.body),
                     "last_modified_ms": 1, "uploader_name": "Test Lab"}
        note_fixtures.TestNotesTests.upload(self)
        self.url = "/api/tests/alpha/annotations"
        self.path = self.tests / "alpha/annotations.json"

    def read(self):
        result = self.client.get(self.url)
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def save(self, items, document=None):
        document = document or self.read()
        return self.client.put(self.url, json={"annotations": items,
            "expected_revision": document["revision"], "expected_data_bounds": document["data_bounds"]})

    def item(self, start=1.5, end=None, text="Vibration started 🔧\r\n<literal>"):
        return {"id": str(uuid4()), "start_s": start, "end_s": end, "text": text}

    def test_legacy_read_crud_noop_and_source_preservation(self):
        before = {p: p.read_bytes() for p in (self.tests / "alpha").rglob("*") if p.is_file()}
        initial = self.read()
        self.assertEqual(initial["annotations"], [])
        self.assertEqual(initial["revision"], 0)
        self.assertFalse(self.path.exists(), "legacy reads must not create files")
        a, b = self.item(), self.item(2, 3)
        saved = self.save([a, b]).json()
        self.assertEqual(saved["revision"], 1)
        self.assertEqual(saved["time_basis"], "stored_elapsed_seconds")
        self.assertEqual(saved["annotations"][0]["text"], a["text"].replace("\r\n", "\n"))
        persisted = self.path.read_bytes()
        self.assertEqual(self.save(saved["annotations"]).json(), saved)
        self.assertEqual(self.path.read_bytes(), persisted)
        a.update(text="Corrected", start_s=1.75)
        self.assertEqual(self.save([a, b]).json()["revision"], 2)
        self.assertEqual(self.save([b]).json()["annotations"][0]["id"], b["id"])
        self.assertEqual(self.save([]).json()["annotations"], [])
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content, str(path))

    def test_stale_and_concurrent_edits_cannot_overwrite(self):
        old = self.read()
        first = self.save([self.item()], old)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(self.save([], old).status_code, 409)
        current = self.read()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.save([self.item()], current), range(2)))
        self.assertEqual(sorted(r.status_code for r in results), [200, 409])
        self.assertEqual(self.read()["revision"], 2)

    def test_trim_retains_original_times_and_guards_stale_bounds(self):
        a, b, c = self.item(.5), self.item(.5, 2), self.item(3.5)
        saved = self.save([a, b, c]).json()
        content = self.path.read_bytes()
        edit._rebuild("alpha", {"trim_t0": 1, "trim_t1": 3})
        self.assertEqual(self.path.read_bytes(), content)
        self.assertEqual(self.save([a, b, c], saved).status_code, 409)
        current = self.read()
        self.assertEqual(current["data_bounds"][0], 1)
        a["text"] = "Outside data but retained"
        self.assertEqual(self.save([a, b, c]).status_code, 200)
        moved = {**a, "start_s": .75}
        self.assertEqual(self.save([moved, b, c]).status_code, 422)
        self.assertEqual(self.save([self.item(.5)]).status_code, 422)
        self.assertEqual(self.save([]).status_code, 200)

    def test_column_rebuild_rename_and_current_trash_restore(self):
        self.assertEqual(self.save([self.item()]).status_code, 200)
        before = self.path.read_bytes()
        edit._rebuild("alpha", {"rename": {"signal": "force"}})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.client.post("/api/tests/alpha/rename?new_name=beta").status_code, 200)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.delete("/api/tests/beta").status_code, 200)
        self.assertEqual(self.client.post("/api/tests/beta/restore").status_code, 200)
        self.assertEqual((self.tests / "beta/annotations.json").read_bytes(), before)
        self.assertEqual(self.client.get("/api/tests/beta/annotations").json()["test"], "beta")

    def test_validation_and_limits_leave_document_unchanged(self):
        self.save([self.item()])
        before = self.path.read_bytes()
        for changes in ({"start_s": -1}, {"start_s": "1"}, {"start_s": True},
                        {"start_s": float("nan")}, {"start_s": float("inf")},
                        {"end_s": 1.5}, {"end_s": 1}, {"end_s": float("inf")},
                        {"id": "not-a-uuid"}, {"text": ""}, {"text": " \t\n"},
                        {"text": "\x00"}, {"text": "\ud800"}, {"text": "x" * 2001},
                        {"text": None}, {"unknown": "x"}, {"start_s": 4.1}):
            with self.subTest(changes=repr(changes)[:60]):
                item = {**self.item(), **changes}
                result = self.client.put(self.url, content=json.dumps({"expected_revision": 1,
                    "expected_data_bounds": self.read()["data_bounds"], "annotations": [item]}),
                    headers={"Content-Type": "application/json"})
                self.assertEqual(result.status_code, 422, result.text)
                self.assertEqual(self.path.read_bytes(), before)
        duplicate = self.item()
        self.assertEqual(self.save([duplicate, duplicate]).status_code, 422)
        self.assertEqual(self.save([self.item() for _ in range(201)]).status_code, 422)
        boundary = self.item(*self.read()["data_bounds"], text="🔧" * 2000)
        self.assertEqual(self.save([boundary]).status_code, 200)
        self.assertEqual(self.save([self.item() for _ in range(200)]).status_code, 200)

    def test_busy_disk_failure_retry_and_corrupt_file_are_safe(self):
        self.save([self.item()])
        saved = self.read()
        before = self.path.read_bytes()
        store.write_json_atomic(self.tests / "alpha/status.json", {"status": "rebuilding"})
        self.assertEqual(self.client.get(self.url).status_code, 409)
        self.assertEqual(self.save([], saved).status_code, 409)
        store.write_json_atomic(self.tests / "alpha/status.json", {"status": "ready"})
        with patch.object(store, "write_json_atomic", side_effect=OSError("disk unavailable")):
            self.assertEqual(self.save([], saved).status_code, 500)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.save([], saved).status_code, 200)
        for value in ("invalid", '[]', '{"version":2}', '{"version":1,"time_basis":"stored_elapsed_seconds","revision":0,"annotations":[{}]}'):
            with self.subTest(value=value):
                self.path.write_text(value, encoding="utf-8")
                self.assertEqual(self.client.get(self.url).status_code, 409)
                self.assertEqual(self.save([], saved).status_code, 409)
                self.assertEqual(self.path.read_text(encoding="utf-8"), value)
