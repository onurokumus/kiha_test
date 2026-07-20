"""CSV dialect handling at ingest (semicolon / decimal comma / clock-time
TIME column / non-numeric columns) and the read-path robustness fixes that go
with it (inf serialization, time-column dedupe, xy point-budget bound)."""

import math
import unittest
from pathlib import Path

import numpy as np
import polars as pl
from fastapi.testclient import TestClient

from app import ingest, main, split, store
from ._base import DataDirTestCase


def _clock(sec: float) -> str:
    """MM:SS,s with a decimal comma, matching the KiHa export format."""
    minutes = int(sec // 60)
    return f"{minutes:02d}:{sec - minutes * 60:04.1f}".replace(".", ",")


def _dc(x: float) -> str:
    return f"{x:.5f}".replace(".", ",")


def write_kiha_csv(path: Path, n: int = 1200, fs: float = 100.0) -> None:
    """Semicolon-separated, decimal-comma, MM:SS,s clock time, with a
    non-numeric text column (must be skipped) and an integer Test_ID run
    column (must be kept and usable for auto-split)."""
    t0 = 19 * 60 + 39.2
    header = ["TIME", "Battery_Volt_L", "RPM_L", "Note_Text", "Test_ID"]
    lines = [";".join(header)]
    for i in range(n):
        sec = t0 + i / fs
        volt = 393.0 + math.sin(i / 50.0) * 2.0
        rpm = 0 if i < 200 else (1000 if i < 700 else 2000)
        note = "run_a" if i < 700 else "run_b"
        tid = 1 if i < 200 else (2 if i < 700 else 3)
        lines.append(";".join(
            [_clock(sec), _dc(volt), str(rpm), note, str(tid)]))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class IngestDialectTests(DataDirTestCase):
    def setUp(self):
        super().setUp()
        self.src = self.root / "kiha.csv"
        write_kiha_csv(self.src)

    def test_sniff_detects_semicolon_and_decimal_comma(self):
        self.assertEqual(ingest.sniff_dialect(self.src), (";", True))

    def test_plain_comma_csv_is_unaffected(self):
        plain = Path(self.temp.name) / "plain.csv"
        plain.write_text("time,rpm\n0.0,1.5\n0.1,2.5\n", encoding="utf-8")
        self.assertEqual(ingest.sniff_dialect(plain), (",", False))

    def test_dialect_file_ingests_to_ready_dataset(self):
        meta = ingest.ingest_csv(self.src, "kiha", source_name="kiha.csv")
        self.assertEqual(meta["csv_separator"], ";")
        self.assertTrue(meta["decimal_comma"])
        self.assertEqual(meta["n_rows"], 1200)
        # ~100 Hz derived from the total span despite 0.1 s clock quantization
        self.assertAlmostEqual(meta["fs_hz"], 100.0, delta=0.5)
        self.assertTrue(meta["time_quantized"])
        # coarse-but-uniform clock time is NOT flagged as jitter
        self.assertFalse(meta["jitter_warning"])
        # clock start 19:39,2 -> 1179.2 s
        self.assertAlmostEqual(meta["t_start"], 1179.2, places=3)

    def test_non_numeric_column_is_skipped_not_fatal(self):
        meta = ingest.ingest_csv(self.src, "kiha")
        self.assertIn("Note_Text", meta["skipped_columns"])
        self.assertNotIn("Note_Text", meta["columns"])
        # numeric columns (including the integer Test_ID) are kept
        for col in ("TIME", "Battery_Volt_L", "RPM_L", "Test_ID"):
            self.assertIn(col, meta["columns"])

    def test_decimal_comma_values_parse_as_floats(self):
        ingest.ingest_csv(self.src, "kiha")
        df = pl.read_parquet(self.tests / "kiha" / "data.parquet",
                             columns=["Battery_Volt_L"])
        self.assertEqual(df["Battery_Volt_L"].dtype, pl.Float64)
        self.assertAlmostEqual(df["Battery_Volt_L"][0], 393.0, places=4)

    def test_kept_integer_column_drives_autosplit(self):
        ingest.ingest_csv(self.src, "kiha")
        cands = {c["col"] for c in split.id_candidates("kiha")}
        self.assertIn("Test_ID", cands)
        self.assertNotIn("Note_Text", cands)  # skipped at ingest
        runs = split.autosplit("kiha", "Test_ID")
        self.assertEqual([(r["start_idx"], r["end_idx"]) for r in runs],
                         [(0, 200), (200, 700), (700, 1200)])

    def test_measured_time_column_reports_measured_source(self):
        meta = ingest.ingest_csv(self.src, "kiha")
        self.assertEqual(meta["time_source"], "measured")

    def test_repeated_timestamp_generates_a_uniform_axis(self):
        """The provided 2-row sample has one repeated timestamp: fs is
        underivable, so ingest generates a perfect axis instead of failing."""
        degenerate = Path(self.temp.name) / "degenerate.csv"
        degenerate.write_text(
            "TIME;RPM_L\n19:39,2;10\n19:39,2;11\n", encoding="utf-8")
        meta = ingest.ingest_csv(degenerate, "degenerate")
        self.assertEqual(store.get_status("degenerate")["status"], "ready")
        self.assertEqual(meta["time_source"], "generated")
        self.assertEqual(meta["fs_hz"], 2048.0)      # DEFAULT_FS_HZ
        self.assertFalse(meta["jitter_warning"])
        # the stored time column is a real uniform ramp starting at 0
        df = pl.read_parquet(self.tests / "degenerate" / "data.parquet")
        self.assertEqual(df["TIME"].to_list(), [0.0, 1.0 / 2048.0])
        self.assertEqual(df["RPM_L"].to_list(), [10.0, 11.0])

    def test_assume_fs_override_sets_the_generated_rate(self):
        degenerate = Path(self.temp.name) / "degenerate2.csv"
        degenerate.write_text(
            "TIME;RPM_L\n19:39,2;10\n19:39,2;11\n19:39,2;12\n", encoding="utf-8")
        meta = ingest.ingest_csv(degenerate, "degenerate2", assume_fs=500.0)
        self.assertEqual(meta["time_source"], "generated")
        self.assertEqual(meta["fs_hz"], 500.0)
        self.assertAlmostEqual(meta["duration_s"], 3 / 500.0, places=6)

    def test_non_numeric_time_column_falls_back_too(self):
        garbage = Path(self.temp.name) / "garbage.csv"
        garbage.write_text(
            "TIME;RPM_L\nfoo;10\nbar;11\nbaz;12\n", encoding="utf-8")
        meta = ingest.ingest_csv(garbage, "garbage", assume_fs=100.0)
        self.assertEqual(meta["time_source"], "generated")
        self.assertEqual(meta["fs_hz"], 100.0)


class AutoSplitRoundTripTests(DataDirTestCase):
    """An untouched auto-split proposal, saved back through the frontend's
    time->index recompute, must reproduce the exact same ranges (bug 1.10)."""

    def setUp(self):
        super().setUp()
        fixture = Path(__file__).parent / "fixtures" / "small.csv"  # fs=10, 20 rows
        self.meta = ingest.ingest_csv(fixture, "small")

    def test_end_s_round_trips_to_the_exclusive_end_idx(self):
        fs = self.meta["fs_hz"]
        t_start = self.meta["t_start"] or 0.0
        runs = split.autosplit("small", "tp_id", ignore_zero=False,
                               min_len_s=0.1)
        self.assertTrue(runs)
        for tp in runs:
            # exactly the recompute SplitView.save performs
            self.assertEqual(round((tp["start_s"] - t_start) * fs),
                             tp["start_idx"])
            self.assertEqual(round((tp["end_s"] - t_start) * fs),
                             tp["end_idx"])
        # the runs tile [0, n_rows) with no gap/overlap and keep the last sample
        self.assertEqual(runs[0]["start_idx"], 0)
        self.assertEqual(runs[-1]["end_idx"], self.meta["n_rows"])
        for earlier, later in zip(runs, runs[1:]):
            self.assertEqual(earlier["end_idx"], later["start_idx"])


class ReadRobustnessTests(DataDirTestCase):
    """Read endpoints must not 500 on inf cells or a time column passed via
    `cols`, and /xy must reject a zero point budget."""

    def setUp(self):
        super().setUp()
        directory = self.tests / "alpha"
        directory.mkdir()
        time = 100.0 + np.arange(10, dtype=np.float64) * 0.1
        rpm = 1000.0 + np.arange(10, dtype=np.float64)
        rpm[4] = np.inf     # bug 1.2: an infinity in the data
        rpm[7] = np.nan
        pl.DataFrame({"time": time, "rpm": rpm}).write_parquet(
            directory / "data.parquet", row_group_size=4)
        store.write_json_atomic(directory / "status.json", {"status": "ready"})
        store.write_json_atomic(directory / "meta.json", {
            "name": "alpha", "fs_hz": 10.0, "n_rows": 10,
            "columns": ["time", "rpm"], "time_column": "time",
            "t_start": 100.0,
        })
        self.client = TestClient(main.app)

    def test_inf_cell_serializes_as_null_not_500(self):
        r = self.client.get("/api/tests/alpha/data?cols=rpm")
        self.assertEqual(r.status_code, 200)
        vals = r.json()["series"]["rpm"]
        self.assertIsNone(vals[4])   # inf -> None
        self.assertIsNone(vals[7])   # nan -> None
        self.assertTrue(all(v is None or math.isfinite(v) for v in vals))

    def test_data_dedupes_and_drops_the_time_column(self):
        r = self.client.get("/api/tests/alpha/data?cols=time,rpm,rpm")
        self.assertEqual(r.status_code, 200)
        # time is returned as `t`, never as a series
        self.assertEqual(list(r.json()["series"].keys()), ["rpm"])

    def test_data_with_only_the_time_column_is_400(self):
        r = self.client.get("/api/tests/alpha/data?cols=time")
        self.assertEqual(r.status_code, 400)

    def test_xy_rejects_a_zero_point_budget(self):
        r = self.client.get(
            "/api/tests/alpha/xy?x=time&y=rpm&max_pts=0")
        self.assertEqual(r.status_code, 422)  # Query(ge=4) bound

    def test_filter_dedupes_the_time_column(self):
        r = self.client.get(
            "/api/tests/alpha/data?cols=rpm&t0=100.0&t1=100.3")
        self.assertEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main()
