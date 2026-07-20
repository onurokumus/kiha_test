"""CSV export endpoints, raw.csv download, and the tp_stats sidecar cache."""

import io
import unittest
from unittest.mock import patch

import numpy as np
import polars as pl
from fastapi.testclient import TestClient

from app import main, store
from ._base import DataDirTestCase


class ExportTests(DataDirTestCase):
    def setUp(self):
        super().setUp()
        directory = self.tests / "alpha"
        directory.mkdir()
        time = 100.0 + np.arange(10, dtype=np.float64) * 0.1
        thrust = np.arange(10, dtype=np.float64) * 2.0
        rpm = 1000.0 + np.arange(10, dtype=np.float64)
        pl.DataFrame({"time": time, "thrust": thrust, "rpm": rpm}).write_parquet(
            directory / "data.parquet", row_group_size=4)
        (directory / "raw.csv").write_bytes(b"original,upload\n1,2\n")
        store.write_json_atomic(directory / "status.json", {"status": "ready"})
        store.write_json_atomic(directory / "meta.json", {
            "name": "alpha",
            "fs_hz": 10.0,
            "n_rows": 10,
            "columns": ["time", "thrust", "rpm"],
            "time_column": "time",
            "t_start": 100.0,
            "source_file": "My Rig Run.csv",
        })
        store.write_json_atomic(directory / "testpoints.json", {
            "version": 1,
            "test": "alpha",
            "test_points": [{
                "id": 3, "name": "run", "label": "", "start_s": 100.2,
                "end_s": 100.5, "start_idx": 2, "end_idx": 5, "notes": "",
            }],
        })
        self.client = TestClient(main.app)

    def test_full_export_roundtrips_all_rows(self):
        r = self.client.get("/api/tests/alpha/export")
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r.headers["content-disposition"])
        df = pl.read_csv(io.BytesIO(r.content))
        self.assertEqual(df.columns, ["time", "thrust", "rpm"])
        self.assertEqual(df.height, 10)
        self.assertAlmostEqual(float(df["thrust"][9]), 18.0)

    def test_windowed_export_selects_columns_and_rows(self):
        r = self.client.get(
            "/api/tests/alpha/export?cols=thrust&t0=100.2&t1=100.4")
        self.assertEqual(r.status_code, 200)
        df = pl.read_csv(io.BytesIO(r.content))
        self.assertEqual(df.columns, ["time", "thrust"])
        # exactly the same clamp rule as /data windows
        i0, i1 = store.window_bounds(store.get_meta("alpha"), 100.2, 100.4)
        self.assertEqual(df.height, i1 - i0)

    def test_export_rejects_unknown_column(self):
        r = self.client.get("/api/tests/alpha/export?cols=nope")
        self.assertEqual(r.status_code, 400)

    def test_export_dedupes_an_explicit_time_column(self):
        r = self.client.get("/api/tests/alpha/export?cols=time,thrust")
        self.assertEqual(r.status_code, 200)
        df = pl.read_csv(io.BytesIO(r.content))
        self.assertEqual(df.columns, ["time", "thrust"])

    def test_testpoint_export_uses_exact_index_bounds(self):
        r = self.client.get("/api/tests/alpha/testpoints/3/export?cols=thrust")
        self.assertEqual(r.status_code, 200)
        self.assertIn("alpha_tp3.csv", r.headers["content-disposition"])
        df = pl.read_csv(io.BytesIO(r.content))
        self.assertEqual(df.height, 3)  # rows [2, 5)
        self.assertEqual(df["thrust"].to_list(), [4.0, 6.0, 8.0])

    def test_missing_testpoint_export_is_404(self):
        r = self.client.get("/api/tests/alpha/testpoints/99/export")
        self.assertEqual(r.status_code, 404)

    def test_raw_download_returns_original_bytes_and_source_name(self):
        r = self.client.get("/api/tests/alpha/raw")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, b"original,upload\n1,2\n")
        self.assertIn("My%20Rig%20Run.csv",
                      r.headers["content-disposition"].replace(" ", "%20"))

    def test_raw_download_404_when_missing(self):
        (self.tests / "alpha" / "raw.csv").unlink()
        r = self.client.get("/api/tests/alpha/raw")
        self.assertEqual(r.status_code, 404)

    def test_tp_stats_sidecar_caches_and_invalidates(self):
        first = store.tp_stats("alpha", "thrust")
        self.assertAlmostEqual(first[0]["mean"], 6.0)  # rows 2..4 -> 4,6,8
        self.assertTrue((self.tests / "alpha" / "tp_stats.json").is_file())

        # second call must come from the sidecar (recompute forbidden)
        with patch.object(store, "_compute_tp_stats",
                          side_effect=AssertionError("cache miss")):
            second = store.tp_stats("alpha", "thrust")
        self.assertEqual(first, second)

        # a test-point save replaces testpoints.json -> fingerprint changes
        # -> the sidecar self-invalidates and stats are recomputed
        store.write_testpoints("alpha", {
            "version": 1,
            "test": "alpha",
            "test_points": [{
                "id": 3, "name": "run", "label": "", "start_s": 100.0,
                "end_s": 100.2, "start_idx": 0, "end_idx": 2, "notes": "",
            }],
        })
        third = store.tp_stats("alpha", "thrust")
        self.assertAlmostEqual(third[0]["mean"], 1.0)  # rows 0..1 -> 0,2

    def test_rebuild_endpoint_refreshes_cached_columns_only(self):
        store.tp_stats("alpha", "thrust")  # populate one column

        r = self.client.post("/api/tests/alpha/tp_stats/rebuild")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["columns_recomputed"], 1)

        # the freshly written sidecar is valid: a read must not recompute
        with patch.object(store, "_compute_tp_stats",
                          side_effect=AssertionError("should be cached")):
            again = store.tp_stats("alpha", "thrust")
        self.assertAlmostEqual(again[0]["mean"], 6.0)

    def test_rebuild_endpoint_is_a_noop_with_an_empty_cache(self):
        r = self.client.post("/api/tests/alpha/tp_stats/rebuild")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["columns_recomputed"], 0)


if __name__ == "__main__":
    unittest.main()
