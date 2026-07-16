import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import polars as pl
from fastapi import BackgroundTasks, HTTPException

from app import edit, ingest, main, store
from app.main import EditOps

FS = 64.0
N = 640  # 10 s


class EditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.tests = Path(self.temp.name) / "tests"
        self.tests.mkdir()
        self.patchers = [
            patch.object(main, "TESTS_DIR", self.tests),
            patch.object(store, "TESTS_DIR", self.tests),
            patch.object(ingest, "TESTS_DIR", self.tests),
            patch.object(edit, "TESTS_DIR", self.tests),
        ]
        for p in self.patchers:
            p.start()

        # small real test: 10 s at 64 Hz with a NaN block in 'a'
        t = np.arange(N) / FS
        a = np.sin(t)
        a[100:110] = np.nan
        b = np.cos(t)
        csv = Path(self.temp.name) / "small.csv"
        pl.DataFrame({"time_s": t, "a": a, "b": b}).write_csv(csv)
        ingest._ingest_csv(csv, "alpha")

        store.write_json_atomic(self.tests / "alpha" / "testpoints.json", {
            "version": 1, "test": "alpha", "fs_hz": FS,
            "test_points": [
                {"id": 1, "name": "TP-01", "label": "", "start_s": 0.5,
                 "end_s": 4.0, "start_idx": 32, "end_idx": 256, "notes": ""},
                {"id": 2, "name": "TP-02", "label": "", "start_s": 8.5,
                 "end_s": 9.5, "start_idx": 544, "end_idx": 608, "notes": ""},
            ],
        })

    def tearDown(self):
        for p in reversed(self.patchers):
            p.stop()
        self.temp.cleanup()

    def meta(self):
        return store.get_meta("alpha")

    def test_rename_and_drop(self):
        edit._rebuild("alpha", {"rename": {"a": "thrust_n"}, "drop": ["b"]})
        m = self.meta()
        self.assertEqual(store.get_status("alpha")["status"], "ready")
        self.assertIn("thrust_n", m["columns"])
        self.assertNotIn("a", m["columns"])
        self.assertNotIn("b", m["columns"])
        # pyramid rebuilt with the new name; window read works end to end
        win = store.read_window("alpha", ["thrust_n"], None, None, px=300)
        self.assertGreater(len(win["t"]), 0)

    def test_trim_clips_testpoints(self):
        edit._rebuild("alpha", {"trim_t0": 2.0, "trim_t1": 8.0})
        m = self.meta()
        self.assertAlmostEqual(m["t_start"], 2.0, places=2)
        self.assertLess(abs(m["n_rows"] - 6 * FS), 3)
        tps = store.read_testpoints("alpha")["test_points"]
        self.assertEqual(len(tps), 1)  # TP-02 (8.5-9.5) fully outside
        self.assertAlmostEqual(tps[0]["start_s"], 2.0, places=6)
        self.assertAlmostEqual(tps[0]["end_s"], 4.0, places=6)
        self.assertEqual(tps[0]["start_idx"], 0)

    def test_zero_fill(self):
        edit._rebuild("alpha", {"nan_policy": "zero_fill"})
        m = self.meta()
        self.assertEqual(m["nan_counts"], {})
        self.assertEqual(m["nan_policy"], "zero_fill")
        df = pl.read_parquet(self.tests / "alpha" / "data.parquet")
        self.assertEqual(float(df["a"][105]), 0.0)

    def test_interpolate(self):
        edit._rebuild("alpha", {"nan_policy": "interpolate"})
        self.assertEqual(self.meta()["nan_counts"], {})
        df = pl.read_parquet(self.tests / "alpha" / "data.parquet")
        v = float(df["a"][105])
        lo = float(df["a"][99])
        hi = float(df["a"][110])
        self.assertTrue(min(lo, hi) - 1e-9 <= v <= max(lo, hi) + 1e-9)

    def test_endpoint_validation(self):
        bt = BackgroundTasks()
        with self.assertRaises(HTTPException):  # no ops
            main.api_edit("alpha", EditOps(), bt)
        with self.assertRaises(HTTPException):  # unknown column
            main.api_edit("alpha", EditOps(drop=["nope"]), bt)
        with self.assertRaises(HTTPException):  # time column protected
            main.api_edit("alpha", EditOps(rename={"time_s": "t"}), bt)
        with self.assertRaises(HTTPException):  # duplicate rename target
            main.api_edit("alpha", EditOps(rename={"a": "b"}), bt)
        with self.assertRaises(HTTPException):  # bad trim
            main.api_edit("alpha", EditOps(trim_t0=9.8, trim_t1=9.9), bt)
        result = main.api_edit("alpha", EditOps(drop=["b"]), bt)
        self.assertEqual(result["status"], "rebuilding")
        self.assertEqual(len(bt.tasks), 1)

    def test_patch_user_meta(self):
        out = main.api_patch_meta(
            "alpha",
            main.UserMetaPatch(user_meta={"prop": "22x10", "motor": "U8"}))
        self.assertEqual(out["user_meta"]["prop"], "22x10")
        self.assertEqual(self.meta()["user_meta"]["motor"], "U8")


if __name__ == "__main__":
    unittest.main()
