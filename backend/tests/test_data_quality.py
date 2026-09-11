"""Upload quality provenance, current-data edits and metadata-only reads."""

import hashlib
import json
from datetime import datetime, timezone
from unittest.mock import patch

import numpy as np
from fastapi.testclient import TestClient

from app import edit, ingest, main, store
from app.quality import EXAMPLE_LIMIT
from ._base import DataDirTestCase


class DataQualityTests(DataDirTestCase):
    def setUp(self):
        super().setUp()
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)

    def import_csv(self, text, name="quality", **options):
        path = self.root / f"{name}.csv"
        path.write_text(text, encoding="utf-8")
        return ingest.ingest_csv(path, name, copy_raw=True, **options)

    def listed(self, name="quality"):
        response = self.client.get("/api/tests")
        self.assertEqual(response.status_code, 200)
        return next(item for item in response.json() if item["name"] == name)

    def test_clean_measured_import_is_fully_checked(self):
        meta = self.import_csv("time,signal\n100,2\n100.5,3\n101,4\n")
        self.assertEqual(meta["fs_hz"], 2)
        self.assertEqual(meta["source_time_quality"]["gap_count"], 0)
        self.assertEqual(meta["nan_counts"], {})
        self.assertEqual(meta["inf_counts"], {})
        self.assertEqual(self.listed()["data_quality"],
                         {"warnings": [], "partial": False})
        result = self.client.get("/api/tests/quality").json()
        self.assertEqual(result["source_time_quality"], meta["source_time_quality"])

    def test_quantized_duplicates_are_measured_and_remain_analyzable(self):
        rows = "\n".join(f"{20 + (i // 2) / 10:.1f},{i}" for i in range(40))
        meta = self.import_csv("time,signal\n" + rows)
        source = meta["source_time_quality"]
        self.assertEqual(source["duplicate_steps"]["count"], 20)
        self.assertEqual(source["duplicate_steps"]["examples"][0],
                         {"row": 2, "previous_s": 20.0, "time_s": 20.0})
        self.assertEqual(source["backward_steps"]["count"], 0)
        self.assertTrue(meta["time_quantized"])
        self.assertEqual(meta["time_source"], "measured")
        self.assertEqual(self.listed()["status"], "ready")
        self.assertIn("duplicate_steps", self.listed()["data_quality"]["warnings"])
        response = self.client.get("/api/tests/quality/spectrum?col=signal&mode=fft")
        self.assertEqual(response.status_code, 200, response.text)

    def test_fallback_keeps_defects_and_finite_source_examples(self):
        # Invalid rows must not be bridged when comparing neighbors. Infinities
        # and unparseable clock strings are invalid timestamps, not JSON floats.
        times = ["10", "11", "11", "9", "", "12", "bad", "8", "inf", "-inf"]
        meta = self.import_csv("time,signal\n" + "\n".join(
            f"{value},{i}" for i, value in enumerate(times)), assume_fs=50)
        source = meta["source_time_quality"]
        self.assertEqual(meta["time_source"], "generated")
        self.assertEqual(source["axis_reason"], "invalid_source_time")
        self.assertEqual(source["duplicate_steps"]["count"], 1)
        self.assertEqual(source["backward_steps"]["examples"],
                         [{"row": 4, "previous_s": 11.0, "time_s": 9.0}])
        self.assertEqual(source["invalid_timestamps"],
                         {"count": 4, "example_rows": [5, 7, 9, 10]})
        self.assertIsNone(source["gap_count"])
        json.dumps(source, allow_nan=False)
        data = store.read_window("quality", ["signal"], None, None, 100)
        np.testing.assert_allclose(data["t"], np.arange(10) / 50)
        self.assertTrue(self.listed()["data_quality"]["partial"])

    def test_explicit_generated_and_absent_time_are_not_source_checks(self):
        cases = [
            ("time,signal\n2,1\n1,2\n1,3\n",
             {"time_mode": "generated", "time_column": "time"}, "generated_requested"),
            ("signal,rpm\n1,10\n2,20\n3,30\n", {}, "no_time_column"),
        ]
        for index, (content, options, reason) in enumerate(cases):
            with self.subTest(reason=reason):
                name = f"generated-{index}"
                meta = self.import_csv(content, name, assume_fs=20, **options)
                self.assertFalse(meta["source_time_quality"]["checked"])
                self.assertEqual(meta["source_time_quality"]["axis_reason"], reason)
                self.assertNotIn("duplicate_steps", meta["source_time_quality"])
                self.assertTrue(self.listed(name)["data_quality"]["partial"])

    def test_counts_cover_batch_boundaries_while_examples_stay_bounded(self):
        n = ingest.INGEST_BATCH + 8
        rows = ["time,signal"]
        for i in range(n):
            value = "" if i == ingest.INGEST_BATCH - 1 else (
                "inf" if i == ingest.INGEST_BATCH else f"{i}.0")
            rows.append(f"{i // 2},{value}")
        meta = self.import_csv("\n".join(rows))
        repeated = meta["source_time_quality"]["duplicate_steps"]
        self.assertEqual(repeated["count"], n // 2)
        self.assertEqual(len(repeated["examples"]), EXAMPLE_LIMIT)
        self.assertEqual(meta["nan_counts"], {"signal": 1})
        self.assertEqual(meta["inf_counts"], {"signal": 1})
        self.assertEqual(meta["source_time_quality"]["gap_count"], 0)

    def test_gap_provenance_survives_trim_fill_and_column_edits(self):
        content = ("time,signal,other\n10,1,10\n10.5,,11\n11,3,12\n"
                   "13.5,inf,13\n14,5,14\n")
        meta = self.import_csv(content)
        source = meta["source_time_quality"]
        self.assertEqual(source["gap_examples"], [
            {"row": 4, "previous_s": 11.0, "time_s": 13.5, "missing_rows": 4}])
        self.assertEqual(meta["time_gap_ranges"], [[3, 7]])
        self.assertEqual(meta["nan_counts"], {"signal": 5, "other": 4})
        edit._rebuild("quality", {"trim_t0": 0.5, "trim_t1": 4.0,
                                  "rename": {"signal": "force"}, "drop": ["other"]})
        trimmed = store.get_meta("quality")
        self.assertEqual(trimmed["time_gap_ranges"], [[2, 6]])
        self.assertEqual(trimmed["inf_counts"], {"force": 1})
        self.assertEqual(trimmed["nan_counts"], {"force": 5})
        self.assertEqual(trimmed["source_time_quality"], source)
        edit._rebuild("quality", {"nan_policy": "zero_fill"})
        filled = store.get_meta("quality")
        self.assertEqual(filled["nan_counts"], {})
        self.assertEqual(filled["inf_counts"], {"force": 1})  # fill does not repair infinity
        self.assertEqual(filled["time_gap_ranges"], [])
        self.assertEqual(filled["source_time_quality"], source)
        self.assertEqual((self.tests / "quality" / "raw.csv").read_text(), content)
        warnings = self.listed()["data_quality"]["warnings"]
        self.assertIn("time_gaps", warnings)  # original gaps still matter after explicit fill
        self.assertNotIn("missing_values", warnings)
        self.assertIn("infinite_values", warnings)

    def test_formulas_update_current_missing_counts(self):
        self.import_csv("time,a,b\n0,1,1\n1,2,0\n2,3,1\n3,4,0\n")
        response = self.client.post("/api/tests/quality/edit", json={
            "formulas": [{"name": "ratio", "expression": "{a}/{b}"}]})
        self.assertEqual(response.status_code, 200, response.text)
        meta = store.get_meta("quality")
        self.assertEqual(meta["nan_counts"], {"ratio": 2})
        self.assertIn("missing_values", self.listed()["data_quality"]["warnings"])

    def test_metadata_revision_refreshes_edits_with_the_same_display_timestamp(self):
        self.import_csv("time,a,b\n0,1,1\n1,2,0\n2,3,1\n3,4,0\n")
        with patch.object(edit, "datetime") as clock:
            clock.now.return_value = datetime(2026, 9, 9, tzinfo=timezone.utc)
            edit._rebuild("quality", {"formulas": [
                {"name": "ratio", "expression": "{a}/{b}"}]})
            before = self.listed()
            edit._rebuild("quality", {"nan_policy": "zero_fill"})
            after = self.listed()
        self.assertEqual(before["edited_at"], after["edited_at"])
        self.assertEqual(before["n_rows"], after["n_rows"])
        self.assertNotEqual(before["quality_revision"], after["quality_revision"])
        self.assertIn("missing_values", before["data_quality"]["warnings"])
        self.assertNotIn("missing_values", after["data_quality"]["warnings"])

    def test_one_finite_sample_is_visible_but_cannot_produce_a_spectrum(self):
        text = "time,signal\n0,1\n" + "\n".join(f"{i}," for i in range(1, 20))
        meta = self.import_csv(text)
        self.assertEqual(meta["nan_counts"], {"signal": 19})
        self.assertEqual(self.listed()["status"], "ready")
        response = self.client.get("/api/tests/quality/spectrum?col=signal&mode=fft")
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("all NaN", response.json()["detail"])

    def test_legacy_metadata_remains_partial_without_bulk_reads_or_writes(self):
        self.import_csv("time,signal\n0,1\n1,\n2,3\n")
        meta = store.get_meta("quality")
        for key in ("source_time_quality", "inf_counts", "time_gap_count"):
            meta.pop(key)
        path = self.tests / "quality" / "meta.json"
        store.write_json_atomic(path, meta)
        before = path.read_bytes()
        with patch.object(store.pl, "read_parquet", side_effect=AssertionError("bulk read")), \
                patch.object(store.pq, "ParquetFile", side_effect=AssertionError("bulk read")):
            info = self.listed()
            self.assertEqual(info["data_quality"],
                             {"warnings": ["missing_values"], "partial": True})
        self.assertEqual(path.read_bytes(), before)
        edit._rebuild("quality", {"nan_policy": "zero_fill"})
        self.assertTrue(self.listed()["data_quality"]["partial"])
        self.assertNotIn("source_time_quality", store.get_meta("quality"))

    def test_failed_imports_keep_existing_analysis_blocker_and_reason(self):
        for name, text, reason in [
            ("one-row", "time,signal\n0,1\n", "at least 2 samples"),
            ("no-signals", "time,note\n0,hello\n1,world\n", "no numeric signal columns"),
        ]:
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, reason):
                    self.import_csv(text, name)
                info = self.listed(name)
                self.assertEqual(info["status"], "error")
                self.assertIn(reason, info["error"])
                self.assertIsNone(info["data_quality"])

    def test_excessive_gaps_are_still_rejected(self):
        with patch.object(ingest, "MAX_GAP_FILL_ROWS", 3):
            with self.assertRaisesRegex(ValueError, "missing rows"):
                self.import_csv("time,signal\n0,1\n1,2\n2,3\n20,4\n21,5\n")
        self.assertEqual(self.listed()["status"], "error")

    def test_source_gap_examples_are_bounded_but_count_is_exact(self):
        times = [value for group in range(12) for value in (group * 10, group * 10 + 1, group * 10 + 2)]
        meta = self.import_csv("time,signal\n" + "\n".join(f"{t},1" for t in times))
        self.assertEqual(meta["source_time_quality"]["gap_count"], 11)
        self.assertEqual(len(meta["source_time_quality"]["gap_examples"]), 8)

    def test_multipart_upload_publishes_quality_and_preserves_it_on_restore(self):
        body = b"time,signal\n0,1\n0.5,2\n0.5,3\n1,4\n"
        response = self.client.post("/api/uploads", json={
            "name": "quality", "source_file": "quality.csv", "size_bytes": len(body),
            "last_modified_ms": 1, "uploader_name": "Quality verification"})
        self.assertEqual(response.status_code, 201, response.text)
        upload_id = response.json()["upload_id"]
        response = self.client.put(f"/api/uploads/{upload_id}/chunks/0?name=quality",
                                   files={"file": ("chunk", body, "application/octet-stream")},
                                   headers={"X-Chunk-SHA256": hashlib.sha256(body).hexdigest()})
        self.assertEqual(response.status_code, 200, response.text)
        response = self.client.post(f"/api/uploads/{upload_id}/complete?name=quality")
        self.assertEqual(response.status_code, 200, response.text)
        info = self.listed()
        self.assertEqual(info["status"], "ready")
        self.assertIn("duplicate_steps", info["data_quality"]["warnings"])
        source = store.get_meta("quality")["source_time_quality"]
        self.assertEqual(self.client.delete("/api/tests/quality").status_code, 200)
        self.assertEqual(self.client.post("/api/tests/quality/restore").status_code, 200)
        self.assertEqual(store.get_meta("quality")["source_time_quality"], source)
        self.assertEqual(self.listed()["data_quality"], info["data_quality"])
