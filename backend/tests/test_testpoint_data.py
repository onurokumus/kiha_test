import unittest

import numpy as np
import polars as pl
from fastapi import HTTPException

from app import main, store
from app.config import POINT_BUDGET_CAP
from ._base import DataDirTestCase


class TestPointDataTests(DataDirTestCase):
    def setUp(self):
        super().setUp()
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

    def test_vectorized_envelope_matches_brute_force_across_row_groups(self):
        """The reduceat-vectorized envelope (perf 2.6) must be byte-identical
        to a straightforward Python reference — including buckets that straddle
        parquet row-group / batch seams and buckets that are entirely NaN."""
        rng = np.random.default_rng(20260719)
        for trial, row_group in enumerate((7, 50, 4096)):
            n = 5000 + trial
            time = np.arange(n, dtype=np.float64) * 0.001 + 10.0
            values = rng.normal(size=n) * 25.0
            # constant runs + NaN patches to stress run-detection and empty
            # (all-NaN) buckets that must fall back to a bucket_t / NaN sample.
            values[1000:1200] = 3.0
            values[2000:2400] = np.nan
            values[123] = np.inf              # treated as non-finite, like NaN
            name = f"stress{trial}"
            directory = self.tests / name
            directory.mkdir()
            pl.DataFrame({"time": time, "v": values}).write_parquet(
                directory / "data.parquet", row_group_size=row_group)
            store.write_json_atomic(directory / "meta.json", {
                "name": name, "fs_hz": 1000.0, "n_rows": n,
                "columns": ["time", "v"], "time_column": "time",
                "t_start": 10.0})
            i0, i1 = 37, n - 11
            store.write_json_atomic(directory / "testpoints.json", {
                "version": 1, "test": name, "test_points": [{
                    "id": 1, "name": "r", "label": "", "notes": "",
                    "start_s": round(float(time[i0]), 6),
                    "end_s": round(float(time[i1]), 6),
                    "start_idx": i0, "end_idx": i1}]})

            result = store.read_testpoint_trace(
                name, 1, ["v"], max_points=600)
            self.assertEqual(result["mode"], "envelope")
            exp_t, exp_y = _brute_force_trace(time, values, i0, i1, 600)
            got = result["series"]["v"]
            self.assertEqual(len(got["t"]), len(exp_t))
            for a, b in zip(got["t"], exp_t):
                self.assertAlmostEqual(a, b, places=9)
            for a, b in zip(got["y"], exp_y):
                if b is None:
                    self.assertIsNone(a)
                else:
                    self.assertAlmostEqual(a, b, places=6)


def _brute_force_trace(t, y, i0, i1, max_points):
    """Reference envelope reduction — the semantics store.read_testpoint_trace
    must preserve: first/last samples kept, interior split into equal buckets,
    each bucket contributing its finite min & max at their sample times in
    sample order (ties → earliest), all-NaN buckets → one NaN at the bucket's
    first sample time."""
    import math
    n_raw = i1 - i0
    interior_n = n_raw - 2
    bucket_count = min(interior_n, (max_points - 2) // 2)
    edges = 1 + (np.arange(bucket_count + 1, dtype=np.int64)
                 * interior_n // bucket_count)
    origin = float(t[i0])
    trace_t = [origin]
    trace_y = [float(y[i0])]
    for b in range(bucket_count):
        a0, a1 = i0 + int(edges[b]), i0 + int(edges[b + 1])
        seg_y, seg_t = y[a0:a1], t[a0:a1]
        finite = np.flatnonzero(np.isfinite(seg_y))
        if not len(finite):
            trace_t.append(float(t[a0]))
            trace_y.append(math.nan)
            continue
        fv = seg_y[finite]
        lo, hi = int(finite[np.argmin(fv)]), int(finite[np.argmax(fv)])
        extrema = [(lo, float(seg_t[lo]), float(seg_y[lo]))]
        if hi != lo:
            extrema.append((hi, float(seg_t[hi]), float(seg_y[hi])))
        extrema.sort(key=lambda it: it[0])
        for _, st, v in extrema:
            trace_t.append(st)
            trace_y.append(v)
    trace_t.append(float(t[i0 + n_raw - 1]))
    trace_y.append(float(y[i0 + n_raw - 1]))
    rel = list(np.round(np.asarray(trace_t) - origin, 6))
    out_y = [(None if not math.isfinite(v) else round(float(v), 6))
             for v in trace_y]
    return rel, out_y


if __name__ == "__main__":
    unittest.main()
