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

