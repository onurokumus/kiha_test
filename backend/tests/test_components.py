"""Component registry, immutable upload identity and guarded test associations."""
import hashlib
import json
import io
import zipfile
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from unittest.mock import patch

from fastapi.testclient import TestClient
from app import analysis_metadata, components, edit, main, store, uploads
from ._base import DataDirTestCase
from . import test_test_notes as notes_fixture


class ComponentTests(DataDirTestCase):
    def setUp(self):
        super().setUp()
        self.client = TestClient(main.app, raise_server_exceptions=False)
        self.body = ("time,signal\n" + "".join(f"{i / 10},{i}\n" for i in range(40))).encode()
        self.init = {"name": "alpha", "source_file": "source.csv", "size_bytes": len(self.body),
                     "last_modified_ms": 1, "uploader_name": "Lab"}

    def create(self, kind="motor", name="motor_1"):
        result = self.client.post("/api/components", json={"kind": kind, "name": name})
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def upload(self, ids=None):
        if ids is not None:
            self.init["components"] = ids
        return notes_fixture.TestNotesTests.upload(self, "Component test")

    def assign(self, ids, revision=0, **fields):
        return self.client.patch("/api/tests/alpha/meta", json={"components": ids,
            "expected_components_revision": revision, **fields})

    def test_legacy_registry_idempotent_creation_and_type_identity(self):
        response = self.client.get("/api/components")
        self.assertEqual(response.json(), {"version": 1, "components": []})
        self.assertFalse(components._path().exists())
        first = self.create(name="  Moteur e\u0301  ")
        self.assertEqual(first["name"], "Moteur é")
        self.assertEqual(first, self.create(name="MOTEUR É"))
        self.assertNotEqual(first["id"], self.create("esc", "Moteur é")["id"])
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(lambda _: self.create("propeller", "helix_1"), range(3)))
        self.assertEqual(len({item["id"] for item in results}), 1)
        self.assertEqual(len(self.client.get("/api/components").json()["components"]), 3)

    def test_registry_validation_limit_corruption_and_disk_failure(self):
        for payload in ({"kind": "unknown", "name": "n"}, {"kind": "motor", "name": " "},
                        {"kind": "motor", "name": 123}, {"kind": "motor", "name": "x" * 121},
                        {"kind": "motor", "name": "x\ny"}, {"kind": "motor", "name": "x\ty"},
                        {"kind": "motor", "name": "\x00"}, {"kind": "motor", "name": "\ud800"}):
            with self.subTest(payload=repr(payload)[:80]):
                response = self.client.post("/api/components", content=json.dumps(payload), headers={"Content-Type": "application/json"})
                self.assertEqual(response.status_code, 422, response.text)
                self.assertFalse(components._path().exists())
        with patch.object(store, "write_json_atomic", side_effect=OSError("disk unavailable")):
            self.assertEqual(self.client.post("/api/components", json={"kind": "motor", "name": "n"}).status_code, 500)
        self.assertFalse(components._path().exists())
        self.create(name="🔧" * 120)
        before = components._path().read_bytes()
        with patch.object(components, "MAX_PER_KIND", 1):
            self.assertEqual(self.client.post("/api/components", json={"kind": "motor", "name": "second"}).status_code, 409)
        self.assertEqual(components._path().read_bytes(), before)
        for value in ("bad JSON", "[]", '{"version":2,"components":[]}'):
            with self.subTest(value=value):
                components._path().write_text(value, encoding="utf-8")
                self.assertEqual(self.client.get("/api/components").status_code, 409)
                self.assertEqual(self.client.post("/api/components", json={"kind": "motor", "name": "n"}).status_code, 409)
                self.assertEqual(components._path().read_text(encoding="utf-8"), value)

    def test_upload_recovery_identity_ingestion_and_corrected_metadata(self):
        motor = self.create()
        esc = self.create("esc", "ESC_1")
        self.init["components"] = {"motor": motor["id"], "esc": esc["id"]}
        created = self.client.post("/api/uploads", json=self.init)
        self.assertEqual(created.status_code, 201)
        session = created.json()
        self.assertEqual(store.list_tests()[0]["components"], session["components"])
        uploads.recover_uploads()
        resumed = self.client.post("/api/uploads", json=self.init)
        self.assertEqual(resumed.status_code, 200)
        self.assertEqual(resumed.json()["components"], components.ids(self.init["components"]))
        changed = self.client.post("/api/uploads", json={**self.init, "components": {}})
        self.assertEqual(changed.status_code, 409)
        # The original file and durable assignments survive GET + recovery.
        path = f"/api/uploads/{session['upload_id']}"
        self.assertEqual(self.client.put(path + "/chunks/0?name=alpha", files={"file": ("chunk", self.body)},
            headers={"X-Chunk-SHA256": hashlib.sha256(self.body).hexdigest()}).status_code, 200)
        self.assertEqual(self.client.post(path + "/complete?name=alpha").status_code, 200)
        meta = store.get_meta("alpha")
        self.assertEqual(meta["components"], session["components"])
        self.assertEqual(store.list_tests()[0]["components"], session["components"])
        manifest = self.tests / "alpha/.upload/manifest.json"
        before = manifest.read_bytes()
        self.assertEqual(self.assign({}).json()["components_revision"], 1)
        uploads.recover_uploads()
        self.assertEqual(manifest.read_bytes(), before)
        self.assertEqual(store.get_meta("alpha")["components"], components.ids())

    def test_legacy_manifest_and_invalid_references_do_not_reserve_names(self):
        esc = self.create("esc", "ESC_1")
        for refs in ({"motor": esc["id"]}, {"motor": str(uuid4())}, {"motor": "bad"}, {"other": None}, None):
            with self.subTest(refs=refs):
                result = self.client.post("/api/uploads", json={**self.init, "components": refs})
                self.assertEqual(result.status_code, 422, result.text)
                self.assertFalse((self.tests / "alpha").exists())
        created = self.client.post("/api/uploads", json=self.init)
        self.assertEqual(created.status_code, 201)
        path = self.tests / "alpha/.upload/manifest.json"
        manifest = json.loads(path.read_text())
        manifest.pop("components")
        store.write_json_atomic(path, manifest)
        uploads.recover_uploads()
        self.assertEqual(self.client.post("/api/uploads", json=self.init).status_code, 200)

    def test_failed_ingestion_preserves_component_identity_for_recovery(self):
        motor = self.create()
        body = b"time,signal\n"
        response = self.client.post('/api/uploads', json={**self.init, 'size_bytes': len(body), 'components': {'motor': motor['id']}})
        self.assertEqual(response.status_code, 201)
        session = response.json(); path = f"/api/uploads/{session['upload_id']}"
        self.assertEqual(self.client.put(path + '/chunks/0?name=alpha', files={'file': ('chunk', body)},
            headers={'X-Chunk-SHA256': hashlib.sha256(body).hexdigest()}).status_code, 200)
        self.assertEqual(self.client.post(path + '/complete?name=alpha').status_code, 200)
        self.assertEqual(store.get_status('alpha')['status'], 'error')
        self.assertEqual(store.get_status('alpha')['components']['motor'], motor['id'])
        uploads.recover_uploads()
        self.assertEqual(self.client.get(path + '?name=alpha').json()['components'], session['components'])

    def test_partial_atomic_associations_revision_source_and_export_context(self):
        motor = self.create(); second = self.create(name="motor_2")
        self.upload()
        custom = {"motor": "legacy free text", "components": "not typed", "__proto__": "preserve"}
        self.client.patch("/api/tests/alpha/meta", json={"user_meta": custom, "notes": "Keep notes"})
        before = {p: p.read_bytes() for p in (self.tests / "alpha").rglob("*") if p.is_file() and p.name != "meta.json"}
        result = self.assign({"motor": motor["id"]})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["user_meta"], custom)
        self.assertEqual(result.json()["notes"], "Keep notes")
        snapshot = analysis_metadata.source_context("alpha", result.json(), ["signal"], 0, 40)
        self.assertEqual(snapshot["source"]["component_ids"]["motor"], motor["id"])
        self.assertEqual(self.assign({"motor": motor["id"]}, 1).json()["components_revision"], 1)
        # A stale components write cannot partially apply an accompanying note.
        stale = self.assign({"motor": second["id"]}, 0, notes="Stale note")
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(store.get_meta("alpha")["notes"], "Keep notes")
        self.assertEqual(self.assign({"motor": second["id"]}, 1).json()["components_revision"], 2)
        self.assertEqual(snapshot["source"]["component_ids"]["motor"], motor["id"])
        response = self.client.post('/api/plot-export', json={'column': 'signal', 'data': 'original',
            'include_metadata': True, 'sources': [{'test': 'alpha'}]})
        self.assertEqual(response.status_code, 200, response.text if response.status_code != 200 else '')
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            document = json.loads(archive.read('analysis.json'))
            record = document['plots'][0]['sources'][0]['source']
            self.assertEqual(record['component_ids']['motor'], second['id'])
            self.assertEqual(record['component_association_revision'], 2)
        self.assertEqual(self.client.patch("/api/tests/alpha/meta", json={"notes": "New note"}).json()["components_revision"], 2)
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content, str(path))

    def test_concurrent_assignment_busy_failure_and_retry(self):
        first = self.create(); second = self.create(name="motor_2")
        self.upload()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda c: self.assign({"motor": c["id"]}), [first, second]))
        self.assertEqual(sorted(r.status_code for r in results), [200, 409])
        before = (self.tests / "alpha/meta.json").read_bytes()
        store.write_json_atomic(self.tests / "alpha/status.json", {"status": "rebuilding"})
        self.assertEqual(self.assign({}, 1).status_code, 409)
        store.write_json_atomic(self.tests / "alpha/status.json", {"status": "ready"})
        with patch.object(store, "write_json_atomic", side_effect=OSError("disk failure")):
            self.assertEqual(self.assign({}, 1).status_code, 500)
        self.assertEqual((self.tests / "alpha/meta.json").read_bytes(), before)
        self.assertEqual(self.assign({}, 1).json()["components_revision"], 2)

    def test_rebuild_rename_current_trash_restore_and_missing_catalog(self):
        motor = self.create(); self.upload({"motor": motor["id"]})
        self.client.patch("/api/tests/alpha/meta", json={"notes": "Findings"})
        annotation = {"id": str(uuid4()), "start_s": 1.5, "text": "Marker"}
        doc = self.client.get("/api/tests/alpha/annotations").json()
        self.client.put("/api/tests/alpha/annotations", json={"expected_revision": 0, "expected_data_bounds": doc["data_bounds"], "annotations": [annotation]})
        annotations = (self.tests / "alpha/annotations.json").read_bytes()
        edit._rebuild("alpha", {"rename": {"signal": "force"}, "trim_t0": 1, "trim_t1": 3})
        self.assertEqual(self.client.post("/api/tests/alpha/rename?new_name=beta").status_code, 200)
        self.assertEqual(self.client.delete("/api/tests/beta").status_code, 200)
        self.assertEqual(self.client.post("/api/tests/beta/restore").status_code, 200)
        meta = store.get_meta("beta")
        self.assertEqual(meta["components"]["motor"], motor["id"])
        self.assertEqual(meta["notes"], "Findings")
        self.assertEqual((self.tests / "beta/annotations.json").read_bytes(), annotations)
        components._path().write_text('{"version":1,"components":[]}', encoding="utf-8")
        # Missing registry references never rewrite current metadata or stop
        # scientific data access; an explicit clear can repair them.
        self.assertEqual(self.client.get("/api/tests/beta").json()["components"]["motor"], motor["id"])
        cleared = self.client.patch("/api/tests/beta/meta", json={"components": {}, "expected_components_revision": 0})
        self.assertEqual(cleared.status_code, 200, cleared.text)
