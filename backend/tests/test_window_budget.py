"""Window serving: the per-plot point budget (bug 2.3), the shared level/merge
helpers (bug 6.1), and the unified finite-safe serializer (bugs 2.7 / 6.10)."""

import math
import unittest

import numpy as np
import polars as pl

from app import store
from app.config import PYRAMID_LEVELS
from app.ingest import build_pyramid
from ._base import DataDirTestCase


class SerializerTests(unittest.TestCase):
    def test_rounds_and_maps_non_finite_to_none(self):
        out = store.to_json_list(
            np.array([1.23456789, np.nan, np.inf, -np.inf, 2.0]))
        self.assertEqual(out, [1.234568, None, None, None, 2.0])

    def test_all_finite_fast_path_returns_plain_floats(self):
        out = store.to_json_list(np.array([0.0, -1.5, 3.0]))
        self.assertEqual(out, [0.0, -1.5, 3.0])
        self.assertTrue(all(isinstance(v, float) for v in out))


class BudgetHelperTests(unittest.TestCase):
    def test_plot_budget_floor_and_cap(self):
        self.assertEqual(store.plot_budget(100), 1000)     # floored
        self.assertEqual(store.plot_budget(1500), 3000)    # ~2/px
        self.assertEqual(store.plot_budget(9000), 8000)    # capped

    def test_pick_level_prefers_coarser_for_a_smaller_budget(self):
        n_raw = 120_000
        self.assertEqual(store.pick_pyramid_level(n_raw, 8000), 16)
        self.assertEqual(store.pick_pyramid_level(n_raw, 1000), 256)
        # nothing fits an impossibly small budget -> coarsest level
        self.assertEqual(store.pick_pyramid_level(n_raw, 1),
                         PYRAMID_LEVELS[-1])

    def test_merge_over_cap_halves_and_keeps_extrema(self):
        t = np.arange(2000, dtype=np.float64)
        mn = np.arange(2000, dtype=np.float64)
        mx = np.arange(2000, dtype=np.float64) + 1000.0
        t2, series2, merge = store.merge_over_cap(
            t, {"c": (mn, mx)}, budget=1000)
        self.assertEqual(merge, 2)
        self.assertEqual(len(t2), 1000)
        # min-of-mins / max-of-maxes preserve the range extremes
        self.assertEqual(series2["c"][0][0], 0.0)
        self.assertEqual(series2["c"][1][-1], 2999.0)


class ReadWindowBudgetTests(DataDirTestCase):
    def setUp(self):
        super().setUp()
        self.n = 120_000
        directory = self.tests / "big"
        directory.mkdir()
        time = np.arange(self.n, dtype=np.float64) / 2048.0
        val = np.sin(time * 3.0)
        val[5000] = 999.0  # a spike the envelope must keep
        pl.DataFrame({"time": time, "val": val}).write_parquet(
            directory / "data.parquet", row_group_size=65536)
        build_pyramid(directory / "data.parquet", directory / "pyramid", "time")
        store.write_json_atomic(directory / "meta.json", {
            "name": "big", "fs_hz": 2048.0, "n_rows": self.n,
            "columns": ["time", "val"], "time_column": "time", "t_start": 0.0,
        })

    def test_small_plot_gets_a_smaller_envelope_than_a_wide_one(self):
        small = store.read_window("big", ["val"], None, None, px=100)
        wide = store.read_window("big", ["val"], None, None, px=4000)

        self.assertEqual(small["mode"], "envelope")
        self.assertEqual(wide["mode"], "envelope")
        # each fits its own budget (2 px), not the fixed 8000 cap (bug 2.3)
        self.assertLessEqual(len(small["t"]), store.plot_budget(100))
        self.assertLessEqual(len(wide["t"]), store.plot_budget(4000))
        # a 100 px plot must NOT receive the same ~7500-point payload a
        # 4000 px plot does
        self.assertLess(len(small["t"]), len(wide["t"]))

    def test_series_shape_and_spike_survive_the_budget(self):
        r = store.read_window("big", ["val"], None, None, px=100)
        s = r["series"]["val"]
        self.assertEqual(len(s["min"]), len(r["t"]))
        self.assertEqual(len(s["max"]), len(r["t"]))
        self.assertGreaterEqual(max(v for v in s["max"] if v is not None), 999.0)

    def test_zoomed_in_window_serves_raw(self):
        # a narrow window (< MAX_POINTS_RAW samples) comes back raw
        r = store.read_window("big", ["val"], 0.0, 0.5, px=1500)
        self.assertEqual(r["mode"], "raw")
        self.assertEqual(r["level"], 1)
        self.assertEqual(len(r["t"]), r["n_raw"])
        self.assertTrue(math.isclose(r["t"][0], 0.0, abs_tol=1e-9))


if __name__ == "__main__":
    unittest.main()
