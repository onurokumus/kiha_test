"""Test-level text: real upload/recovery and metadata/lifecycle preservation."""

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import edit, main, store, uploads
from ._base import DataDirTestCase


class TestNotesTests(DataDirTestCase):
    def setUp(self):
        super().setUp()
        self.client = TestClient(main.app, raise_server_exceptions=False)
        self.body = ("time,signal\n" + "".join(f"{i / 10},{i}\n" for i in range(40))).encode()
        self.init = {"name": "alpha", "source_file": "source.csv",
                     "size_bytes": len(self.body), "last_modified_ms": 1,
                     "uploader_name": "Test Lab"}

    def upload(self, description=None):
        payload = {**self.init, **({"description": description} if description is not None else {})}
        response = self.client.post("/api/uploads", json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        session = response.json()
        path = f"/api/uploads/{session['upload_id']}"
        result = self.client.put(path + "/chunks/0?name=alpha",
                                 files={"file": ("chunk", self.body)},
                                 headers={"X-Chunk-SHA256": hashlib.sha256(self.body).hexdigest()})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(self.client.post(path + "/complete?name=alpha").status_code, 200)
        self.assertEqual(store.get_status("alpha")["status"], "ready")
        return session

    def save(self, **fields):
        return self.client.patch("/api/tests/alpha/meta", json=fields)

    def test_description_session_recovery_identity_and_ingestion(self):
        description = "Temperature test 🌡️\nİzmir / 測定 <b>literal</b>"
        session = self.upload(description)
        self.assertEqual(session["description"], description)
        self.assertEqual(store.get_meta("alpha")["description"], description)
        self.assertEqual(store.list_tests()[0]["description"], description)
        uploads.recover_uploads()
        self.assertEqual(store.get_meta("alpha")["description"], description)
        path = self.tests / "alpha/.upload/manifest.json"
        self.assertEqual(json.loads(path.read_text())["description"], description)
        self.assertEqual(self.save(description="Corrected").status_code, 200)
        # The upload record is immutable; later editable descriptions live in meta.
        self.assertEqual(json.loads(path.read_text())["description"], description)
        self.assertEqual(store.list_tests()[0]["description"], "Corrected")

    def test_receiving_resume_rejects_changed_description(self):
        first = self.client.post("/api/uploads", json={**self.init, "description": "A\r\nB"})
        self.assertEqual(first.status_code, 201)
        self.assertEqual(first.json()["description"], "A\nB")
        uploads.recover_uploads()
        same = self.client.post("/api/uploads", json={**self.init, "description": "A\nB"})
        self.assertEqual(same.status_code, 200)
        self.assertEqual(first.json()["upload_id"], same.json()["upload_id"])
        changed = self.client.post("/api/uploads", json={**self.init, "description": "Different"})
        self.assertEqual(changed.status_code, 409)
        self.assertEqual(store.list_tests()[0]["description"], "A\nB")

    def test_legacy_manifest_without_description_resumes(self):
        response = self.client.post("/api/uploads", json=self.init)
        self.assertEqual(response.status_code, 201)
        path = self.tests / "alpha/.upload/manifest.json"
        manifest = json.loads(path.read_text())
        manifest.pop("description")
        store.write_json_atomic(path, manifest)
        uploads.recover_uploads()
        resumed = self.client.post("/api/uploads", json=self.init)
        self.assertEqual(resumed.status_code, 200)
        self.assertEqual(resumed.json()["description"], "")

    def test_partial_patch_preserves_legacy_descriptors_and_scientific_metadata(self):
        self.upload()
        custom = {"description": "old custom descriptor", "notes": "legacy notes",
                  "motor": "M1", "__proto__": "preserve me"}
        self.assertEqual(self.save(user_meta=custom).status_code, 200)
        before = store.get_meta("alpha")
        files = {str(p): p.read_bytes() for p in (self.tests / "alpha").rglob("*")
                 if p.is_file() and p.name != "meta.json"}
        text = "End of test: 80°C\r\n\tCheck fixture 🔧"
        saved = self.save(description="Temperature", notes=text)
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json(), {**before, "description": "Temperature",
                                        "notes": text.replace("\r\n", "\n")})
        self.assertEqual(store.get_meta("alpha"), saved.json())
        for path, content in files.items():
            self.assertEqual(Path(path).read_bytes(), content, path)
        # Old clients still replace their block and cannot erase the new fields.
        self.assertEqual(self.save(user_meta={"motor": "M2"}).status_code, 200)
        self.assertEqual(store.get_meta("alpha")["notes"], saved.json()["notes"])
        self.assertEqual(self.save(description="", notes="").status_code, 200)
        self.assertEqual(store.list_tests()[0]["description"], "")
        self.assertEqual(store.get_meta("alpha")["notes"], "")

    def test_text_validation_and_boundary_lengths_are_atomic(self):
        self.upload()
        path = self.tests / "alpha/meta.json"
        before = path.read_bytes()
        for field, limit in (("description", 1000), ("notes", 20_000)):
            for value in (None, 123, {}, [], "\x00", "\x1b", "x" * (limit + 1)):
                with self.subTest(field=field, value=repr(value)[:40]):
                    self.assertEqual(self.save(**{field: value}).status_code, 422)
                    self.assertEqual(path.read_bytes(), before)
        for field, limit in (("description", 1000), ("notes", 20_000)):
            result = self.save(**{field: "🔧" * limit})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(len(result.json()[field]), limit)

    def test_upload_rejects_bad_description_before_reserving_name(self):
        for value in (None, 123, [], "x" * 1001, "\x00"):
            with self.subTest(value=repr(value)[:40]):
                result = self.client.post("/api/uploads", json={**self.init, "description": value})
                self.assertEqual(result.status_code, 422)
                self.assertFalse((self.tests / "alpha").exists())

    def test_escaped_invalid_unicode_returns_validation_error_without_writes(self):
        # Use escaped JSON explicitly: httpx rejects a lone surrogate when
        # serializing json= itself, before the request can reach the application.
        result = self.client.post("/api/uploads", content=json.dumps({**self.init, "description": "\ud800"}),
                                  headers={"Content-Type": "application/json"})
        self.assertEqual(result.status_code, 422, result.text)
        self.assertFalse((self.tests / "alpha").exists())
        self.upload()
        path = self.tests / "alpha/meta.json"
        before = path.read_bytes()
        for field in ("description", "notes"):
            result = self.client.patch("/api/tests/alpha/meta", content=json.dumps({field: "\ud800"}),
                                       headers={"Content-Type": "application/json"})
            self.assertEqual(result.status_code, 422, result.text)
            self.assertEqual(path.read_bytes(), before)

    def test_notes_survive_schema_edit_rename_trash_restore(self):
        self.upload("Temperature")
        self.assertEqual(self.save(notes="Hot at the end", user_meta={"custom": "keep"}).status_code, 200)
        edit._rebuild("alpha", {"rename": {"signal": "force"}})
        self.assertEqual(store.get_meta("alpha")["notes"], "Hot at the end")
        self.assertEqual(self.client.post("/api/tests/alpha/rename?new_name=beta").status_code, 200)
        self.assertEqual(store.get_meta("beta")["description"], "Temperature")
        self.assertEqual(self.client.delete("/api/tests/beta").status_code, 200)
        self.assertEqual(self.client.post("/api/tests/beta/restore").status_code, 200)
        meta = store.get_meta("beta")
        self.assertEqual(meta["notes"], "Hot at the end")
        self.assertEqual(meta["user_meta"], {"custom": "keep"})
        self.assertIn("force", meta["columns"])

    def test_busy_missing_and_disk_failure_leave_saved_notes_unchanged(self):
        self.assertEqual(self.save(notes="missing").status_code, 404)
        self.upload("Original")
        self.assertEqual(self.save(notes="Saved").status_code, 200)
        path = self.tests / "alpha/meta.json"
        before = path.read_bytes()
        store.write_json_atomic(self.tests / "alpha/status.json", {"status": "rebuilding"})
        self.assertEqual(self.save(notes="Busy").status_code, 409)
        store.write_json_atomic(self.tests / "alpha/status.json", {"status": "ready"})
        with patch.object(store, "write_json_atomic", side_effect=OSError("disk unavailable")):
            self.assertEqual(self.save(notes="Failed").status_code, 500)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.save(notes="Retry").status_code, 200)
