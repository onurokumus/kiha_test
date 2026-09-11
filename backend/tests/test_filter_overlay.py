"""Original/filtered overlays share a saved TP's rows and time origin."""

import hashlib

import numpy as np
import polars as pl
from fastapi.testclient import TestClient

from app import main, store
from ._base import DataDirTestCase


class FilterOverlayTests(DataDirTestCase):
    def setUp(self):
        super().setUp()
        self.client = TestClient(main.app)
        self.directory = self.tests / "overlay"
        self.directory.mkdir()
        self.time = 50.0 + np.arange(240, dtype=np.float64) / 1000.0
        self.values = np.full(240, 10_000.0)
        self.values[35:187] = 20.0
        self.values[70:74] = 400.0
        self.values[100] = np.nan
        self.meta = {
            "name": "overlay", "fs_hz": 1000.0, "n_rows": 240,
            "columns": ["time", "signal"], "time_column": "time",
            "t_start": 50.0, "time_gap_ranges": [],
        }
        # Deliberately inconsistent legacy display times: stored indices win.
        self.points = [{"id": 7, "start_idx": 35, "end_idx": 187,
                        "start_s": 50.037, "end_s": 50.190}]
        self.write_fixture()
        (self.directory / "raw.csv").write_text("original upload\n")

    def write_fixture(self):
        pl.DataFrame({"time": self.time, "signal": self.values}).write_parquet(
            self.directory / "data.parquet")
        store.write_json_atomic(self.directory / "meta.json", self.meta)
        store.write_json_atomic(self.directory / "status.json",
                                {"status": "ready"})
        store.write_json_atomic(self.directory / "testpoints.json", {
            "test_points": self.points,
        })

    def request(self, **overrides):
        return self.client.get("/api/tests/overlay/filter", params={
            "cols": "signal", "type": "despike", "tp_id": 7,
            "window_s": 0.025, "max_spike_s": 0.010,
            "abs_floor": 10.0, **overrides,
        })

    def result(self, **overrides):
        response = self.request(**overrides)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_saved_rows_and_actual_origin_match_original_trace(self):
        original = store.read_testpoint_trace("overlay", 7, ["signal"])
        filtered = self.result()
        self.assertEqual((filtered["i0"], filtered["i1"]), (35, 187))
        self.assertEqual(filtered["n_raw"], 152)
        self.assertEqual(filtered["tp_id"], 7)
        self.assertEqual(filtered["time_origin_s"], original["time_origin_s"])
        self.assertAlmostEqual(filtered["time_origin_s"], 50.035)
        np.testing.assert_allclose(
            np.array(filtered["t"]) - filtered["time_origin_s"],
            original["series"]["signal"]["t"], atol=1e-6)
        self.assertEqual(filtered["relative_t"],
                         original["series"]["signal"]["t"])
        self.assertEqual(filtered["t"][-1], 50.186)
        self.assertEqual(filtered["series"]["signal"][35:39], [20.0] * 4)
        self.assertIsNone(filtered["series"]["signal"][65])
        self.assertEqual(filtered["replacement_counts"], {"signal": 4})
        self.assertEqual(filtered["spike_event_counts"], {"signal": 1})

    def test_tp_envelope_is_local_and_excludes_adjacent_rows(self):
        result = self.result(display="envelope")
        self.assertEqual(result["mode"], "envelope")
        self.assertEqual(result["level"], 16)
        self.assertEqual(result["t"][0], 50.035)
        self.assertEqual(result["t"][-1], 50.179)
        self.assertEqual(result["relative_t"],
                         [round(i * 0.016, 6) for i in range(10)])
        self.assertEqual(result["series"]["signal"]["min"], [20.0] * 10)
        self.assertEqual(result["series"]["signal"]["max"], [20.0] * 10)
        self.assertEqual(result["replacement_counts"], {"signal": 4})
        # No global pyramid exists in this fixture. TP envelopes require only
        # the exact source rows, so bucket alignment cannot reach a neighbor.
        self.assertFalse((self.directory / "pyramid").exists())

    def test_relative_time_is_computed_before_absolute_serialization(self):
        # A sub-microsecond origin rounds up in absolute t. Relative t must
        # still begin at exact zero, like the original test-point endpoint.
        self.time += 0.0000007
        self.write_fixture()
        original = store.read_testpoint_trace("overlay", 7, ["signal"])
        for display in ("line", "envelope"):
            with self.subTest(display=display):
                result = self.result(display=display)
                self.assertEqual(result["relative_t"][0], 0.0)
                self.assertEqual(result["time_origin_s"], self.time[35])
                self.assertAlmostEqual(result["time_origin_s"],
                                       original["time_origin_s"], places=6)
                if display == "line":
                    self.assertEqual(result["relative_t"],
                                     original["series"]["signal"]["t"])

    def test_filter_boundary_context_does_not_leak_from_adjacent_tps(self):
        for display in ("line", "envelope"):
            with self.subTest(display=display):
                result = self.result(type="moving_avg", window_s=0.005,
                                     display=display)
                series = result["series"]["signal"]
                if result["mode"] == "envelope":
                    self.assertEqual(series["min"][0], 20.0)
                    self.assertEqual(series["max"][-1], 20.0)
                else:
                    self.assertEqual(series[0], 20.0)
                    self.assertEqual(series[-1], 20.0)

    def test_long_tp_auto_envelope_and_strided_line_filter_before_reduction(self):
        self.time = 50.0 + np.arange(20_040, dtype=np.float64) / 1000.0
        self.values = np.full(20_040, 10_000.0)
        self.values[35:20_021] = 20.0
        self.values[70:74] = 400.0
        self.meta["n_rows"] = len(self.time)
        self.points[0]["end_idx"] = 20_021
        self.write_fixture()
        for display in ("auto", "line"):
            with self.subTest(display=display):
                result = self.result(display=display, px=100)
                self.assertEqual((result["i0"], result["i1"]), (35, 20_021))
                self.assertEqual(result["n_raw"], 19_986)
                self.assertEqual(result["replacement_counts"], {"signal": 4})
                self.assertEqual(result["relative_t"][0], 0.0)
                self.assertLess(result["t"][-1], self.time[20_021])
                if display == "auto":
                    self.assertEqual(result["mode"], "envelope")
                    self.assertLessEqual(len(result["t"]), 1000)
                    self.assertEqual(set(result["series"]["signal"]["min"]), {20.0})
                    self.assertEqual(set(result["series"]["signal"]["max"]), {20.0})
                else:
                    self.assertEqual(result["mode"], "raw")
                    self.assertGreater(result["level"], 1)
                    self.assertLessEqual(len(result["t"]), 8000)
                    self.assertEqual(set(result["series"]["signal"]), {20.0})

    def test_legacy_times_and_open_ends_use_shared_tp_resolution(self):
        cases = [
            ([{"id": 7, "start_s": 50.035, "end_s": 50.187}], (35, 187)),
            ([{"id": 7, "start_idx": 35, "start_s": 50.035},
              {"id": 8, "start_idx": 187, "start_s": 50.187}], (35, 187)),
            ([{"id": 7, "start_idx": 187, "start_s": 50.187}], (187, 240)),
        ]
        for points, expected in cases:
            with self.subTest(points=points):
                self.points = points
                self.write_fixture()
                result = self.result()
                self.assertEqual((result["i0"], result["i1"]), expected)
                self.assertEqual(result["n_raw"], expected[1] - expected[0])
                self.assertEqual(result["time_origin_s"], self.time[expected[0]])

    def test_known_gaps_remain_hard_boundaries_inside_saved_tp(self):
        self.values[35:100] = 0.0
        self.values[100:120] = np.nan
        self.values[120:187] = 100.0
        self.values[75:79] = 300.0
        self.values[130:134] = 400.0
        self.meta["time_gap_ranges"] = [[100, 120]]
        self.write_fixture()
        result = self.result()
        values = result["series"]["signal"]
        self.assertEqual(values[:65], [0.0] * 65)
        self.assertEqual(values[65:85], [None] * 20)
        self.assertEqual(values[85:], [100.0] * 67)
        self.assertEqual(result["replacement_counts"], {"signal": 8})
        self.assertEqual(result["spike_event_counts"], {"signal": 2})
        self.assertEqual(result["time_gap_count"], 1)
        self.assertFalse(result["gap_segment_warning"])

    def test_short_gap_segments_are_missing_with_warning_or_clear_error(self):
        self.values[35:187] = 20.0
        self.values[50:65] = np.nan
        self.meta["time_gap_ranges"] = [[50, 65]]
        self.write_fixture()
        result = self.result()
        self.assertEqual(result["series"]["signal"][:30], [None] * 30)
        self.assertEqual(result["series"]["signal"][30:], [20.0] * 122)
        self.assertTrue(result["gap_segment_warning"])
        self.values[50:175] = np.nan
        self.meta["time_gap_ranges"] = [[50, 175]]
        self.write_fixture()
        response = self.request()
        self.assertEqual(response.status_code, 400)
        self.assertIn("continuous regions", response.json()["detail"])

    def test_non_finite_samples_are_restored_and_not_counted_as_repairs(self):
        self.values[90:93] = [np.nan, np.inf, -np.inf]
        self.write_fixture()
        result = self.result()
        self.assertEqual(result["series"]["signal"][55:58], [None] * 3)
        self.assertEqual(result["nan_counts"], {"signal": 4})
        self.assertEqual(result["replacement_counts"], {"signal": 4})

    def test_requests_are_read_only_for_every_supported_filter(self):
        def files():
            return {path.name: (path.stat().st_mtime_ns,
                                hashlib.sha256(path.read_bytes()).hexdigest())
                    for path in self.directory.iterdir() if path.is_file()}

        before = files()
        for kind in ("despike", "moving_avg", "detrend", "lowpass",
                     "highpass", "bandpass", "bandstop"):
            with self.subTest(kind=kind):
                self.result(type=kind, f1=10, f2=100, window_s=0.025)
        self.assertEqual(files(), before)
        np.testing.assert_equal(
            pl.read_parquet(self.directory / "data.parquet")["signal"].to_numpy(),
            self.values)

    def test_invalid_missing_or_ambiguous_tp_scope_is_rejected(self):
        for params, status, message in (
            ({"tp_id": 999}, 404, "not found"),
            ({"t0": 50.035}, 400, "cannot be combined"),
            ({"t1": 50.186}, 400, "cannot be combined"),
            ({"tp_id": "invalid"}, 422, None),
        ):
            with self.subTest(params=params):
                response = self.request(**params)
                self.assertEqual(response.status_code, status, response.text)
                if message:
                    self.assertIn(message, response.json()["detail"])
        self.points[0]["end_idx"] = 35
        self.write_fixture()
        response = self.request()
        self.assertEqual(response.status_code, 400)
        self.assertIn("empty or reversed", response.json()["detail"])
