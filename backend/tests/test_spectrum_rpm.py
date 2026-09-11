"""RPM reference-speed metadata for per-revolution spectrum axes."""

import math

import numpy as np
import polars as pl
from fastapi.testclient import TestClient

from app import dsp, main, store
from ._base import DataDirTestCase


class SpectrumRpmTests(DataDirTestCase):
    def setUp(self):
        super().setUp()
        self.client = TestClient(main.app)
        self.fs = 100.0
        self.n = 200

        directory = self.tests / "rpm-spectrum"
        directory.mkdir()
        time = np.arange(self.n, dtype=np.float64) / self.fs
        rpm = np.linspace(1200.0, 1800.0, self.n)
        signal_values = np.sin(2 * math.pi * 25.0 * time)
        pl.DataFrame({
            "time": time,
            "signal": signal_values,
            "rpm": rpm,
        }).write_parquet(directory / "data.parquet")
        store.write_json_atomic(directory / "meta.json", {
            "name": "rpm-spectrum",
            "fs_hz": self.fs,
            "n_rows": self.n,
            "columns": ["time", "signal", "rpm"],
            "time_column": "time",
            "t_start": 0.0,
            "time_gap_ranges": [],
        })

    def test_spectrum_reports_mean_rpm_for_the_exact_window(self):
        response = dsp.spectrum(
            "rpm-spectrum", "signal", "fft", 0.5, 1.49, rpm_col="rpm")
        expected = np.linspace(1200.0, 1800.0, self.n)[50:150]

        self.assertEqual(response["rpm_col"], "rpm")
        self.assertAlmostEqual(response["mean_rpm"], float(np.mean(expected)))
        self.assertAlmostEqual(response["min_rpm"], float(np.min(expected)))
        self.assertAlmostEqual(response["max_rpm"], float(np.max(expected)))
        self.assertTrue(response["freqs"])

    def test_spectrum_api_rejects_an_unknown_rpm_column(self):
        response = self.client.get(
            "/api/tests/rpm-spectrum/spectrum",
            params={"col": "signal", "rpm_col": "missing"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("unknown RPM column", response.json()["detail"])

    def test_saved_tp_rpm_uses_identical_rows_and_preserves_per_hz_density(self):
        directory = self.tests / "rpm-spectrum"
        frame = pl.read_parquet(directory / "data.parquet")
        rpm = np.full(self.n, 100_000.0)
        rpm[50:150] = np.tile([1200.0, -1800.0, 0.0, np.nan, np.inf], 20)
        frame.with_columns(pl.Series("rpm", rpm)).write_parquet(
            directory / "data.parquet")
        store.write_json_atomic(directory / "testpoints.json", {
            "test_points": [{"id": 8, "start_idx": 50, "end_idx": 150,
                             "start_s": 0.6, "end_s": 1.6}],
        })
        for mode in ("fft", "welch"):
            with self.subTest(mode=mode):
                plain = dsp.spectrum("rpm-spectrum", "signal", mode, None, None,
                                     tp_id=8)
                result = dsp.spectrum("rpm-spectrum", "signal", mode, None, None,
                                      rpm_col="rpm", tp_id=8)
                self.assertEqual((result["i0"], result["i1"]), (50, 150))
                self.assertEqual(result["mean_rpm"], 1000.0)
                self.assertEqual(result["min_rpm"], 0.0)
                self.assertEqual(result["max_rpm"], 1800.0)
                self.assertEqual(result["rpm_finite_count"], 60)
                self.assertEqual(result["rpm_nan_count"], 40)
                self.assertEqual(result["freqs"], plain["freqs"])
                self.assertEqual(result["mag"], plain["mag"])
                self.assertEqual(result["method"]["units"], "U²/Hz" if mode == "welch" else "U")

