"""Full-resolution TP summary semantics, precision and cache compatibility."""
import json
import math
from unittest.mock import patch

import numpy as np
import polars as pl
from fastapi.testclient import TestClient

from app import main, store
from ._base import DataDirTestCase


class TpSummaryTests(DataDirTestCase):
    def setUp(self):
        super().setUp()
        self.directory = self.tests / "summary"
        self.directory.mkdir()
        self.client = TestClient(main.app)

    def fixture(self, values, points=None):
        pl.DataFrame({"time": 10 + np.arange(len(values)) / 10,
                      "signal": pl.Series(values, dtype=pl.Float64)}).write_parquet(
            self.directory / "data.parquet", row_group_size=3)
        store.write_json_atomic(self.directory / "meta.json", {
            "name": "summary", "fs_hz": 10, "n_rows": len(values),
            "columns": ["time", "signal"], "time_column": "time", "t_start": 10})
        store.write_json_atomic(self.directory / "testpoints.json", {
            "version": 1, "test": "summary", "test_points": points or [
                {"id": 1, "name": "run", "label": "", "start_s": 10,
                 "end_s": None, "start_idx": 0, "end_idx": len(values)}]})

    def result(self):
        response = self.client.get("/api/tests/summary/tp_stats?col=signal")
        self.assertEqual(response.status_code, 200, response.text)
        values = response.json()
        json.dumps(values, allow_nan=False)
        return values

    def test_population_sd_and_full_resolution_mean_ignore_display_reduction(self):
        values = [500, 1, 0, 80, -20, 0, 0, 2, -500]
        self.fixture(values, [{"id": 1, "name": "run", "label": "",
                               "start_s": 10.1, "end_s": 10.8,
                               "start_idx": 1, "end_idx": 8}])
        result = self.result()[0]
        expected_mean = 63 / 7
        expected_sd = math.sqrt(sum((v - expected_mean) ** 2 for v in values[1:8]) / 7)
        self.assertEqual((result["n"], result["n_valid"]), (7, 7))
        summary = result["summary"]
        self.assertEqual((summary["i0"], summary["i1"]), (1, 8))
        self.assertEqual(summary["method"], "finite-population-v1")
        self.assertAlmostEqual(summary["mean"], expected_mean)
        self.assertAlmostEqual(summary["std_population"], expected_sd)
        trace = store.read_testpoint_trace("summary", 1, ["signal"], max_points=4)
        self.assertEqual(trace["mode"], "envelope")
        self.assertNotAlmostEqual(float(np.mean(trace["series"]["signal"]["y"])), expected_mean)
        self.assertEqual(self.result(), [result])  # reduced trace cannot change stats

    def test_excludes_null_nan_and_both_infinities(self):
        self.fixture([1, None, float("nan"), float("inf"), -float("inf"), 3])
        result = self.result()[0]
        self.assertEqual((result["n"], result["n_valid"]), (6, 2))
        self.assertEqual(result["summary"]["mean"], 2)
        self.assertEqual(result["summary"]["std_population"], 1)  # not sample SD sqrt(2)

    def test_no_finite_samples_reports_null_not_zero(self):
        self.fixture([None, float("nan"), float("inf")])
        result = self.result()[0]
        self.assertEqual(result["n_valid"], 0)
        self.assertIsNone(result["summary"]["mean"])
        self.assertIsNone(result["summary"]["std_population"])

    def test_empty_and_singleton_points(self):
        self.fixture([7, 9], [
            {"id": 1, "name": "empty", "label": "", "start_s": 10,
             "end_s": 10, "start_idx": 0, "end_idx": 0},
            {"id": 2, "name": "one", "label": "", "start_s": 10.1,
             "end_s": 10.2, "start_idx": 1, "end_idx": 2}])
        empty, one = self.result()
        self.assertEqual(empty["n"], 0)
        self.assertIsNone(empty["summary"]["i0"])
        self.assertIsNone(empty["summary"]["std_population"])
        self.assertEqual(one["summary"]["mean"], 9)
        self.assertEqual(one["summary"]["std_population"], 0)

    def test_unrounded_small_unit_values_preserve_legacy_scatter_fields(self):
        self.fixture([1e-10, 2e-10, 3e-10])
        result = self.result()[0]
        self.assertEqual((result["mean"], result["min"], result["max"]), (0, 0, 0))
        self.assertTrue(math.isclose(result["summary"]["mean"], 2e-10, rel_tol=1e-12))
        self.assertTrue(math.isclose(result["summary"]["std_population"], math.sqrt(2/3)*1e-10, rel_tol=1e-12))

    def test_finite_extremes_and_high_offset_stay_serializable(self):
        for values, mean, std in [
            ([1e308] * 4, 1e308, 0),
            ([-1e200, 1e200], 0, 1e200),
            ([-1e-200, 1e-200], 0, 1e-200),
            ([1e12 + 1, 1e12 + 2, 1e12 + 3], 1e12 + 2, math.sqrt(2/3)),
            ([0, 0, 0], 0, 0),
        ]:
            with self.subTest(values=values):
                self.fixture(values)
                summary = self.result()[0]["summary"]
                self.assertTrue(math.isclose(summary["mean"], mean, rel_tol=1e-12))
                self.assertTrue(math.isclose(summary["std_population"], std, rel_tol=1e-12))

    def test_legacy_time_only_and_open_ended_bounds_match_tp_trace(self):
        self.fixture([1, 2, 3, 4, 5, 6], [
            {"id": 1, "name": "old", "label": "", "start_s": 10.1, "end_s": None},
            {"id": 2, "name": "last", "label": "", "start_s": 10.4, "end_s": None}])
        for result in self.result():
            trace = store.read_testpoint_trace("summary", result["id"], ["signal"], max_points=6)
            summary = result["summary"]
            self.assertEqual((summary["i0"], summary["i1"]), (trace["i0"], trace["i1"]))
            self.assertAlmostEqual(summary["mean"], float(np.mean(trace["series"]["signal"]["y"])))

    def test_v1_cache_upgrades_lazily_and_new_summary_is_cached(self):
        self.fixture([1, 2, 3])
        store.write_json_atomic(self.directory / "tp_stats.json", {
            "version": 1, "fingerprint": store._tp_stats_fingerprint("summary"),
            "columns": {"signal": [{"id": 1, "mean": 999}]}})
        result = self.result()
        self.assertEqual(result[0]["summary"]["mean"], 2)
        self.assertEqual(store._read_json(self.directory / "tp_stats.json")["version"], 2)
        with patch.object(store, "_compute_tp_stats", side_effect=AssertionError("cache miss")):
            self.assertEqual(self.result(), result)

    def test_data_and_tp_changes_refresh_summary_and_manual_rebuild(self):
        self.fixture([1, 2, 3, 4])
        self.assertEqual(self.result()[0]["summary"]["mean"], 2.5)
        self.fixture([2, 4, 6, 8])
        self.assertEqual(self.result()[0]["summary"]["mean"], 5)
        points = store.read_testpoints("summary")
        points["test_points"][0]["end_idx"] = 2
        store.write_testpoints("summary", points)
        expected = self.result()
        self.assertEqual(expected[0]["summary"]["mean"], 3)
        response = self.client.post("/api/tests/summary/tp_stats/rebuild")
        self.assertEqual(response.json()["columns_recomputed"], 1)
        self.assertEqual(self.result(), expected)
