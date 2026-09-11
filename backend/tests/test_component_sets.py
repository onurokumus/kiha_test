"""Multiple independent hardware sets survive uploads, edits and guarded saves."""
import copy
import hashlib
import io
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app import analysis_metadata, components, edit, main, store, uploads
from ._base import DataDirTestCase


class ComponentSetTests(DataDirTestCase):
    def setUp(self):
        super().setUp()
        self.client = TestClient(main.app, raise_server_exceptions=False)
        self.body = ("time,rpm_a,rpm_b,temp_a,temp_b,power_a,power_b\n" +
                     "".join(f"{i / 10},600,1200,30,86,100,0.2\n" for i in range(40))).encode()
        self.init = {"name": "alpha", "source_file": "source.csv",
                     "size_bytes": len(self.body), "last_modified_ms": 1}
        self.first = self.make_set("Left")
        self.second = self.make_set("Right")

    def make_set(self, name):
        refs = {}
        for kind in components.KINDS:
            response = self.client.post("/api/components", json={"kind": kind, "name": f"{name} {kind}"})
            self.assertEqual(response.status_code, 200, response.text)
            refs[kind] = response.json()["id"]
        return components.normalize_sets([{"id": str(uuid4()), "name": name, "components": refs}])[0]

    def complete(self, session, body=None):
        body = self.body if body is None else body
        path = f"/api/uploads/{session['upload_id']}"
        response = self.client.put(path + "/chunks/0?name=alpha", files={"file": ("chunk", body)},
                                   headers={"X-Chunk-SHA256": hashlib.sha256(body).hexdigest()})
        self.assertEqual(response.status_code, 200, response.text)
        response = self.client.post(path + "/complete?name=alpha")
        self.assertEqual(response.status_code, 200, response.text)
        return path

    def upload(self, assigned=None):
        payload = self.init if assigned is None else {**self.init, "component_sets": assigned}
        response = self.client.post("/api/uploads", json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        self.complete(response.json())
        self.assertEqual(store.get_status("alpha")["status"], "ready")
        return response.json()

    def bound_sets(self):
        result = copy.deepcopy([self.first, self.second])
        for item, suffix in zip(result, ("a", "b")):
            item.update(rpm_column=f"rpm_{suffix}", motor_temperature_column=f"temp_{suffix}",
                        power_column=f"power_{suffix}")
        result[1].update(motor_temperature_unit="F", power_unit="kW")
        return result

    def save(self, value, revision=0, **fields):
        return self.client.patch("/api/tests/alpha/meta", json={"component_sets": value,
            "expected_component_sets_revision": revision, **fields})

    def test_normalized_legacy_projection_and_explicit_empty(self):
        legacy = {"components": self.first["components"], "component_rpm_column": "rpm_a"}
        value = components.sets(legacy)
        self.assertEqual(value[0]["id"], "legacy")
        self.assertEqual(value[0]["name"], "Set 1")
        self.assertEqual(value[0]["rpm_column"], "rpm_a")
        self.assertIsNone(value[0]["motor_temperature_column"])
        self.assertEqual(value[0]["power_unit"], "W")
        self.assertEqual(components.sets({**legacy, "component_sets": []}), [])
        self.assertEqual(components.sets({})[0]["components"], components.ids())

    def test_atomic_multi_set_save_provenance_projection_and_clear(self):
        self.upload()
        files = {p: p.read_bytes() for p in (self.tests / "alpha").rglob("*")
                 if p.is_file() and p.name != "meta.json"}
        bound = self.bound_sets()
        bound[0]["name"] = "  Left  "
        result = self.save(bound, notes="Findings")
        self.assertEqual(result.status_code, 200, result.text)
        meta = result.json()
        self.assertEqual(meta["component_sets_revision"], 1)
        self.assertEqual(meta["component_sets"][0]["name"], "Left")
        self.assertEqual(meta["components"], self.first["components"])
        self.assertEqual(meta["component_rpm_column"], "rpm_a")
        self.assertEqual(meta["component_rpm_revision"], 1)
        self.assertEqual(meta["components_revision"], 1)
        self.assertEqual(self.save(bound, 1).json()["component_sets_revision"], 1)
        snapshot = analysis_metadata.source_context("alpha", meta, ["rpm_a"], 0, 40)
        self.assertEqual(snapshot["source"]["component_sets"], meta["component_sets"])
        self.assertEqual(snapshot["source"]["component_sets_revision"], 1)
        response = self.client.post("/api/plot-export", json={"column": "rpm_a", "data": "original",
            "include_metadata": True, "sources": [{"test": "alpha"}]})
        self.assertEqual(response.status_code, 200, response.text if response.status_code != 200 else "")
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            source = json.loads(archive.read("analysis.json"))["plots"][0]["sources"][0]["source"]
            self.assertEqual(source["component_sets"], meta["component_sets"])
            self.assertEqual(source["component_sets_revision"], 1)
        cleared = self.save([], 1).json()
        self.assertEqual(cleared["component_sets"], [])
        self.assertEqual(cleared["components"], components.ids())
        self.assertIsNone(cleared["component_rpm_column"])
        self.assertEqual(cleared["components_revision"], 2)
        self.assertEqual(cleared["component_rpm_revision"], 2)
        self.assertEqual(store.list_tests()[0]["component_sets"], [])
        self.assertEqual(snapshot["source"]["component_sets"], meta["component_sets"])
        for path, body in files.items():
            self.assertEqual(path.read_bytes(), body, str(path))

    def test_legacy_migration_preserves_assignments_and_blocks_old_writers(self):
        self.upload()
        response = self.client.patch("/api/tests/alpha/meta", json={"components": self.first["components"],
            "expected_components_revision": 0, "component_rpm_column": "rpm_a", "expected_component_rpm_revision": 0})
        self.assertEqual(response.status_code, 200, response.text)
        legacy = response.json()
        self.assertNotIn("component_sets", legacy)
        migrated = self.save(components.sets(legacy), legacy["component_sets_revision"])
        self.assertEqual(migrated.status_code, 200, migrated.text)
        self.assertEqual(migrated.json()["components_revision"], 1)
        self.assertEqual(migrated.json()["component_rpm_revision"], 1)
        before = (self.tests / "alpha/meta.json").read_bytes()
        for payload in ({"components": {}, "expected_components_revision": 1},
                        {"component_rpm_column": None, "expected_component_rpm_revision": 1},
                        {"expected_components_revision": 1}):
            with self.subTest(payload=payload):
                response = self.client.patch("/api/tests/alpha/meta", json={**payload, "notes": "stale"})
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual((self.tests / "alpha/meta.json").read_bytes(), before)

    def test_guarded_concurrent_and_failed_saves_are_atomic(self):
        self.upload([self.first, self.second])
        missing = self.client.patch("/api/tests/alpha/meta", json={"component_sets": [], "notes": "wrong"})
        self.assertEqual(missing.status_code, 409, missing.text)
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda value: self.save([value], notes=value["name"]), [self.first, self.second]))
        self.assertEqual(sorted(response.status_code for response in responses), [200, 409])
        path = self.tests / "alpha/meta.json"
        before = path.read_bytes()
        self.assertEqual(self.save([], 0, notes="stale").status_code, 409)
        self.assertEqual(path.read_bytes(), before)
        with patch.object(store, "write_json_atomic", side_effect=OSError("disk unavailable")):
            self.assertEqual(self.save([], 1, notes="failed").status_code, 500)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.save([], 1, notes="retry").json()["component_sets_revision"], 2)

    def test_validation_does_not_partially_write_metadata(self):
        self.upload()
        values = [None, [self.first] * 17, [self.first, self.first],
                  [self.first, {**self.second, "name": " LEFT "}],
                  [self.first, {**self.second, "components": self.first["components"]}]]
        for changed in ({"id": "bad"}, {"name": " "}, {"name": "line\nbreak"}, {"name": "x" * 121},
                        {"motor_temperature_unit": "celsius"}, {"power_unit": "watts"},
                        {"rpm_column": "time"}, {"motor_temperature_column": "missing"},
                        {"power_column": "time"}, {"power_column": ""},
                        {"components": {"motor": self.first["components"]["esc"]}},
                        {"components": {"motor": str(uuid4())}}):
            values.append([{**self.first, **changed}])
        before = (self.tests / "alpha/meta.json").read_bytes()
        for value in values:
            with self.subTest(value=value):
                result = self.save(value, notes="not saved")
                self.assertEqual(result.status_code, 422, result.text)
                self.assertEqual((self.tests / "alpha/meta.json").read_bytes(), before)
        mixed = self.save([], components={}, expected_components_revision=0)
        self.assertEqual(mixed.status_code, 422, mixed.text)
        self.assertEqual((self.tests / "alpha/meta.json").read_bytes(), before)

    def test_upload_resume_identity_ingest_and_immutable_manifest(self):
        assigned = [self.first, self.second]
        payload = {**self.init, "component_sets": assigned}
        response = self.client.post("/api/uploads", json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        session = response.json()
        self.assertEqual(session["component_sets"], assigned)
        self.assertEqual(store.list_tests()[0]["component_sets"], assigned)
        uploads.recover_uploads()
        self.assertEqual(self.client.post("/api/uploads", json=payload).status_code, 200)
        self.assertEqual(self.client.post("/api/uploads", json=self.init).status_code, 409)
        for changed in ([], list(reversed(assigned)), [{**self.first, "name": "changed"}, self.second]):
            self.assertEqual(self.client.post("/api/uploads", json={**payload, "component_sets": changed}).status_code, 409)
        path = self.complete(session)
        self.assertEqual(store.get_meta("alpha")["component_sets"], assigned)
        self.assertEqual(store.get_meta("alpha")["component_sets_revision"], 0)
        manifest = self.tests / "alpha/.upload/manifest.json"
        before = manifest.read_bytes()
        self.assertEqual(self.save(self.bound_sets()).status_code, 200)
        uploads.recover_uploads()
        self.assertEqual(manifest.read_bytes(), before)
        self.assertEqual(self.client.get(path + "?name=alpha").json()["component_sets"], assigned)
        self.assertEqual(store.list_tests()[0]["component_sets"], self.bound_sets())

    def test_upload_validation_precedes_name_reservation_and_accepts_empty(self):
        for extra in ({"component_sets": None}, {"component_sets": [self.first, self.first]},
                      {"component_sets": self.bound_sets()},
                      {"component_sets": [], "components": {}},
                      {"component_sets": [{**self.first, "components": {"motor": str(uuid4())}}]}):
            with self.subTest(extra=extra):
                response = self.client.post("/api/uploads", json={**self.init, **extra})
                self.assertEqual(response.status_code, 422, response.text)
                self.assertFalse((self.tests / "alpha").exists())
        self.upload([])
        self.assertEqual(store.get_meta("alpha")["component_sets"], [])
        self.assertEqual(components.sets(store.get_meta("alpha")), [])

    def test_failed_ingest_recovery_and_trash_preserve_sets(self):
        body = b"time,rpm_a\n"
        response = self.client.post("/api/uploads", json={**self.init, "size_bytes": len(body),
                                                         "component_sets": [self.first, self.second]})
        self.assertEqual(response.status_code, 201, response.text)
        path = self.complete(response.json(), body)
        self.assertEqual(store.get_status("alpha")["status"], "error")
        self.assertEqual(store.get_status("alpha")["component_sets"], [self.first, self.second])
        uploads.recover_uploads()
        self.assertEqual(self.client.get(path + "?name=alpha").json()["component_sets"], [self.first, self.second])
        self.assertEqual(self.client.delete("/api/tests/alpha").status_code, 200)
        entries = self.client.get("/api/trash").json()["entries"]
        self.assertEqual(entries[0]["component_sets"], [self.first, self.second])

    def test_rename_drop_trim_trash_restore_keep_bindings_and_revisions(self):
        self.upload([self.first, self.second])
        self.assertEqual(self.save(self.bound_sets()).status_code, 200)
        edit._rebuild("alpha", {"rename": {"rpm_a": "speed", "temp_b": "temperature", "power_a": "watts"},
                                "drop": ["temp_a", "power_b"], "trim_t0": 1, "trim_t1": 3})
        meta = store.get_meta("alpha")
        self.assertEqual(meta["component_sets_revision"], 2)
        first, second = meta["component_sets"]
        self.assertEqual(first["rpm_column"], "speed")
        self.assertIsNone(first["motor_temperature_column"])
        self.assertEqual(first["power_column"], "watts")
        self.assertEqual(second["rpm_column"], "rpm_b")
        self.assertEqual(second["motor_temperature_column"], "temperature")
        self.assertEqual(second["motor_temperature_unit"], "F")
        self.assertIsNone(second["power_column"])
        self.assertEqual(meta["component_rpm_column"], "speed")
        self.assertEqual(meta["component_rpm_revision"], 2)
        self.assertEqual(self.save(self.bound_sets(), 1).status_code, 409)
        edit._rebuild("alpha", {"nan_policy": "zero_fill"})
        self.assertEqual(store.get_meta("alpha")["component_sets_revision"], 2)
        self.assertEqual(self.client.post("/api/tests/alpha/rename?new_name=beta").status_code, 200)
        self.assertEqual(self.client.delete("/api/tests/beta").status_code, 200)
        entries = self.client.get("/api/trash").json()["entries"]
        self.assertEqual(entries[0]["component_sets"], meta["component_sets"])
        self.assertEqual(self.client.post("/api/tests/beta/restore").status_code, 200)
        self.assertEqual(store.get_meta("beta")["component_sets"], meta["component_sets"])
        self.assertEqual(store.get_meta("beta")["component_sets_revision"], 2)

    def test_upload_resume_and_metadata_reads_do_not_need_registry(self):
        response = self.client.post("/api/uploads", json={**self.init, "component_sets": [self.first, self.second]})
        self.assertEqual(response.status_code, 201, response.text)
        components._path().write_text("invalid", encoding="utf-8")
        self.assertEqual(self.client.post("/api/uploads", json={**self.init, "component_sets": [self.first, self.second]}).status_code, 200)
        self.complete(response.json())
        self.assertEqual(self.client.get("/api/tests/alpha").status_code, 200)
        self.assertEqual(self.save([], 0).status_code, 200)

    def test_legacy_writes_and_column_edits_invalidate_open_canonical_drafts(self):
        self.upload()
        draft = components.sets(store.get_meta("alpha"))
        response = self.client.patch("/api/tests/alpha/meta", json={"components": self.first["components"],
            "expected_components_revision": 0})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["component_sets_revision"], 1)
        self.assertEqual(self.save(draft, 0, notes="stale associations").status_code, 409)
        draft = components.sets(store.get_meta("alpha"))
        response = self.client.patch("/api/tests/alpha/meta", json={"component_rpm_column": "rpm_a",
            "expected_component_rpm_revision": 0})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["component_sets_revision"], 2)
        self.assertEqual(self.save(draft, 1, notes="stale RPM").status_code, 409)
        response = self.client.patch("/api/tests/alpha/meta", json={"component_rpm_column": "rpm_a",
            "expected_component_rpm_revision": 1})
        self.assertEqual(response.json()["component_sets_revision"], 2)
        draft = components.sets(store.get_meta("alpha"))
        edit._rebuild("alpha", {"rename": {"rpm_a": "speed"}})
        self.assertEqual(store.get_meta("alpha")["component_sets_revision"], 3)
        self.assertEqual(self.save(draft, 2, notes="stale columns").status_code, 409)
        self.assertNotIn("notes", store.get_meta("alpha"))
        self.assertEqual(self.save(components.sets(store.get_meta("alpha")), 3).status_code, 200)

    def test_invalid_saved_associations_do_not_block_scientific_exports(self):
        self.upload()
        initial = store.get_meta("alpha")
        for configuration in ({"components": {"motor": "bad-id"}},
                              {"component_sets": [{"id": "bad-id", "name": "Saved"}]},
                              {"component_sets": None}):
            with self.subTest(configuration=configuration):
                meta = {**initial, **configuration}
                store.write_json_atomic(self.tests / "alpha/meta.json", meta)
                snapshot = analysis_metadata.source_context("alpha", meta, ["rpm_a"], 0, 40)
                self.assertEqual(snapshot["source"]["component_sets_status"], "invalid_saved_configuration")
                self.assertEqual(snapshot["source"]["component_sets"], configuration["component_sets"]
                                 if "component_sets" in configuration else [{"id": "legacy", "name": "Set 1",
                                     "components": configuration["components"], "rpm_column": None}])
                response = self.client.post("/api/plot-export", json={"column": "rpm_a", "data": "original",
                    "include_metadata": True, "sources": [{"test": "alpha"}]})
                self.assertEqual(response.status_code, 200, response.text if response.status_code != 200 else "")

    def test_long_existing_column_names_are_valid_bindings(self):
        column = "motor temperature sensor " + "x" * 260
        proposed = [{**self.first, "motor_temperature_column": column}]
        self.assertEqual(components.validate_sets(proposed, {"columns": ["time", column], "time_column": "time"})[0]
                         ["motor_temperature_column"], column)

    def test_explicit_guarded_replacement_repairs_corrupt_sets_and_legacy_projection(self):
        self.upload([self.first, self.second])
        initial = {**store.get_meta("alpha"), "notes": "Keep findings", "component_sets_revision": 7}
        files = {path: path.read_bytes() for path in (self.tests / "alpha").rglob("*")
                 if path.is_file() and path.name != "meta.json"}
        path = self.tests / "alpha/meta.json"
        cases = [{**initial, "component_sets": None}, {**initial, "component_sets": [None]},
                 {**initial, "component_sets": [{"id": "bad", "name": "Saved"}]},
                 {**initial, "components": {"motor": "bad"}},
                 {**initial, "component_sets": None, "components": {"motor": "bad"}}]
        legacy = {key: value for key, value in initial.items() if key != "component_sets"}
        cases.append({**legacy, "components": {"motor": "bad"}})
        for saved in cases:
            with self.subTest(saved=saved):
                store.write_json_atomic(path, saved)
                before = path.read_bytes()
                conflict = self.save([self.first], 6, notes="Stale finding")
                self.assertEqual(conflict.status_code, 409, conflict.text)
                self.assertEqual(path.read_bytes(), before)
                invalid = self.save([{**self.first, "power_column": "missing"}], 7)
                self.assertEqual(invalid.status_code, 422, invalid.text)
                self.assertEqual(path.read_bytes(), before)
                with patch.object(store, "write_json_atomic", side_effect=OSError("disk unavailable")):
                    self.assertEqual(self.save([self.first], 7).status_code, 500)
                self.assertEqual(path.read_bytes(), before)
                repaired = self.save([self.first], 7)
                self.assertEqual(repaired.status_code, 200, repaired.text)
                meta = repaired.json()
                self.assertEqual(meta["component_sets"], [self.first])
                self.assertEqual(meta["component_sets_revision"], 8)
                self.assertEqual(meta["notes"], "Keep findings")
                self.assertEqual(meta["components"], self.first["components"])
                if saved["components"].get("motor") == "bad":
                    self.assertEqual(meta["components_revision"], saved["components_revision"] + 1)
                for source, content in files.items():
                    self.assertEqual(source.read_bytes(), content, str(source))
        # Canonical sets may remain identical while only their compatibility
        # projection is damaged. Repair it without inventing a semantic set edit.
        saved = {**initial, "components": {"motor": "bad"}}
        store.write_json_atomic(path, saved)
        result = self.save(initial["component_sets"], 7)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["component_sets_revision"], 7)
        self.assertEqual(result.json()["components_revision"], initial["components_revision"] + 1)
