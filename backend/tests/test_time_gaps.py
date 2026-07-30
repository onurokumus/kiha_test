"""Missing-row timing and DSP boundary behavior."""

import math
from pathlib import Path

from app import dsp, edit, ingest, store
from ._base import DataDirTestCase


class TimeGapDspTests(DataDirTestCase):
    def setUp(self):
        super().setUp()
        source = Path(self.temp.name) / "gap-signal.csv"
        fs = 20.0
        lines = ["TIME,signal"]
        for index in range(80):
            if 30 <= index < 40:
                continue
            time_s = index / fs
            value = math.sin(2 * math.pi * 2.0 * time_s)
            lines.append(f"{time_s:.8f},{value:.12f}")
        source.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.meta = ingest.ingest_csv(
            source,
            "gap-signal",
            time_mode="column",
            time_column="TIME",
        )

    def test_filter_treats_inserted_rows_as_hard_boundaries(self):
        response = dsp.filtered_window(
            "gap-signal",
            ["signal"],
            "detrend",
            None,
            None,
            1000,
        )
        self.assertEqual(response["mode"], "raw")
        self.assertEqual(response["time_gap_count"], 1)
        self.assertFalse(response["gap_segment_warning"])
        self.assertEqual(response["nan_counts"], {"signal": 10})
        self.assertEqual(
            response["series"]["signal"][30:40],
            [None] * 10,
        )
        self.assertTrue(
            all(value is not None
                for value in response["series"]["signal"][:30])
        )
        self.assertTrue(
            all(value is not None
                for value in response["series"]["signal"][40:])
        )

    def test_spectrum_rejects_a_range_that_crosses_a_gap(self):
        with self.assertRaisesRegex(
                ValueError, "crosses 1 missing-data gap"):
            dsp.spectrum(
                "gap-signal", "signal", "fft", None, None)

    def test_spectrum_still_works_within_one_continuous_region(self):
        response = dsp.spectrum(
            "gap-signal", "signal", "fft", 0.0, 1.4)
        self.assertEqual(response["fs_hz"], 20.0)
        self.assertGreater(response["n_samples"], 16)
        self.assertTrue(response["freqs"])

    def test_trim_shifts_gap_ranges_to_the_new_row_origin(self):
        edit._rebuild(
            "gap-signal", {"trim_t0": 1.0, "trim_t1": 3.0})
        meta = store.get_meta("gap-signal")
        self.assertEqual(meta["time_gap_ranges"], [[10, 20]])
        self.assertEqual(meta["time_gap_count"], 1)
        self.assertEqual(meta["missing_rows_inserted"], 10)
        self.assertEqual(
            meta["source_n_rows"],
            meta["n_rows"] - meta["missing_rows_inserted"],
        )

    def test_explicit_fill_policy_clears_gap_boundaries(self):
        edit._rebuild("gap-signal", {"nan_policy": "zero_fill"})
        meta = store.get_meta("gap-signal")
        self.assertEqual(meta["time_gap_ranges"], [])
        self.assertEqual(meta["time_gap_count"], 0)
        self.assertEqual(meta["missing_rows_inserted"], 0)
        self.assertEqual(meta["nan_counts"], {})
