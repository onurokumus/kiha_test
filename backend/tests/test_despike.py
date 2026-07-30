"""Robust short-run despike filtering and its API contract."""

import unittest

import numpy as np
import polars as pl
from fastapi.testclient import TestClient

from app import dsp, main, store
from ._base import DataDirTestCase


class DespikeFilterTests(DataDirTestCase):
    def setUp(self):
        super().setUp()
        self.client = TestClient(main.app)
        self.fs = 1000.0
        self.n = 240

        directory = self.tests / "spikes"
        directory.mkdir()
        time = np.arange(self.n, dtype=np.float64) / self.fs

        trend = np.arange(self.n, dtype=np.float64) * 0.1
        trend[50] = np.nan
        trend[80:85] += 300.0       # five-sample glitch: replace
        trend[120:140] += 300.0     # 20-sample event: preserve

        flat = np.zeros(self.n, dtype=np.float64)
        flat[30:34] = 300.0         # MAD is exactly zero around this pulse
        flat[60] = 5.0              # below the configured absolute floor

        edge = np.zeros(self.n, dtype=np.float64)
        edge[:4] = 300.0
        edge[-4:] = 300.0

        pl.DataFrame({
            "time": time,
            "trend": trend,
            "flat": flat,
            "edge": edge,
        }).write_parquet(directory / "data.parquet")
        store.write_json_atomic(directory / "meta.json", {
            "name": "spikes",
            "fs_hz": self.fs,
            "n_rows": self.n,
            "columns": ["time", "trend", "flat", "edge"],
            "time_column": "time",
            "t_start": 0.0,
            "time_gap_ranges": [],
        })

    def filter(self, column: str, **overrides):
        params = {
            "cols": column,
            "type": "despike",
            "px": 1000,
            "window_s": 0.025,
            "max_spike_s": 0.010,
            "threshold": 3.5,
            "abs_floor": 10.0,
            **overrides,
        }
        return self.client.get(
            "/api/tests/spikes/filter", params=params)

    def test_multi_sample_run_is_replaced_but_long_event_is_preserved(self):
        response = self.filter("trend")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        values = body["series"]["trend"]

        np.testing.assert_allclose(
            values[80:85], np.arange(80, 85) * 0.1)
        np.testing.assert_allclose(
            values[120:140], np.arange(120, 140) * 0.1 + 300.0)
        self.assertIsNone(values[50])
        self.assertEqual(body["replacement_counts"], {"trend": 5})
        self.assertEqual(body["spike_event_counts"], {"trend": 1})

    def test_absolute_floor_handles_flat_mad_without_removing_small_change(self):
        response = self.filter("flat")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        values = body["series"]["flat"]

        self.assertEqual(values[30:34], [0.0, 0.0, 0.0, 0.0])
        self.assertEqual(values[60], 5.0)
        self.assertEqual(body["replacement_counts"], {"flat": 4})
        self.assertEqual(body["spike_event_counts"], {"flat": 1})

    def test_time_parameters_resolve_to_an_odd_majority_clean_window(self):
        params = dsp._despike_sample_parameters(
            2048.0, 0.025, 0.010, 3.5, 0.0)
        self.assertEqual(params[:2], (51, 20))
        self.assertEqual(params[0] % 2, 1)
        self.assertGreater(params[0], 2 * params[1])

    def test_short_runs_at_selected_range_edges_use_local_median_fallback(self):
        response = self.filter("edge")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        values = body["series"]["edge"]

        self.assertEqual(values[:4], [0.0] * 4)
        self.assertEqual(values[-4:], [0.0] * 4)
        self.assertEqual(body["replacement_counts"], {"edge": 8})
        self.assertEqual(body["spike_event_counts"], {"edge": 2})

    def test_invalid_despike_parameters_return_clear_client_errors(self):
        cases = (
            ({"threshold": 0}, "threshold"),
            ({"abs_floor": -1}, "abs_floor"),
            ({"max_spike_s": 0}, "max_spike_s"),
            (
                {"window_s": 0.015, "max_spike_s": 0.010},
                "more than twice",
            ),
        )
        for overrides, message in cases:
            with self.subTest(overrides=overrides):
                response = self.filter("flat", **overrides)
                self.assertEqual(response.status_code, 400, response.text)
                self.assertIn(message, response.json()["detail"])

    def test_nan_gap_is_preserved_and_replacements_stay_on_each_side(self):
        directory = self.tests / "gap-spikes"
        directory.mkdir()
        n = 200
        time = np.arange(n, dtype=np.float64) / self.fs
        values = np.zeros(n, dtype=np.float64)
        values[90:110] = np.nan
        values[110:] = 100.0
        values[75:79] = 300.0
        values[121:125] = 400.0
        pl.DataFrame({"time": time, "signal": values}).write_parquet(
            directory / "data.parquet")
        store.write_json_atomic(directory / "meta.json", {
            "name": "gap-spikes",
            "fs_hz": self.fs,
            "n_rows": n,
            "columns": ["time", "signal"],
            "time_column": "time",
            "t_start": 0.0,
            "time_gap_ranges": [[90, 110]],
        })

        response = self.client.get(
            "/api/tests/gap-spikes/filter",
            params={
                "cols": "signal",
                "type": "despike",
                "window_s": 0.025,
                "max_spike_s": 0.010,
                "threshold": 3.5,
                "abs_floor": 10.0,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        filtered = body["series"]["signal"]

        self.assertEqual(filtered[75:79], [0.0] * 4)
        self.assertEqual(filtered[90:110], [None] * 20)
        self.assertEqual(filtered[121:125], [100.0] * 4)
        self.assertEqual(body["replacement_counts"], {"signal": 8})
        self.assertEqual(body["spike_event_counts"], {"signal": 2})
        self.assertEqual(body["time_gap_count"], 1)


if __name__ == "__main__":
    unittest.main()
