"""CSV export endpoints, raw.csv download, and the tp_stats sidecar cache."""

import io
import asyncio
import csv
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
        self.assertEqual(df.columns, ["time", "thrust", "test_point_id"])
        self.assertEqual(df["test_point_id"].to_list(), [3, 3, 3])
        self.assertEqual(df["thrust"].to_list(), [4.0, 6.0, 8.0])

    def test_adjacent_legacy_and_open_points_keep_their_own_ids(self):
        store.write_testpoints("alpha", {"test_points": [
            {"id": 9, "start_s": 100.7, "end_s": None},
            {"id": 0, "start_s": 100.0, "end_s": 100.2},
            # Deliberately inconsistent times: saved indices win.
            {"id": 3, "start_s": 100.2, "end_s": 100.9,
             "start_idx": 2, "end_idx": 5},
            {"id": -4, "start_s": 100.5, "end_s": None},
        ]})
        for tp_id, expected in [(0, [0, 2]), (3, [4, 6, 8]),
                                (-4, [10, 12]), (9, [14, 16, 18])]:
            with self.subTest(tp_id=tp_id):
                r = self.client.get(f"/api/tests/alpha/testpoints/{tp_id}/export")
                self.assertEqual(r.status_code, 200)
                df = pl.read_csv(io.BytesIO(r.content))
                self.assertEqual(df["thrust"].to_list(), expected)
                self.assertEqual(df["test_point_id"].to_list(), [tp_id] * len(expected))

    def test_draft_exports_new_and_edited_points_without_persisting(self):
        saved = (self.tests / "alpha/testpoints.json").read_bytes()
        for tp_id in (3, 20):
            with self.subTest(tp_id=tp_id):
                r = self.client.get(f"/api/tests/alpha/testpoints/{tp_id}/export"
                                    "?start_idx=4&end_idx=7&cols=rpm,time,rpm")
                self.assertEqual(r.status_code, 200)
                self.assertIn(f"alpha_tp{tp_id}_draft.csv", r.headers["content-disposition"])
                df = pl.read_csv(io.BytesIO(r.content))
                self.assertEqual(df.columns, ["time", "rpm", "test_point_id"])
                self.assertEqual(df["rpm"].to_list(), [1004, 1005, 1006])
                self.assertEqual(df["test_point_id"].to_list(), [tp_id] * 3)
        self.assertEqual((self.tests / "alpha/testpoints.json").read_bytes(), saved)

    def test_draft_clamps_and_rejects_invalid_ranges(self):
        url = "/api/tests/alpha/testpoints/3/export"
        r = self.client.get(url + "?start_idx=-50&end_idx=50")
        self.assertEqual(pl.read_csv(io.BytesIO(r.content)).height, 10)
        for query, status in [("start_idx=2", 400), ("end_idx=3", 400),
                              ("start_idx=3&end_idx=3", 400),
                              ("start_idx=5&end_idx=2", 400),
                              ("start_idx=10&end_idx=20", 400),
                              ("start_idx=nan&end_idx=2", 422),
                              ("start_idx=1.5&end_idx=2", 422)]:
            with self.subTest(query=query):
                r = self.client.get(url + "?" + query)
                self.assertEqual(r.status_code, status)
                self.assertNotIn("content-disposition", r.headers)

    def test_identity_collision_preserves_all_source_values_and_subset_names(self):
        directory = self.tests / "alpha"
        frame = pl.read_parquet(directory / "data.parquet").with_columns(
            pl.lit(99).alias("test_point_id"),
            pl.lit(101).alias("source_test_point_id"))
        frame.write_parquet(directory / "data.parquet")
        meta = store.get_meta("alpha")
        meta["columns"] = frame.columns
        store.write_json_atomic(directory / "meta.json", meta)
        for cols in ("", "?cols=test_point_id,thrust"):
            with self.subTest(cols=cols):
                r = self.client.get("/api/tests/alpha/testpoints/3/export" + cols)
                df = pl.read_csv(io.BytesIO(r.content))
                self.assertEqual(df["test_point_id"].to_list(), [3] * 3)
                self.assertEqual(df["source_source_test_point_id"].to_list(), [99] * 3)
                self.assertEqual(df.columns[0], "time")
                if not cols:
                    self.assertEqual(df["source_test_point_id"].to_list(), [101] * 3)
        # Generic export retains the original schema and acquisition ID.
        raw = self.client.get("/api/tests/alpha/export")
        self.assertEqual(pl.read_csv(io.BytesIO(raw.content))["test_point_id"].to_list(), [99] * 10)

    def test_time_column_can_collide_with_identity(self):
        directory = self.tests / "alpha"
        frame = pl.read_parquet(directory / "data.parquet").rename({"time": "test_point_id"})
        frame.write_parquet(directory / "data.parquet")
        meta = store.get_meta("alpha")
        meta.update(columns=frame.columns, time_column="test_point_id")
        store.write_json_atomic(directory / "meta.json", meta)
        r = self.client.get("/api/tests/alpha/testpoints/3/export?cols=thrust")
        df = pl.read_csv(io.BytesIO(r.content))
        self.assertEqual(df.columns, ["source_test_point_id", "thrust", "test_point_id"])
        self.assertEqual(df["source_test_point_id"].to_list(), frame["test_point_id"][2:5].to_list())

    def test_full_resolution_multibatch_export_preserves_missing_and_precision(self):
        directory = self.tests / "alpha"
        n = 140010
        signal = pl.Series("signal", np.arange(n) * 1.234567890123e-12).scatter(
            [3, 4, 5, 6], [None, float("nan"), float("inf"), -float("inf")])
        frame = pl.DataFrame({"time": np.arange(n) / 10, "signal": signal})
        frame.write_parquet(directory / "data.parquet", row_group_size=70000)
        meta = store.get_meta("alpha")
        meta.update(columns=frame.columns, n_rows=n, t_start=0)
        store.write_json_atomic(directory / "meta.json", meta)
        r = self.client.get(f"/api/tests/alpha/testpoints/12/export?start_idx=2&end_idx={n - 2}")
        self.assertEqual(r.status_code, 200)
        # Arrow writes lowercase nan/inf; Polars' inference classifies that
        # column as text. A numeric reader must use the known source dtype.
        df = pl.read_csv(io.BytesIO(r.content), schema_overrides={"signal": pl.Float64})
        self.assertEqual(df.height, n - 4)
        self.assertEqual(df["test_point_id"].unique().to_list(), [12])
        self.assertEqual(df["signal"].null_count(), 1)
        self.assertEqual(df["signal"].is_nan().sum(), 1)
        np.testing.assert_array_equal(df["signal"].to_numpy(), frame["signal"][2:-2].to_numpy())
        self.assertEqual(r.content.count(b'"test_point_id"'), 1)

    def test_identifier_larger_than_int64_is_preserved(self):
        tp_id = 10 ** 30
        r = self.client.get(f"/api/tests/alpha/testpoints/{tp_id}/export?start_idx=0&end_idx=1")
        self.assertEqual(r.status_code, 200)
        rows = list(csv.DictReader(io.StringIO(r.text)))
        self.assertEqual(rows[0]["test_point_id"], str(tp_id))

    def test_missing_busy_failed_and_empty_tp_export_fail_before_attachment(self):
        self.assertEqual(self.client.get("/api/tests/missing/testpoints/3/export").status_code, 404)
        for status in ("rebuilding", "error"):
            store.write_json_atomic(self.tests / "alpha/status.json", {"status": status})
            r = self.client.get("/api/tests/alpha/testpoints/3/export")
            self.assertEqual(r.status_code, 409)
            self.assertNotIn("content-disposition", r.headers)
        store.write_json_atomic(self.tests / "alpha/status.json", {"status": "ready"})
        store.write_testpoints("alpha", {"test_points": [
            {"id": 3, "start_s": 100.2, "start_idx": 2, "end_idx": 2}]})
        self.assertEqual(self.client.get("/api/tests/alpha/testpoints/3/export").status_code, 400)
        self.assertEqual(self.client.get("/api/tests/alpha/testpoints/3/export?cols=nope").status_code, 400)

    def test_stream_resolves_saved_bounds_again_after_preflight(self):
        response = main.api_export_testpoint("alpha", 3, "thrust")
        store.write_testpoints("alpha", {"test_points": [
            {"id": 3, "start_s": 100.6, "start_idx": 6, "end_idx": 8}]})

        async def consume():
            return b"".join([part async for part in response.body_iterator])

        df = pl.read_csv(io.BytesIO(asyncio.run(consume())))
        self.assertEqual(df["thrust"].to_list(), [12, 14])
        self.assertEqual(df["test_point_id"].to_list(), [3, 3])

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
