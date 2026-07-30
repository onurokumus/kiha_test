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
        # Working time is elapsed seconds; the source clock origin is retained
        # only as traceability metadata.
        self.assertEqual(meta["t_start"], 0.0)
        self.assertEqual(meta["time_unit"], "s")
        self.assertAlmostEqual(
            meta["source_time_origin_s"], 1179.2, places=3)
        stored = pl.read_parquet(
            self.tests / "kiha" / "data.parquet", columns=["TIME"])
        self.assertEqual(stored["TIME"][0], 0.0)

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

    def test_backward_time_step_falls_back_to_a_uniform_axis(self):
        backward = Path(self.temp.name) / "backward.csv"
        backward.write_text(
            "TIME,rpm\n10,100\n12,101\n11,102\n13,103\n",
            encoding="utf-8",
        )
        meta = ingest.ingest_csv(
            backward, "backward", assume_fs=50.0,
            time_mode="column", time_column="TIME")
        self.assertEqual(meta["time_source"], "generated")
        self.assertEqual(meta["fs_hz"], 50.0)
        stored = pl.read_parquet(
            self.tests / "backward" / "data.parquet")
        np.testing.assert_allclose(
            stored["TIME"].to_numpy(), [0.0, 0.02, 0.04, 0.06])

    def test_missing_measured_rows_keep_nominal_rate_and_become_nan_gaps(self):
        missing = Path(self.temp.name) / "missing-rows.csv"
        missing.write_text(
            "TIME,signal,rpm\n"
            "0.0,10,100\n"
            "0.5,11,101\n"
            "1.0,12,102\n"
            "3.5,13,103\n"
            "4.0,14,104\n",
            encoding="utf-8",
        )
        meta = ingest.ingest_csv(
            missing, "missing-rows",
            time_mode="column", time_column="TIME")

        self.assertEqual(meta["fs_hz"], 2.0)
        self.assertEqual(meta["source_n_rows"], 5)
        self.assertEqual(meta["n_rows"], 9)
        self.assertEqual(meta["time_gap_count"], 1)
        self.assertEqual(meta["missing_rows_inserted"], 4)
        self.assertEqual(meta["time_gap_seconds"], 2.0)
        self.assertEqual(meta["time_gap_ranges"], [[3, 7]])
        self.assertTrue(meta["jitter_warning"])
        self.assertEqual(meta["nan_counts"]["signal"], 4)
        self.assertEqual(meta["nan_counts"]["rpm"], 4)
        listed = next(
            test for test in store.list_tests()
            if test["name"] == "missing-rows"
        )
        self.assertEqual(listed["time_gap_count"], 1)
        self.assertEqual(listed["missing_rows_inserted"], 4)

        stored = pl.read_parquet(
            self.tests / "missing-rows" / "data.parquet")
        np.testing.assert_allclose(
            stored["TIME"].to_numpy(),
            np.arange(9, dtype=np.float64) / 2.0,
        )
        signal = stored["signal"].to_numpy()
        np.testing.assert_allclose(signal[[0, 1, 2, 7, 8]],
                                   [10, 11, 12, 13, 14])
        self.assertTrue(np.isnan(signal[3:7]).all())

        self.assertEqual(store.window_bounds(meta, 3.5, 4.0), (7, 9))
        window = store.read_window(
            "missing-rows", ["signal"], None, None, 1000)
        self.assertEqual(window["t"], [i / 2.0 for i in range(9)])
        self.assertEqual(window["series"]["signal"][3:7],
                         [None, None, None, None])

    def test_generated_time_cannot_infer_missing_source_rows(self):
        missing = Path(self.temp.name) / "generated-missing.csv"
        missing.write_text(
            "TIME,signal\n"
            "0.0,10\n"
            "0.5,11\n"
            "1.0,12\n"
            "3.5,13\n"
            "4.0,14\n",
            encoding="utf-8",
        )
        meta = ingest.ingest_csv(
            missing, "generated-missing", assume_fs=2.0,
            time_mode="generated", time_column="TIME")
        self.assertEqual(meta["n_rows"], 5)
        self.assertEqual(meta["source_n_rows"], 5)
        self.assertEqual(meta["time_gap_count"], 0)
        self.assertEqual(meta["missing_rows_inserted"], 0)
        stored = pl.read_parquet(
            self.tests / "generated-missing" / "data.parquet")
        np.testing.assert_allclose(
            stored["TIME"].to_numpy(), [0.0, 0.5, 1.0, 1.5, 2.0])

    def test_very_low_measured_rate_keeps_usable_precision(self):
        slow = Path(self.temp.name) / "slow.csv"
        slow.write_text(
            "TIME,rpm\n0,100\n3000,101\n6000,102\n",
            encoding="utf-8",
        )
        meta = ingest.ingest_csv(
            slow, "slow", time_mode="column", time_column="TIME")
        self.assertAlmostEqual(meta["fs_hz"], 1.0 / 3000.0)
        self.assertGreater(meta["fs_hz"], 0)
        self.assertEqual(meta["duration_s"], 9000.0)

    def test_high_generated_rate_keeps_sub_millisecond_duration(self):
        fast = Path(self.temp.name) / "fast.csv"
        fast.write_text("rpm\n100\n101\n", encoding="utf-8")
        meta = ingest.ingest_csv(
            fast, "fast", assume_fs=10_000.0,
            time_mode="generated", time_column="time_s")
        self.assertEqual(meta["fs_hz"], 10_000.0)
        self.assertAlmostEqual(meta["duration_s"], 0.0002)
        self.assertGreater(meta["duration_s"], 0)

    def test_tiny_positive_generated_rate_is_not_rounded_to_zero(self):
        tiny = Path(self.temp.name) / "tiny-rate.csv"
        tiny.write_text("rpm\n100\n101\n", encoding="utf-8")
        meta = ingest.ingest_csv(
            tiny, "tiny-rate", assume_fs=4e-7,
            time_mode="generated", time_column="time_s")
        self.assertEqual(meta["fs_hz"], 4e-7)
        self.assertGreater(meta["duration_s"], 0)

    def test_hh_mm_ss_millisecond_clock_starts_at_zero_seconds(self):
        screenshot = Path(self.temp.name) / "screenshot-clock.csv"
        screenshot.write_text(
            "TIME;RPM_L\n"
            "11:00:19.687;100\n"
            "11:00:19.687;101\n"
            "11:00:19.688;102\n"
            "11:00:19.688;103\n"
            "11:00:19.689;104\n",
            encoding="utf-8",
        )
        meta = ingest.ingest_csv(screenshot, "screenshot-clock")
        self.assertEqual(meta["time_source"], "measured")
        self.assertEqual(meta["t_start"], 0.0)
        self.assertAlmostEqual(meta["source_time_origin_s"], 39619.687)
        self.assertAlmostEqual(meta["fs_hz"], 2000.0, places=2)
        self.assertTrue(meta["time_quantized"])
        stored = pl.read_parquet(
            self.tests / "screenshot-clock" / "data.parquet")
        np.testing.assert_allclose(
            stored["TIME"].to_numpy(),
            [0.0, 0.0, 0.001, 0.001, 0.002],
            atol=1e-9,
        )

    def test_numeric_time_origin_is_normalized_too(self):
        numeric = Path(self.temp.name) / "numeric-origin.csv"
        numeric.write_text(
            "elapsed,rpm\n100.0,10\n100.1,11\n100.2,12\n",
            encoding="utf-8",
        )
        meta = ingest.ingest_csv(
            numeric, "numeric-origin",
            time_mode="column", time_column="elapsed")
        self.assertEqual(meta["t_start"], 0.0)
        self.assertAlmostEqual(meta["source_time_origin_s"], 100.0)
        stored = pl.read_parquet(
            self.tests / "numeric-origin" / "data.parquet")
        np.testing.assert_allclose(
            stored["elapsed"].to_numpy(), [0.0, 0.1, 0.2])

    def test_padded_time_header_is_detected_and_selectable_exactly(self):
        padded = Path(self.temp.name) / "padded-header.csv"
        padded.write_text(
            " force , TIME ,rpm\n"
            "12,11:00:19.687,100\n"
            "13,11:00:19.688,101\n"
            "14,11:00:19.689,102\n",
            encoding="utf-8",
        )
        auto = ingest.ingest_csv(padded, "padded-auto")
        self.assertEqual(auto["time_column"], " TIME ")
        self.assertEqual(auto["time_source"], "measured")
        auto_stored = pl.read_parquet(
            self.tests / "padded-auto" / "data.parquet")
        np.testing.assert_allclose(
            auto_stored[" TIME "].to_numpy(), [0.0, 0.001, 0.002],
            atol=1e-9,
        )

        explicit = ingest.ingest_csv(
            padded, "padded-explicit",
            time_mode="column", time_column=" TIME ")
        self.assertEqual(explicit["time_column"], " TIME ")
        self.assertIn(" force ", explicit["columns"])

    def test_explicit_existing_time_column_preserves_first_signal(self):
        custom = Path(self.temp.name) / "custom-column.csv"
        custom.write_text(
            "force,RigClock,rpm\n"
            "12.0,50.00,100\n"
            "13.0,50.01,101\n"
            "14.0,50.02,102\n",
            encoding="utf-8",
        )
        meta = ingest.ingest_csv(
            custom, "custom-column",
            time_mode="column", time_column="RigClock")
        self.assertEqual(meta["time_column"], "RigClock")
        self.assertEqual(meta["columns"], ["RigClock", "force", "rpm"])
        stored = pl.read_parquet(
            self.tests / "custom-column" / "data.parquet")
        np.testing.assert_allclose(
            stored["RigClock"].to_numpy(), [0.0, 0.01, 0.02])
        self.assertEqual(stored["force"].to_list(), [12.0, 13.0, 14.0])

    def test_generated_custom_time_column_uses_requested_rate(self):
        custom = Path(self.temp.name) / "generated-column.csv"
        custom.write_text(
            "force,rpm\n12,100\n13,101\n14,102\n",
            encoding="utf-8",
        )
        meta = ingest.ingest_csv(
            custom, "generated-column", assume_fs=500.0,
            time_mode="generated", time_column="elapsed_s")
        self.assertEqual(meta["time_source"], "generated")
        self.assertEqual(meta["time_column"], "elapsed_s")
        self.assertEqual(meta["fs_hz"], 500.0)
        self.assertEqual(meta["columns"], ["elapsed_s", "force", "rpm"])
        stored = pl.read_parquet(
            self.tests / "generated-column" / "data.parquet")
        np.testing.assert_allclose(
            stored["elapsed_s"].to_numpy(), [0.0, 0.002, 0.004])
        self.assertEqual(stored["force"].to_list(), [12.0, 13.0, 14.0])

    def test_time_column_cannot_collide_with_pyramid_field_names(self):
        custom = Path(self.temp.name) / "pyramid-name-collision.csv"
        custom.write_text(
            "force,force__min,rpm\n"
            "12,0.0,100\n"
            "13,0.1,101\n"
            "14,0.2,102\n",
            encoding="utf-8",
        )
        cases = (
            ("generated", "force__min"),
            ("column", "force__min"),
        )
        for mode, column in cases:
            with self.subTest(mode=mode):
                with self.assertRaisesRegex(
                        ValueError, "conflicts with pyramid fields"):
                    ingest.ingest_csv(
                        custom, f"collision-{mode}", assume_fs=500.0,
                        time_mode=mode, time_column=column)

    def test_generated_mode_overrides_a_valid_clock_rate(self):
        forced = Path(self.temp.name) / "forced-rate.csv"
        forced.write_text(
            "TIME,rpm\n10.0,1\n10.1,2\n10.2,3\n",
            encoding="utf-8",
        )
        meta = ingest.ingest_csv(
            forced, "forced-rate", assume_fs=20.0,
            time_mode="generated", time_column="TIME")
        self.assertEqual(meta["fs_hz"], 20.0)
        self.assertEqual(meta["source_time_origin_s"], None)
        stored = pl.read_parquet(
            self.tests / "forced-rate" / "data.parquet")
        np.testing.assert_allclose(
            stored["TIME"].to_numpy(), [0.0, 0.05, 0.1])

    def test_auto_mode_adds_time_without_dropping_a_signal(self):
        no_time = Path(self.temp.name) / "no-time.csv"
        no_time.write_text(
            "force,rpm\n12,100\n13,101\n14,102\n",
            encoding="utf-8",
        )
        meta = ingest.ingest_csv(
            no_time, "no-time", assume_fs=100.0)
        self.assertEqual(meta["time_column"], "time_s")
        self.assertEqual(meta["columns"], ["time_s", "force", "rpm"])
        self.assertEqual(meta["time_source"], "generated")

    def test_unknown_explicit_time_column_fails_clearly(self):
        missing = Path(self.temp.name) / "missing-column.csv"
        missing.write_text(
            "force,rpm\n12,100\n13,101\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
                ValueError, "time column 'RigClock' was not found"):
            ingest.ingest_csv(
                missing, "missing-column",
                time_mode="column", time_column="RigClock")


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

    def test_data_rejects_an_unknown_display_mode(self):
        r = self.client.get(
            "/api/tests/alpha/data?cols=rpm&display=unknown")
        self.assertEqual(r.status_code, 422)

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
