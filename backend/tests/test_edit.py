import json
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import polars as pl
from fastapi import BackgroundTasks, HTTPException

from app import edit, ingest, main, store
from app.main import EditOps
from ._base import DataDirTestCase

FS = 64.0
N = 640  # 10 s


class EditTests(DataDirTestCase):
    def setUp(self):
        super().setUp()

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

    def test_failed_rebuild_leaves_the_original_test_intact(self):
        # A crash while building the new pyramid must not touch the live
        # data/meta/pyramid — everything expensive is staged first.
        before_meta = self.meta()
        before_parquet = (self.tests / "alpha" / "data.parquet").read_bytes()
        with patch.object(edit, "build_pyramid",
                          side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                edit._rebuild("alpha", {"drop": ["b"]})

        self.assertEqual(store.get_status("alpha")["status"], "error")
        self.assertEqual((self.tests / "alpha" / "data.parquet").read_bytes(),
                         before_parquet)
        self.assertEqual(self.meta()["columns"], before_meta["columns"])
        # no staging leftovers
        self.assertFalse((self.tests / "alpha" / "data.parquet.tmp").exists())
        self.assertFalse((self.tests / "alpha" / "pyramid.tmp").exists())
        self.assertFalse((self.tests / "alpha" / "pyramid.old").exists())
        # the untouched test still serves end to end
        win = store.read_window("alpha", ["a", "b"], None, None, px=100)
        self.assertGreater(len(win["t"]), 0)

    def test_edit_invalidates_tp_stats(self):
        before = store.tp_stats("alpha", "a")  # 2 TPs -> populates sidecar
        self.assertTrue((self.tests / "alpha" / "tp_stats.json").is_file())
        edit._rebuild("alpha", {"trim_t0": 2.0, "trim_t1": 8.0})
        # trim drops TP-02 (8.5-9.5); the stale sidecar must not survive
        after = store.tp_stats("alpha", "a")
        self.assertEqual(len(before), 2)
        self.assertEqual(len(after), 1)

    def test_second_edit_is_rejected_once_rebuilding(self):
        # First /edit flips the status to 'rebuilding' before returning
        # (background task not yet run); a second request must 409 instead
        # of scheduling ops validated against the pre-rebuild schema.
        main.api_edit("alpha", EditOps(drop=["b"]), BackgroundTasks())
        self.assertEqual(store.get_status("alpha")["status"], "rebuilding")
        with self.assertRaises(HTTPException) as caught:
            main.api_edit("alpha", EditOps(drop=["b"]), BackgroundTasks())
        self.assertEqual(caught.exception.status_code, 409)

    def test_patch_user_meta(self):
        out = main.api_patch_meta(
            "alpha",
            main.UserMetaPatch(user_meta={"prop": "22x10", "motor": "U8"}))
        self.assertEqual(out["user_meta"]["prop"], "22x10")
        self.assertEqual(self.meta()["user_meta"]["motor"], "U8")


if __name__ == "__main__":
    unittest.main()
