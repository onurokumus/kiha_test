import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import polars as pl
from fastapi import HTTPException

from app import main, store
from app.config import POINT_BUDGET_CAP


class TestPointDataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.tests = Path(self.temp.name) / "tests"
        self.tests.mkdir()
        self.patchers = [
            patch.object(main, "TESTS_DIR", self.tests),
            patch.object(store, "TESTS_DIR", self.tests),
        ]
        for patcher in self.patchers:
            patcher.start()

        directory = self.tests / "alpha"
        directory.mkdir()
        time = 100.0 + np.arange(14, dtype=np.float64) * 0.01
        vibration = np.zeros(14, dtype=np.float64)
        vibration[2] = 900.0       # immediately before the point
        vibration[3] = 1.0         # retained first sample
        vibration[5] = 80.0        # narrow in-range spike
        vibration[6] = -20.0       # in-range trough after the spike
        vibration[9] = 2.0         # retained last sample
        vibration[10] = -900.0     # immediately after the point
        pl.DataFrame({"time": time, "vibration": vibration}).write_parquet(
            directory / "data.parquet", row_group_size=4)
        store.write_json_atomic(directory / "meta.json", {
            "name": "alpha",
            "fs_hz": 100.0,
            "n_rows": len(time),
            "columns": ["time", "vibration"],
            "time_column": "time",
            "t_start": 100.0,
        })
        store.write_json_atomic(directory / "testpoints.json", {
            "version": 1,
            "test": "alpha",
            "test_points": [{
                "id": 7,
                "name": "run",
                "label": "nominal",
                "start_s": 100.03,
                "end_s": 100.10,
                "start_idx": 3,
                "end_idx": 10,
                "notes": "",
            }],
        })

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def test_raw_trace_uses_exact_half_open_bounds_and_relative_time(self):
        result = store.read_testpoint_trace(
            "alpha", 7, ["vibration"], max_points=8)

        trace = result["series"]["vibration"]
        self.assertEqual(result["mode"], "raw")
        self.assertEqual(result["level"], 1)
        self.assertEqual(result["n_raw"], 7)
        self.assertEqual((result["i0"], result["i1"]), (3, 10))
        self.assertEqual(trace["t"][0], 0.0)
        self.assertEqual(trace["t"][-1], 0.06)
        self.assertEqual(trace["y"], [1.0, 0.0, 80.0, -20.0,
                                      0.0, 0.0, 2.0])
        self.assertNotIn(900.0, trace["y"])
        self.assertNotIn(-900.0, trace["y"])

    def test_envelope_is_ordered_capped_and_keeps_a_narrow_spike(self):
        result = store.read_testpoint_trace(
            "alpha", 7, ["vibration"], max_points=4)

        trace = result["series"]["vibration"]
        self.assertEqual(result["mode"], "envelope")
        self.assertGreater(result["level"], 1)
        self.assertEqual(result["n_raw"], 7)
        self.assertEqual(trace["t"][0], 0.0)
        self.assertLessEqual(len(trace["t"]), 4)
        self.assertEqual(len(trace["t"]), len(trace["y"]))
        self.assertTrue(all(left <= right for left, right in
                            zip(trace["t"], trace["t"][1:])))
        self.assertIn(80.0, trace["y"])
        self.assertIn(-20.0, trace["y"])
        self.assertNotIn(900.0, trace["y"])
        self.assertNotIn(-900.0, trace["y"])

    def test_point_budget_is_strictly_bounded(self):
        for invalid in (3, POINT_BUDGET_CAP + 1):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                store.read_testpoint_trace(
                    "alpha", 7, ["vibration"], max_points=invalid)

    def test_endpoint_reports_unknown_point_and_column(self):
        with self.assertRaises(HTTPException) as missing_point:
            main.api_get_testpoint_data(
                "alpha", 99, "vibration", max_points=8)
        self.assertEqual(missing_point.exception.status_code, 404)

        with self.assertRaises(HTTPException) as unknown_column:
            main.api_get_testpoint_data(
                "alpha", 7, "missing", max_points=8)
        self.assertEqual(unknown_column.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
