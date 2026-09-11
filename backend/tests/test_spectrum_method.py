"""Reproducible method checks for docs/FFT_COMPARISON.md.

Analytic tones and a small direct DFT provide independent numerical oracles.
The endpoint/reduction cases characterize existing limitations; they must be
updated with the comparison document if a later milestone changes the method.
All Parquet/CSV/metadata fixtures live in DataDirTestCase's temporary directory.
"""

import cmath
import csv
import hashlib
import io
import math
from unittest.mock import patch

import numpy as np
import polars as pl
from fastapi.testclient import TestClient

from app import dsp, main, store
from ._base import DataDirTestCase


def direct_dft(values, k):
    """Small, deliberately non-FFT oracle from the DFT definition."""
    n = len(values)
    return sum(float(value) * cmath.exp(-2j * math.pi * k * j / n)
               for j, value in enumerate(values))


def direct_amplitudes(values):
    centered = np.asarray(values, dtype=np.float64) - np.mean(values)
    n = len(centered)
    return [abs(direct_dft(centered, k)) / n
            * (1 if k == 0 or 2 * k == n else 2)
            for k in range(n // 2 + 1)]


class SpectrumMethodTests(DataDirTestCase):
    def setUp(self):
        super().setUp()
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)

    def write_signal(self, values, fs, *, name="method", start=0.0,
                     rpm=None, gaps=None):
        directory = self.tests / name
        directory.mkdir(exist_ok=True)
        data = {
            "time_s": start + np.arange(len(values)) / fs,
            "signal": pl.Series(values, dtype=pl.Float64),
        }
        if rpm is not None:
            data["rpm"] = pl.Series(rpm, dtype=pl.Float64)
        pl.DataFrame(data).write_parquet(directory / "data.parquet")
        meta = {
            "name": name, "fs_hz": fs, "n_rows": len(values),
            "columns": list(data), "time_column": "time_s",
            "t_start": start, "duration_s": len(values) / fs,
        }
        # Omission deliberately exercises legacy metadata without gap ranges.
        if gaps is not None:
            meta["time_gap_ranges"] = gaps
        store.write_json_atomic(directory / "meta.json", meta)
        return meta

    def spectrum(self, name="method", **params):
        response = self.client.get(
            f"/api/tests/{name}/spectrum", params={"col": "signal", **params})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_fft_bin_centered_tones_use_peak_amplitude_and_remove_dc(self):
        time = np.arange(4096) / 2048.0
        self.write_signal(
            7 + 3 * np.sin(2 * np.pi * 128 * time)
            + 0.8 * np.cos(2 * np.pi * 320 * time), 2048.0)
        result = self.spectrum()
        self.assertEqual(result["mode"], "fft")
        self.assertEqual(result["n_samples"], 4096)
        self.assertEqual(result["fs_hz"], 2048.0)
        self.assertEqual(result["nan_count"], 0)
        self.assertNotIn("nperseg", result)
        np.testing.assert_array_equal(result["freqs"], np.arange(2049) * 0.5)
        self.assertAlmostEqual(result["mag"][256], 3.0, places=12)
        self.assertAlmostEqual(result["mag"][640], 0.8, places=12)
        self.assertLess(result["mag"][0], 1e-12)

    def test_even_length_nyquist_is_not_doubled(self):
        self.write_signal(4 + 2.5 * (-1.0) ** np.arange(256), 256.0)
        result = self.spectrum()
        self.assertEqual(result["freqs"][-1], 128.0)
        self.assertAlmostEqual(result["mag"][-1], 2.5, places=12)
        self.assertLess(max(result["mag"][:-1]), 1e-12)

    def test_odd_length_last_bin_is_doubled_without_padding(self):
        self.write_signal(
            4 + 2.5 * np.cos(2 * np.pi * 50 * np.arange(101) / 101), 101.0)
        result = self.spectrum()
        self.assertEqual(len(result["freqs"]), 51)
        self.assertEqual(result["freqs"][-1], 50.0)
        self.assertAlmostEqual(result["mag"][-1], 2.5, places=12)

    def test_off_bin_fft_matches_rectangular_window_direct_dft(self):
        values = 3 * np.cos(2 * np.pi * 10.25 * np.arange(128) / 128)
        self.write_signal(values, 128.0)
        result = self.spectrum(nperseg=64)  # Welch-only option has no FFT effect.
        np.testing.assert_allclose(result["mag"], direct_amplitudes(values),
                                   rtol=1e-11, atol=1e-12)
        self.assertEqual(result["freqs"][np.argmax(result["mag"])], 10.0)
        self.assertLess(max(result["mag"]), 3.0)
        self.assertGreater(result["mag"][11], 0.5)

    def test_nonfinite_values_interpolate_by_index_and_hold_endpoints(self):
        values = list(np.arange(32, dtype=float))
        for index, missing in [(0, None), (1, np.nan), (10, np.inf),
                               (15, -np.inf), (30, None), (31, np.nan)]:
            values[index] = missing
        self.write_signal(values, 32.0)
        expected = np.arange(32, dtype=float)
        expected[:2] = 2.0
        expected[30:] = 29.0
        result = self.spectrum()
        self.assertEqual(result["nan_count"], 6)
        np.testing.assert_allclose(result["mag"], direct_amplitudes(expected),
                                   rtol=1e-11, atol=1e-12)
        # A linear trend remains: spectrum detrending removes only the mean.
        self.assertGreater(result["mag"][1], 1.0)

    def test_welch_periodic_hann_density_has_expected_peak_and_power(self):
        time = np.arange(8192) / 2048.0
        self.write_signal(7 + 3 * np.sin(2 * np.pi * 128 * time), 2048.0)
        result = self.spectrum(mode="welch")
        self.assertEqual(result["nperseg"], 4096)
        self.assertEqual(result["n_samples"], 8192)
        np.testing.assert_array_equal(result["freqs"], np.arange(2049) * 0.5)
        # Periodic Hann: line-center PSD = A^2 * L / (3 * fs).
        self.assertAlmostEqual(result["mag"][256], 6.0, places=11)
        self.assertAlmostEqual(result["mag"][255], 1.5, places=11)
        self.assertAlmostEqual(result["mag"][257], 1.5, places=11)
        self.assertAlmostEqual(sum(result["mag"]) * 0.5, 4.5, places=11)

    def test_welch_overlap_segment_detrend_and_unused_tail_match_direct_dft(self):
        values = (np.arange(181, dtype=float) / 7
                  + np.sin(2 * np.pi * np.arange(181) / 11))
        values[160:] += 1000  # These samples are outside all four complete segments.
        self.write_signal(values, 128.0)
        result = self.spectrum(mode="welch", nperseg=64)
        window = 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(64) / 64)
        expected = []
        for k in range(33):
            segment_power = []
            for start in (0, 32, 64, 96):
                segment = values[start:start + 64]
                transformed = direct_dft((segment - segment.mean()) * window, k)
                power = abs(transformed) ** 2 / (128 * sum(window ** 2))
                segment_power.append(power * (1 if k in (0, 32) else 2))
            expected.append(sum(segment_power) / 4)
        np.testing.assert_allclose(result["mag"], expected, rtol=1e-10, atol=1e-12)

    def test_welch_segment_length_clamps_and_short_ranges_still_work(self):
        for n, requested, effective in [(128, -5, 64), (128, 20, 64),
                                        (128, 65, 65), (128, 4096, 128),
                                        (16, 4096, 16)]:
            with self.subTest(n=n, requested=requested):
                self.write_signal(np.arange(n, dtype=float), 128.0)
                result = self.spectrum(mode="welch", nperseg=requested)
                self.assertEqual(result["nperseg"], effective)
                self.assertEqual(len(result["freqs"]), effective // 2 + 1)
                self.assertAlmostEqual(result["freqs"][1], 128 / effective,
                                       delta=0.00005)

    def test_time_window_rounds_outward_using_metadata_rate_and_origin(self):
        values = np.sin(2 * np.pi * np.arange(512) / 11)
        meta = self.write_signal(values, 128.0, start=10.0)
        self.assertEqual(store.window_bounds(meta, 10.253, 10.498), (32, 65))
        result = self.spectrum(t0=10.253, t1=10.498)
        self.assertEqual(result["n_samples"], 33)
        np.testing.assert_allclose(result["mag"], direct_amplitudes(values[32:65]),
                                   rtol=1e-11, atol=1e-12)
        self.assertEqual(self.spectrum(t0=-100, t1=100)["n_samples"], 512)

    def test_saved_tp_spectrum_matches_half_open_export_and_full_window_is_compatible(self):
        self.write_signal(np.sin(2 * np.pi * np.arange(256) / 16), 128.0)
        store.write_json_atomic(self.tests / "method" / "testpoints.json", {
            "version": 1, "test": "method", "fs_hz": 128.0,
            "test_points": [{"id": 1, "name": "TP-1", "label": "",
                             "start_s": 0.25, "end_s": 1.25,
                             "start_idx": 32, "end_idx": 160}],
        })
        exported = self.client.get("/api/tests/method/testpoints/1/export")
        self.assertEqual(exported.status_code, 200, exported.text)
        rows = list(csv.DictReader(io.StringIO(exported.text)))
        self.assertEqual(len(rows), 128)
        result = self.spectrum(t0=0.25, t1=1.25)
        self.assertEqual(result["n_samples"], 129)
        self.assertEqual(result["freqs"][1], 128 / 129)
        saved = self.spectrum(tp_id=1)
        self.assertEqual(saved["n_samples"], len(rows))
        self.assertEqual((saved["i0"], saved["i1"]), (32, 160))
        self.assertEqual(saved["tp_id"], 1)
        self.assertEqual(saved["time_start_s"], float(rows[0]["time_s"]))
        self.assertEqual(saved["time_end_s"], float(rows[-1]["time_s"]))
        self.assertEqual(saved["freqs"][1], 1.0)

    def test_fft_payload_maxima_retain_their_actual_bin_frequencies(self):
        time = np.arange(16384) / 2048.0
        self.write_signal(3 * np.sin(2 * np.pi * 128.125 * time), 2048.0)
        result = self.spectrum()
        self.assertEqual(len(result["freqs"]), 2731)  # 8193 bins, groups of 3.
        self.assertEqual(result["freqs"][np.argmax(result["mag"])], 128.125)
        self.assertAlmostEqual(max(result["mag"]), 3.0, places=11)
        exact = dsp.spectrum_samples("method", "signal", "fft", None, None)
        self.assertEqual(exact.freqs[np.argmax(exact.mag)], 128.125)
        np.testing.assert_array_equal(result["freqs"], exact.freqs[result["bin_indices"]])
        np.testing.assert_array_equal(result["mag"], exact.mag[result["bin_indices"]])
        self.assertEqual(result["reduction"], {
            "method": "max-bin", "factor": 3,
            "n_bins_original": 8193, "n_bins_returned": 2731,
        })
        self.assertEqual(result["peak"]["frequency_hz"], 128.125)
        self.assertEqual(result["peak"]["bin_index"], 1025)
        # max_bins is an internal argument, not an exposed HTTP option.
        self.assertEqual(self.spectrum(max_bins=9000), result)

    def test_reduced_welch_density_cannot_be_integrated_as_original_bins(self):
        time = np.arange(8192) / 2048.0
        self.write_signal(3 * np.sin(2 * np.pi * 128.25 * time), 2048.0)
        result = self.spectrum(mode="welch", nperseg=8192)
        self.assertEqual(len(result["freqs"]), 2049)  # 4097 bins, groups of 2.
        self.assertEqual(result["freqs"][np.argmax(result["mag"])], 128.25)
        self.assertAlmostEqual(max(result["mag"]), 12.0, places=10)
        self.assertAlmostEqual(sum(result["mag"]) * 0.5, 7.5, places=10)
        exact = dsp.spectrum_samples("method", "signal", "welch", None, None,
                                     nperseg=8192)
        self.assertAlmostEqual(sum(exact.mag) * 0.25, 4.5, places=10)
        self.assertEqual(result["method"]["units"], "U²/Hz")

    def test_time_plot_filter_does_not_change_stored_spectrum(self):
        time = np.arange(4096) / 2048.0
        self.write_signal(3 * np.sin(2 * np.pi * 256 * time), 2048.0)
        before = self.spectrum()
        filtered = dsp.filtered_window(
            "method", ["signal"], "lowpass", None, None, 1000,
            f1=50.0, order=4)
        self.assertEqual(filtered["mode"], "raw")
        self.assertLess(np.std(filtered["series"]["signal"]), 0.2)
        self.assertEqual(self.spectrum(), before)

    def test_known_gaps_block_both_estimators_even_with_finite_signal(self):
        self.write_signal(np.arange(128, dtype=float), 128.0, gaps=[[64, 80]])
        for mode in ("fft", "welch"):
            with self.subTest(mode=mode):
                response = self.client.get("/api/tests/method/spectrum",
                                           params={"col": "signal", "mode": mode})
                self.assertEqual(response.status_code, 400)
                self.assertIn("crosses 1 missing-data gap", response.json()["detail"])
                self.assertEqual(self.spectrum(mode=mode, t1=63 / 128)["n_samples"], 64)

    def test_unusable_signal_and_sample_limits_report_analysis_errors(self):
        for values, message in [(np.zeros(15), "range too short"),
                                ([None] * 32, "range is all NaN"),
                                ([1.0] + [None] * 31, "range is all NaN")]:
            with self.subTest(message=message, values=values[:2]):
                self.write_signal(values, 128.0)
                response = self.client.get("/api/tests/method/spectrum",
                                           params={"col": "signal"})
                self.assertEqual(response.status_code, 400)
                self.assertIn(message, response.json()["detail"])
        self.write_signal(np.zeros(32), 128.0)
        with patch.object(dsp, "MAX_FILTER_SAMPLES", 31):
            response = self.client.get("/api/tests/method/spectrum",
                                       params={"col": "signal"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("range spans 32 samples (max 31)", response.json()["detail"])

    def test_rpm_uses_finite_absolute_values_including_stopped_samples(self):
        time = np.arange(256) / 256.0
        rpm = [1200, -1200, 0, None, np.inf, -np.inf, 2400, -2400] * 32
        self.write_signal(np.sin(2 * np.pi * 96 * time), 256.0, rpm=rpm)
        plain = self.spectrum()
        result = self.spectrum(rpm_col="rpm")
        self.assertEqual(result["mean_rpm"], 1440.0)
        self.assertEqual(result["min_rpm"], 0.0)
        self.assertEqual(result["max_rpm"], 2400.0)
        self.assertEqual(result["freqs"], plain["freqs"])
        self.assertEqual(result["mag"], plain["mag"])
        frequency = result["freqs"][np.argmax(result["mag"])]
        self.assertEqual(frequency * 60 / result["mean_rpm"], 4.0)

    def test_unusable_rpm_reports_analysis_errors(self):
        for rpm, message in [([None] * 32, "all NaN"),
                             ([0.0] * 32, "no positive mean speed")]:
            with self.subTest(message=message):
                self.write_signal(np.arange(32, dtype=float), 128.0, rpm=rpm)
                response = self.client.get(
                    "/api/tests/method/spectrum",
                    params={"col": "signal", "rpm_col": "rpm"})
                self.assertEqual(response.status_code, 400)
                self.assertIn(message, response.json()["detail"])

    def test_saved_indices_override_display_times_and_read_actual_time_centers(self):
        values = np.sin(2 * np.pi * np.arange(512) / 13)
        self.write_signal(values, 128.0, start=50.0000007)
        directory = self.tests / "method"
        # Simulate quantized stored timestamps: actual centers are reported,
        # while frequency still uses the declared nominal rate without resampling.
        frame = pl.read_parquet(directory / "data.parquet")
        times = np.round(frame["time_s"].to_numpy(), 3) + 0.0000007
        frame.with_columns(pl.Series("time_s", times)).write_parquet(
            directory / "data.parquet")
        store.write_json_atomic(directory / "testpoints.json", {"test_points": [
            {"id": 7, "start_idx": 33, "end_idx": 188,
             "start_s": 50.4, "end_s": 51.6},
        ]})
        result = self.spectrum(tp_id=7)
        self.assertEqual((result["i0"], result["i1"]), (33, 188))
        self.assertEqual(result["time_start_s"], times[33])
        self.assertEqual(result["time_end_s"], times[187])
        self.assertEqual(result["method"]["bin_spacing_hz"], 128 / 155)
        np.testing.assert_allclose(result["mag"], direct_amplitudes(values[33:188]),
                                   rtol=1e-11, atol=1e-12)

    def test_legacy_open_tp_bounds_stop_at_next_tp_or_data_end(self):
        self.write_signal(np.arange(512, dtype=float), 128.0, start=10.0)
        store.write_json_atomic(self.tests / "method" / "testpoints.json", {
            "test_points": [
                {"id": 1, "start_s": 10.25, "end_s": None},
                {"id": 2, "start_s": 11.0, "end_s": None},
                {"id": 3, "start_s": 12.0, "end_s": None},
            ],
        })
        for tp_id, expected in [(1, (32, 128)), (2, (128, 256)), (3, (256, 512))]:
            with self.subTest(tp_id=tp_id):
                result = self.spectrum(tp_id=tp_id)
                self.assertEqual((result["i0"], result["i1"]), expected)
                self.assertEqual(result["n_samples"], expected[1] - expected[0])

    def test_tp_scope_rejects_conflicting_bounds_and_missing_or_empty_points(self):
        self.write_signal(np.arange(128, dtype=float), 128.0)
        store.write_json_atomic(self.tests / "method" / "testpoints.json", {
            "test_points": [{"id": 1, "start_idx": 20, "end_idx": 20,
                             "start_s": 20 / 128, "end_s": 20 / 128}],
        })
        cases = [({"tp_id": 9}, 404, "test point 9 not found"),
                 ({"tp_id": 1}, 400, "empty or reversed"),
                 ({"tp_id": 1, "t0": 0}, 400, "cannot be combined"),
                 ({"tp_id": 1, "t1": 1}, 400, "cannot be combined"),
                 ({"t0": "nan"}, 400, "must be finite"),
                 ({"t1": "inf"}, 400, "must be finite")]
        for params, status, message in cases:
            with self.subTest(params=params):
                response = self.client.get("/api/tests/method/spectrum",
                                           params={"col": "signal", **params})
                self.assertEqual(response.status_code, status, response.text)
                self.assertIn(message, response.json()["detail"])

    def test_tp_gap_rejection_uses_exact_half_open_rows(self):
        self.write_signal(np.arange(128, dtype=float), 128.0, gaps=[[64, 80]])
        store.write_json_atomic(self.tests / "method" / "testpoints.json", {
            "test_points": [
                {"id": 1, "start_idx": 0, "end_idx": 64, "start_s": 0},
                {"id": 2, "start_idx": 80, "end_idx": 128, "start_s": 80 / 128},
                {"id": 3, "start_idx": 48, "end_idx": 96, "start_s": 48 / 128},
            ],
        })
        for mode in ("fft", "welch"):
            with self.subTest(mode=mode):
                self.assertEqual(self.spectrum(tp_id=1, mode=mode)["n_samples"], 64)
                self.assertEqual(self.spectrum(tp_id=2, mode=mode)["n_samples"], 48)
                response = self.client.get("/api/tests/method/spectrum", params={
                    "col": "signal", "mode": mode, "tp_id": 3,
                })
                self.assertEqual(response.status_code, 400)
                self.assertIn("crosses 1 missing-data gap", response.json()["detail"])

    def test_method_metadata_describes_actual_fft_and_welch_processing(self):
        self.write_signal(np.arange(181, dtype=float), 128.0)
        fft = self.spectrum()
        self.assertEqual(fft["method"], {
            "version": "kiha-spectrum-v2", "source": "stored", "prefilter": "none",
            "sampling": "metadata_fs", "missing_values": "linear_by_index_hold_edges",
            "detrend": "constant", "window": "rectangular", "nfft": 181,
            "bin_spacing_hz": 128 / 181, "onesided": True,
            "scaling": "peak_amplitude", "units": "U", "nperseg": None,
            "noverlap": None, "average": None, "segment_count": 1,
            "used_samples": 181, "trailing_samples": 0,
        })
        welch = self.spectrum(mode="welch", nperseg=64)
        method = welch["method"]
        for key, value in {"window": "hann_periodic", "nfft": 64, "nperseg": 64,
                           "noverlap": 32, "average": "mean", "segment_count": 4,
                           "used_samples": 160, "trailing_samples": 21,
                           "scaling": "density", "units": "U²/Hz",
                           "bin_spacing_hz": 2}.items():
            self.assertEqual(method[key], value, key)
        self.assertEqual(welch["reduction"], {
            "method": "none", "factor": 1, "n_bins_original": 33, "n_bins_returned": 33,
        })
        self.assertEqual(welch["bin_indices"], list(range(33)))

    def test_missing_counts_and_existing_timing_flags_do_not_claim_legacy_validation(self):
        values = list(np.arange(32, dtype=float))
        values[0], values[15], values[31] = None, np.inf, np.nan
        meta = self.write_signal(values, 32.0)
        legacy = self.spectrum()
        self.assertEqual((legacy["finite_count"], legacy["nan_count"]), (29, 3))
        self.assertEqual(legacy["quality"], {
            "known_gap_count": 0, "gap_metadata_available": False,
            "time_source": None, "time_quantized": None, "jitter_warning": None,
        })
        meta.update(time_gap_ranges=[], time_source="generated", time_quantized=False,
                    jitter_warning=False)
        store.write_json_atomic(self.tests / "method" / "meta.json", meta)
        generated = self.spectrum()
        self.assertTrue(generated["quality"]["gap_metadata_available"])
        self.assertEqual(generated["quality"]["time_source"], "generated")
        self.assertFalse(generated["quality"]["jitter_warning"])
        self.assertEqual(generated["mag"], legacy["mag"])

    def test_narrow_native_bins_remain_distinct_and_true_peak_is_unrounded(self):
        n, fs, k = 16384, 0.1, 1025
        self.write_signal(np.sin(2 * np.pi * k * np.arange(n) / n), fs)
        result = self.spectrum()
        self.assertTrue(np.all(np.diff(result["freqs"]) > 0))
        self.assertEqual(result["peak"]["frequency_hz"], k * fs / n)
        self.assertEqual(result["freqs"][np.argmax(result["mag"])], k * fs / n)
        self.assertNotEqual(result["peak"]["frequency_hz"], round(k * fs / n, 4))

    def test_reduction_ties_choose_first_native_bin_and_keep_partial_tail(self):
        self.write_signal(np.zeros(34), 34.0)
        result = dsp.spectrum("method", "signal", "fft", None, None, max_bins=4)
        self.assertEqual(result["bin_indices"], [0, 5, 10, 15])
        self.assertEqual(result["freqs"], [0, 5, 10, 15])
        self.assertEqual(result["peak"]["bin_index"], 0)
        self.write_signal((-1.0) ** np.arange(34), 34.0)
        tail = dsp.spectrum("method", "signal", "fft", None, None, max_bins=4)
        self.assertEqual(tail["bin_indices"][-1], 17)
        self.assertEqual(tail["freqs"][-1], 17)
        self.assertAlmostEqual(tail["mag"][-1], 1)

    def test_spectrum_time_column_alias_and_read_only_source(self):
        self.write_signal(np.arange(32, dtype=float), 32.0)
        paths = list((self.tests / "method").iterdir())
        before = {p.name: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
                  for p in paths}
        result = self.client.get("/api/tests/method/spectrum", params={
            "col": "time_s", "rpm_col": "time_s", "mode": "welch",
        })
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["n_samples"], 32)
        after = {p.name: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
                 for p in paths}
        self.assertEqual(after, before)

    def test_internal_display_cap_and_invalid_rate_fail_clearly(self):
        meta = self.write_signal(np.arange(32, dtype=float), 32.0)
        for cap in (0, -1, 1.5, True):
            with self.subTest(cap=cap), self.assertRaisesRegex(ValueError, "positive integer"):
                dsp.spectrum("method", "signal", "fft", None, None, max_bins=cap)
        meta["fs_hz"] = 0
        store.write_json_atomic(self.tests / "method" / "meta.json", meta)
        response = self.client.get("/api/tests/method/spectrum", params={"col": "signal"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("sample rate", response.json()["detail"])
