"""Full-resolution time-plot exports, processing parity and failure atomicity."""

import asyncio
from contextlib import contextmanager
import csv
import hashlib
import io
from unittest.mock import patch
from urllib.parse import unquote
import zipfile

import numpy as np
import polars as pl
from fastapi.testclient import TestClient

from app import dsp, locks, main, plot_export, store
from app.ingest import build_pyramid
from ._base import DataDirTestCase


class PlotExportFixture(DataDirTestCase):
    def setUp(self):
        super().setUp()
        self.client = TestClient(main.app)
        self.time = 100.0000007 + np.arange(240, dtype=np.float64) / 1000.0
        self.signal = np.full(240, 10_000.0)
        self.signal[35:187] = 1.2345678901234567
        self.signal[70:74] = 400.0
        self.signal[100] = np.nan
        self.points = [{"id": 7, "start_idx": 35, "end_idx": 187,
                        "start_s": 100.039, "end_s": 100.220}]
        self.write_test("alpha", self.time, self.signal, self.points)

    def write_test(self, name, time, signal, points, *, fs=1000.0,
                   time_column="clock", gaps=None, extra=None):
        directory = self.tests / name
        directory.mkdir(exist_ok=True)
        pl.DataFrame({time_column: time, "signal": signal, **(extra or {})}).write_parquet(
            directory / "data.parquet", row_group_size=64)
        columns = [time_column, "signal", *(extra or {})]
        store.write_json_atomic(directory / "meta.json", {
            "name": name, "fs_hz": fs, "n_rows": len(time),
            "columns": columns, "time_column": time_column,
            "t_start": float(time[0]), "time_gap_ranges": gaps or [],
        })
        store.write_json_atomic(directory / "status.json", {"status": "ready"})
        store.write_json_atomic(directory / "testpoints.json", {"test_points": points})
        (directory / "raw.csv").write_bytes(b"untouched,original\n1,2\n")
        return directory

    def payload(self, **overrides):
        return {"column": "signal", "data": "both", "sources": [
            {"test": "alpha", "tp_id": 7, "px": 800, "display": "auto",
             "expected_i0": 35, "expected_i1": 187}],
            "filter": {"kind": "despike", "window_s": 0.025,
                       "max_spike_s": 0.010, "abs_floor": 10},
            "x_range": None, **overrides}

    def export(self, **overrides):
        return self.client.post("/api/plot-export", json=self.payload(**overrides))

    def rows(self, response):
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "text/csv; charset=utf-8")
        self.assertIn("attachment", response.headers["content-disposition"])
        rows = list(csv.DictReader(io.StringIO(response.text)))
        self.assertEqual(int(response.headers["x-export-rows"]), len(rows))
        return rows


class PlotExportTests(PlotExportFixture):
    def test_saved_tp_full_resolution_values_ids_and_precision(self):
        response = self.export()
        rows = self.rows(response)
        self.assertEqual(list(rows[0]), ["source_test", "test_point_id", "sample_index",
                                        "time_s", "tp_time_s", "signal [original]",
                                        "signal [filtered]"])
        self.assertEqual(len(rows), 152)
        self.assertEqual([int(row["sample_index"]) for row in rows], list(range(35, 187)))
        self.assertEqual({row["source_test"] for row in rows}, {"alpha"})
        self.assertEqual({row["test_point_id"] for row in rows}, {"7"})
        self.assertEqual(float(rows[0]["time_s"]), self.time[35])
        self.assertEqual(float(rows[0]["tp_time_s"]), 0.0)
        self.assertEqual(float(rows[0]["signal [original]"]), self.signal[35])
        self.assertEqual([float(row["signal [filtered]"]) for row in rows[35:39]],
                         [self.signal[35]] * 4)
        self.assertEqual(rows[65]["signal [original]"], "nan")
        self.assertEqual(rows[65]["signal [filtered]"], "")
        self.assertIn("alpha_signal_test-points_both.csv",
                      unquote(response.headers["content-disposition"]))

    def test_explicit_original_filtered_and_both_column_choices(self):
        for choice, expected in (("original", ["signal [original]"]),
                                 ("filtered", ["signal [filtered]"]),
                                 ("both", ["signal [original]", "signal [filtered]"])):
            with self.subTest(choice=choice):
                response = self.export(data=choice, **({"filter": None} if choice == "original" else {}))
                rows = self.rows(response)
                self.assertEqual(list(rows[0])[5:], expected)
                self.assertEqual(len(rows), 152)

    def test_tp_x_range_crops_after_full_tp_filtering_inclusive_sample_centers(self):
        rows = self.rows(self.export(x_range=[0.035, 0.038]))
        self.assertEqual([int(row["sample_index"]) for row in rows], [70, 71, 72, 73])
        self.assertEqual([float(row["signal [original]"]) for row in rows], [400.0] * 4)
        self.assertEqual([float(row["signal [filtered]"]) for row in rows],
                         [self.signal[35]] * 4)
        # Filtering only the cropped four samples would fail. Context remains
        # the complete saved TP, including both clean replacement shoulders.

    def test_cross_test_tps_keep_each_origin_rate_and_large_id(self):
        other_time = 12.0 + np.arange(160) / 500.0
        other_values = np.full(160, 20.0)
        other_values[30:34] = 800.0
        large_id = 2**70 + 17
        self.write_test("beta", other_time, other_values,
                        [{"id": large_id, "start_idx": 7, "end_idx": 100,
                          "start_s": 12.014}], fs=500.0, time_column="elapsed")
        sources = self.payload()["sources"] + [{"test": "beta", "tp_id": large_id}]
        response = self.export(sources=sources)
        rows = self.rows(response)
        second = [row for row in rows if row["source_test"] == "beta"]
        self.assertEqual(len(rows), 152 + 93)
        self.assertEqual(len(second), 93)
        self.assertEqual({row["test_point_id"] for row in second}, {str(large_id)})
        self.assertEqual(float(second[0]["time_s"]), other_time[7])
        self.assertEqual(float(second[0]["tp_time_s"]), 0.0)
        self.assertEqual([float(row["signal [filtered]"]) for row in second[23:27]], [20.0] * 4)
        self.assertIn("multi-test_signal_test-points_both.csv",
                      unquote(response.headers["content-disposition"]))

    def test_legacy_and_open_saved_tp_bounds_match_shared_resolver(self):
        store.write_json_atomic(self.tests / "alpha" / "testpoints.json", {
            "test_points": [{"id": 7, "start_s": float(self.time[35])},
                            {"id": 9, "start_idx": 187, "start_s": float(self.time[187])}]})
        rows = self.rows(self.export())
        self.assertEqual([int(row["sample_index"]) for row in rows], list(range(35, 187)))

    def test_full_test_envelope_context_matches_dsp_before_cropping(self):
        directory = self.tests / "alpha"
        build_pyramid(directory / "data.parquet", directory / "pyramid", "clock")
        t0, t1 = float(self.time[37]), float(self.time[186])
        source = {"test": "alpha", "t0": t0, "t1": t1,
                  "px": 200, "display": "envelope"}
        spec = {"kind": "moving_avg", "window_s": 0.011}
        samples = dsp.filtered_samples("alpha", ["signal"], t0=t0, t1=t1,
                                       px=200, display="envelope", **spec)
        self.assertLess(samples.s0, samples.i0)
        self.assertGreater(samples.s1, samples.i1)
        response = self.export(sources=[source], filter=spec, x_range=[t0, t1])
        rows = self.rows(response)
        indices = np.array([int(row["sample_index"]) for row in rows])
        np.testing.assert_array_equal(indices, np.arange(37, 187))
        expected = samples.filtered["signal"][indices - samples.s0]
        np.testing.assert_allclose([float(row["signal [filtered]"] or "nan") for row in rows],
                                   expected, equal_nan=True, rtol=1e-14)
        self.assertTrue(all(row["test_point_id"] == row["tp_time_s"] == "" for row in rows))
        # Processing only the visible rows yields different boundary values.
        line = dsp.filtered_samples("alpha", ["signal"], t0=t0, t1=t1,
                                    px=200, display="line", **spec)
        self.assertNotAlmostEqual(expected[0], line.filtered["signal"][37 - line.s0])

    def test_full_test_original_clips_actual_time_without_filter_requirement(self):
        t0, t1 = float(self.time[37]), float(self.time[42])
        rows = self.rows(self.export(data="original", filter=None,
                                    sources=[{"test": "alpha", "t0": t0, "t1": t1}],
                                    x_range=[float(self.time[38]), float(self.time[40])]))
        self.assertEqual([int(row["sample_index"]) for row in rows], [38, 39, 40])

    def test_missing_and_infinite_cells_preserve_original_and_filtered_semantics(self):
        self.signal[90:93] = [np.nan, np.inf, -np.inf]
        values = self.signal.tolist()
        values[93] = None
        self.write_test("alpha", self.time, values, self.points)
        rows = self.rows(self.export())
        self.assertEqual([rows[i - 35]["signal [original]"] for i in range(90, 94)],
                         ["nan", "inf", "-inf", ""])
        self.assertEqual([rows[i - 35]["signal [filtered]"] for i in range(90, 94)],
                         [""] * 4)

    def test_known_gaps_are_not_bridged_in_filtered_csv(self):
        self.signal[35:100] = 0
        self.signal[100:120] = np.nan
        self.signal[120:187] = 100
        self.write_test("alpha", self.time, self.signal, self.points, gaps=[[100, 120]])
        rows = self.rows(self.export(filter={"kind": "moving_avg", "window_s": 0.011}))
        self.assertEqual([float(row["signal [filtered]"]) for row in rows[:65]], [0.0] * 65)
        self.assertEqual([row["signal [filtered]"] for row in rows[65:85]], [""] * 20)
        self.assertEqual([float(row["signal [filtered]"]) for row in rows[85:]], [100.0] * 67)

    def test_original_integer_values_and_reserved_signal_names_do_not_collide(self):
        integers = np.arange(240, dtype=np.int64) + 2**60
        self.write_test("alpha", self.time, self.signal, self.points,
                        extra={"source_test": integers})
        rows = self.rows(self.export(column="source_test", data="original", filter=None))
        self.assertEqual(list(rows[0])[-1], "source_test [original]")
        self.assertEqual(rows[0]["source_test [original]"], str(integers[35]))
        self.assertEqual(rows[0]["source_test"], "alpha")

    def test_large_reduced_plot_exports_every_row_across_batches(self):
        count = 140_006
        t = np.arange(count, dtype=np.float64) / 1000
        y = np.full(count, 1.2345678901234567)
        y[50_002:50_005] = 1000
        self.write_test("large", t, y, [{"id": 2, "start_idx": 3,
                                        "end_idx": count - 2, "start_s": 0.003}])
        response = self.export(sources=[{"test": "large", "tp_id": 2,
                                        "px": 200, "display": "line"}])
        rows = self.rows(response)
        self.assertEqual(len(rows), count - 5)
        self.assertEqual(rows[0]["sample_index"], "3")
        self.assertEqual(rows[-1]["sample_index"], str(count - 3))
        self.assertEqual(sum(line.startswith('"source_test"') for line in response.text.splitlines()), 1)
        self.assertEqual(float(rows[50_002 - 3]["signal [original]"]), 1000)
        self.assertAlmostEqual(float(rows[50_002 - 3]["signal [filtered]"]), y[0], places=14)

    def test_empty_crop_missing_or_changed_sources_fail_before_attachment(self):
        cases = [({"x_range": [50, 60]}, 400),
                 ({"sources": [{"test": "missing", "tp_id": 7}]}, 404),
                 ({"sources": [{"test": "alpha", "tp_id": 99}]}, 404),
                 ({"sources": [{"test": "alpha", "tp_id": 7,
                                 "expected_i0": 36, "expected_i1": 187}]}, 409),
                 ({"column": "clock"}, 400),
                 ({"column": "missing"}, 400)]
        for params, status in cases:
            with self.subTest(params=params):
                response = self.export(**params)
                self.assertEqual(response.status_code, status, response.text)
                self.assertNotIn("content-disposition", response.headers)
                self.assertIn("detail", response.json())

    def test_busy_error_and_missing_status_are_rejected(self):
        for status in ("receiving", "ingesting", "rebuilding", "error", "missing"):
            with self.subTest(status=status):
                store.write_json_atomic(self.tests / "alpha" / "status.json", {"status": status})
                response = self.export()
                self.assertEqual(response.status_code, 409)
                self.assertNotIn("content-disposition", response.headers)

    def test_strict_payload_validation_and_path_containment(self):
        cases = [({"filter": None}, 422),
                 ({"x_range": [2, 1]}, 422),
                 ({"sources": []}, 422),
                 ({"sources": [{"test": "alpha", "tp_id": 7, "t0": 100}]}, 422),
                 ({"sources": [{"test": "alpha", "tp_id": True}]}, 422),
                 ({"sources": [{"test": "alpha", "expected_i0": 0}]}, 422),
                 ({"sources": [{"test": "alpha"}, {"test": "alpha", "tp_id": 7}]}, 422),
                 ({"sources": [{"test": "alpha", "tp_id": 7}] * 2}, 422),
                 ({"sources": [{"test": "../alpha", "tp_id": 7}]}, 400),
                 ({"sources": [{"test": "..\\alpha", "tp_id": 7}]}, 400),
                 ({"filter": {"kind": "despike", "unknown": 1}}, 422)]
        for params, status in cases:
            with self.subTest(params=params):
                response = self.export(**params)
                self.assertEqual(response.status_code, status, response.text)
        payload = self.payload()
        for path, value in (("x_range", [float("inf"), 1]), ("sources", [{"test": "alpha", "t0": float("nan")}] )):
            with self.subTest(nonfinite=path):
                payload[path] = value
                with self.assertRaises(ValueError):
                    plot_export.PlotExportRequest.model_validate(payload)
                payload = self.payload()

    def test_aggregate_row_and_source_limits_are_enforced_before_native_reads(self):
        with patch.object(plot_export, "MAX_EXPORT_ROWS", 151), \
                patch.object(dsp, "filtered_samples") as process:
            response = self.export()
            self.assertEqual(response.status_code, 400)
            self.assertIn("max 151", response.json()["detail"])
            process.assert_not_called()
        response = self.export(sources=[{"test": "alpha", "tp_id": i} for i in range(129)])
        self.assertEqual(response.status_code, 422)

    def test_all_filter_kinds_use_shared_unrounded_processing(self):
        for kind in ("despike", "moving_avg", "detrend", "lowpass", "highpass", "bandpass", "bandstop"):
            with self.subTest(kind=kind):
                spec = {"kind": kind, "window_s": 0.025, "f1": 10, "f2": 100}
                samples = dsp.filtered_samples("alpha", ["signal"], t0=None, t1=None,
                                               px=800, tp_id=7, **spec)
                rows = self.rows(self.export(filter=spec))
                np.testing.assert_allclose([float(row["signal [filtered]"] or "nan") for row in rows],
                                           samples.filtered["signal"], rtol=1e-14, equal_nan=True)

    def test_later_source_filter_failure_closes_staging_file_without_partial_csv(self):
        self.write_test("short", self.time[:20], self.signal[:20],
                        [{"id": 1, "start_idx": 0, "end_idx": 20, "start_s": 100}])
        sources = self.payload()["sources"] + [{"test": "short", "tp_id": 1}]
        factory = plot_export.tempfile.SpooledTemporaryFile
        created = []

        def tracked(*args, **kwargs):
            output = factory(*args, **kwargs)
            created.append(output)
            return output

        with patch.object(plot_export.tempfile, "SpooledTemporaryFile", tracked):
            response = self.export(sources=sources)
        self.assertEqual(response.status_code, 400)
        self.assertIn("cannot filter 'short'", response.json()["detail"])
        self.assertNotIn("content-disposition", response.headers)
        self.assertTrue(created and all(output.closed for output in created))

    def test_changed_bounds_between_preflight_and_staging_are_rejected(self):
        @contextmanager
        def changed_read(name, **kwargs):
            points = [{**self.points[0], "end_idx": 186}]
            store.write_json_atomic(self.tests / name / "testpoints.json", {"test_points": points})
            with locks.data_read(name, **kwargs):
                yield

        with patch.object(plot_export, "data_read", changed_read):
            response = self.export()
        self.assertEqual(response.status_code, 409)
        self.assertNotIn("content-disposition", response.headers)

    def test_staging_and_completed_download_are_read_only_and_close_tempfile(self):
        def fingerprints():
            return {str(path): (path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
                    for path in self.tests.rglob("*") if path.is_file()}

        before = fingerprints()
        factory = plot_export.tempfile.SpooledTemporaryFile
        created = []

        def tracked(*args, **kwargs):
            output = factory(*args, **kwargs)
            created.append(output)
            return output

        with patch.object(plot_export.tempfile, "SpooledTemporaryFile", tracked):
            self.rows(self.export())
        self.assertEqual(fingerprints(), before)
        self.assertTrue(created and all(output.closed for output in created))

    def test_cancelled_stream_closes_prepared_tempfile(self):
        request = plot_export.PlotExportRequest.model_validate(self.payload())
        output = io.BytesIO(b"ready,csv\n1,2\n")
        with patch.object(plot_export, "prepare_export", return_value=(output, 14, 1)):
            response = plot_export.api_plot_export(request)

        async def consume_and_cancel():
            messages = 0

            async def send(message):
                nonlocal messages
                messages += 1
                if message["type"] == "http.response.body":
                    raise asyncio.CancelledError()

            try:
                await response({"type": "http", "asgi": {"spec_version": "2.4"}}, None, send)
            except asyncio.CancelledError:
                pass
            self.assertGreater(messages, 1)

        asyncio.run(consume_and_cancel())
        self.assertTrue(output.closed)

    def test_failed_transport_before_body_starts_closes_staged_file(self):
        request = plot_export.PlotExportRequest.model_validate(self.payload())
        output = io.BytesIO(b"ready,csv\n1,2\n")
        with patch.object(plot_export, "prepare_export", return_value=(output, 14, 1)):
            response = plot_export.api_plot_export(request)

        async def failed_send(_message):
            raise OSError("connection closed before headers")

        async def send_response():
            try:
                await response({"type": "http", "asgi": {"spec_version": "2.4"}}, None, failed_send)
            except Exception:
                pass

        asyncio.run(send_response())
        self.assertTrue(output.closed)

    def test_cors_exposes_download_filename_and_summary_headers(self):
        response = self.client.post("/api/plot-export", json=self.payload(),
                                    headers={"Origin": "http://127.0.0.1:3000"})
        self.rows(response)
        exposed = response.headers["access-control-expose-headers"].lower()
        self.assertIn("content-disposition", exposed)
        self.assertIn("x-export-rows", exposed)


class PlotExportBundleTests(PlotExportFixture):
    def bundle(self, plots=None, *, layout="2x2"):
        if plots is None:
            plots = [{"slot": 1, "request": self.payload()},
                     {"slot": 3, "request": self.payload(data="original", filter=None)}]
        return self.client.post("/api/plot-export/bundle", json={"layout": layout, "plots": plots})

    def archive(self, response):
        self.assertEqual(response.status_code, 200, response.text[:1000])
        self.assertEqual(response.headers["content-type"], "application/zip")
        self.assertIn("attachment", response.headers["content-disposition"])
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        self.addCleanup(archive.close)
        self.assertIsNone(archive.testzip())
        return archive

    def test_2x2_entries_match_independent_csv_requests_byte_for_byte(self):
        requests = [
            self.payload(),
            self.payload(data="original", filter=None),
            self.payload(data="filtered", filter={"kind": "moving_avg", "window_s": 0.011}),
            self.payload(data="both", filter={"kind": "detrend"}, x_range=[0.03, 0.06]),
        ]
        slots = [1, 3, 7, 9]
        single_files = [self.client.post("/api/plot-export", json=request).content
                        for request in requests]
        response = self.bundle([{"slot": slot, "request": request}
                                for slot, request in zip(slots, requests)])
        archive = self.archive(response)
        names = archive.namelist()
        self.assertEqual(len(names), 4)
        for position, (slot, expected) in enumerate(zip(slots, single_files), 1):
            self.assertTrue(names[position - 1].startswith(f"{position:02d}_slot-{slot}_"))
            self.assertTrue(names[position - 1].endswith(".csv"))
            self.assertFalse(names[position - 1].endswith(".csv.csv"))
            self.assertEqual(archive.read(names[position - 1]), expected)
        self.assertEqual(response.headers["x-export-plots"], "4")
        self.assertEqual(response.headers["x-export-sources"], "4")
        self.assertEqual(int(response.headers["x-export-rows"]),
                         sum(len(list(csv.DictReader(io.StringIO(value.decode()))))
                             for value in single_files))
        self.assertIn("alpha_time-plots_2x2_4-plots.zip",
                      unquote(response.headers["content-disposition"]))

    def test_3x3_keeps_nine_duplicate_variables_and_distinct_filter_results(self):
        kinds = ["despike", "moving_avg", "detrend", "lowpass", "highpass",
                 "bandpass", "bandstop", "despike", "moving_avg"]
        plots = [{"slot": i + 1, "request": self.payload(data="filtered", filter={
            "kind": kind, "f1": 10, "f2": 100, "window_s": 0.025})}
            for i, kind in enumerate(kinds)]
        response = self.bundle(plots, layout="3x3")
        archive = self.archive(response)
        names = archive.namelist()
        self.assertEqual(len(names), 9)
        self.assertEqual(len(set(names)), 9)
        contents = [archive.read(name) for name in names]
        self.assertEqual(contents[0], contents[7])
        self.assertEqual(contents[1], contents[8])
        self.assertNotEqual(contents[0], contents[1])
        self.assertEqual(int(response.headers["x-export-rows"]), 9 * 152)
        self.assertEqual(response.headers["x-export-plots"], "9")
        self.assertIn("3x3_9-plots.zip", unquote(response.headers["content-disposition"]))

    def test_single_selected_slot_in_multi_layout_is_valid(self):
        response = self.bundle([{"slot": 8, "request": self.payload()}], layout="3x3")
        archive = self.archive(response)
        self.assertEqual(len(archive.namelist()), 1)
        self.assertTrue(archive.namelist()[0].startswith("01_slot-8_"))

    def test_layout_slots_and_nested_settings_are_strictly_validated(self):
        request = self.payload()
        cases = [
            ([], "2x2"),
            ([{"slot": i, "request": request} for i in range(1, 6)], "2x2"),
            ([{"slot": 1, "request": request}], "4x4"),
            ([{"slot": 0, "request": request}], "2x2"),
            ([{"slot": 10, "request": request}], "3x3"),
            ([{"slot": True, "request": request}], "2x2"),
            ([{"slot": 1, "request": request}] * 2, "2x2"),
            ([{"slot": 3, "request": request}, {"slot": 1, "request": request}], "2x2"),
            ([{"slot": 1, "request": self.payload(filter=None)}], "2x2"),
            ([{"slot": 1, "request": request, "extra": 1}], "2x2"),
        ]
        for plots, layout in cases:
            with self.subTest(plots=plots, layout=layout):
                response = self.bundle(plots, layout=layout)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertNotIn("content-disposition", response.headers)

    def test_aggregate_rows_count_duplicate_plot_work_before_dsp_or_tempfiles(self):
        plots = [{"slot": i, "request": self.payload()} for i in (1, 2, 3)]
        with patch.object(plot_export, "MAX_EXPORT_ROWS", 400), \
                patch.object(dsp, "filtered_samples") as process, \
                patch.object(plot_export.tempfile, "SpooledTemporaryFile") as temporary:
            response = self.bundle(plots)
        self.assertEqual(response.status_code, 400)
        self.assertIn("Plot 3", response.json()["detail"])
        self.assertIn("max 400", response.json()["detail"])
        process.assert_not_called()
        temporary.assert_not_called()

    def test_aggregate_source_reference_limit_spans_all_selected_slots(self):
        sources = [{"test": "alpha", "tp_id": i} for i in range(65)]
        plots = [{"slot": i, "request": self.payload(sources=sources)} for i in (1, 2)]
        with patch.object(plot_export, "_preflight_export") as preflight:
            response = self.bundle(plots)
        self.assertEqual(response.status_code, 422)
        self.assertIn("130 source references", response.text)
        preflight.assert_not_called()

    def test_growth_after_bundle_preflight_cannot_bypass_aggregate_limit(self):
        source = {"test": "alpha", "tp_id": 7}
        plots = [{"slot": i, "request": self.payload(sources=[source])} for i in (1, 2)]
        prepare = plot_export.prepare_export
        calls = 0

        def grow_after_first(*args, **kwargs):
            nonlocal calls
            result = prepare(*args, **kwargs)
            calls += 1
            if calls == 1:
                store.write_json_atomic(self.tests / "alpha" / "testpoints.json", {
                    "test_points": [{**self.points[0], "end_idx": 220}]})
            return result

        with patch.object(plot_export, "MAX_EXPORT_ROWS", 330), \
                patch.object(plot_export, "prepare_export", grow_after_first):
            response = self.bundle(plots)
        self.assertEqual(response.status_code, 400)
        self.assertIn("Plot 2", response.json()["detail"])
        self.assertIn("337 source rows", response.json()["detail"])
        self.assertNotIn("content-disposition", response.headers)

    def test_invalid_later_source_preflight_prevents_any_processing(self):
        plots = [{"slot": 1, "request": self.payload()},
                 {"slot": 4, "request": self.payload(column="missing")}]
        with patch.object(dsp, "filtered_samples") as process:
            response = self.bundle(plots)
        self.assertEqual(response.status_code, 400)
        self.assertIn("Plot 4", response.json()["detail"])
        process.assert_not_called()

    def test_later_plot_filter_failure_closes_every_temp_and_returns_no_archive(self):
        plots = [{"slot": 1, "request": self.payload()},
                 {"slot": 2, "request": self.payload(filter={"kind": "lowpass", "f1": 9000})}]
        factory = plot_export.tempfile.SpooledTemporaryFile
        created = []

        def tracked(*args, **kwargs):
            output = factory(*args, **kwargs)
            created.append(output)
            return output

        with patch.object(plot_export.tempfile, "SpooledTemporaryFile", tracked):
            response = self.bundle(plots)
        self.assertEqual(response.status_code, 400)
        self.assertIn("Plot 2", response.json()["detail"])
        self.assertNotIn("content-disposition", response.headers)
        self.assertGreaterEqual(len(created), 3)
        self.assertTrue(all(output.closed for output in created))

    def test_zip_copy_failure_closes_current_csv_and_archive(self):
        factory = plot_export.tempfile.SpooledTemporaryFile
        created = []

        def tracked(*args, **kwargs):
            output = factory(*args, **kwargs)
            created.append(output)
            return output

        request = plot_export.PlotExportBundleRequest.model_validate({
            "layout": "2x2", "plots": [{"slot": 1, "request": self.payload()}]})
        with patch.object(plot_export.tempfile, "SpooledTemporaryFile", tracked), \
                patch.object(plot_export.progress, "copy_file", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                plot_export.prepare_bundle(request)
        self.assertEqual(len(created), 2)
        self.assertTrue(all(output.closed for output in created))

    def test_same_test_and_cross_test_scopes_keep_original_ids_and_origins(self):
        beta_time = 2.0 + np.arange(100) / 500.0
        self.write_test("beta", beta_time, np.full(100, 15.0), [{
            "id": 22, "start_idx": 10, "end_idx": 80, "start_s": 2.02}], fs=500.0)
        cross_test = self.payload(sources=[{"test": "alpha", "tp_id": 7},
                                          {"test": "beta", "tp_id": 22}], x_range=[0.01, 0.04])
        full_test = self.payload(data="original", filter=None,
                                 sources=[{"test": "beta", "display": "line"}],
                                 x_range=[2.01, 2.03])
        response = self.bundle([{"slot": 2, "request": cross_test},
                                {"slot": 5, "request": full_test}])
        archive = self.archive(response)
        for entry, request in zip(archive.namelist(), (cross_test, full_test)):
            expected = self.client.post("/api/plot-export", json=request)
            self.assertEqual(expected.status_code, 200)
            self.assertEqual(archive.read(entry), expected.content)
        self.assertIn("multi-test_time-plots", unquote(response.headers["content-disposition"]))

    def test_entry_names_are_flat_safe_and_distinct_after_variable_sanitization(self):
        extra = {"../torque/a": np.arange(240), "..\\torque:a": np.arange(240)}
        self.write_test("alpha", self.time, self.signal, self.points, extra=extra)
        plots = [{"slot": slot, "request": self.payload(column=column, data="original", filter=None)}
                 for slot, column in zip((3, 9), extra)]
        archive = self.archive(self.bundle(plots))
        self.assertEqual(len(set(archive.namelist())), 2)
        for name in archive.namelist():
            self.assertNotIn("/", name)
            self.assertNotIn("\\", name)
            self.assertFalse(name.startswith("."))

    def test_completed_bundle_preserves_source_bytes_and_closes_csvs_and_zip(self):
        before = {str(path): (path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
                  for path in self.tests.rglob("*") if path.is_file()}
        factory = plot_export.tempfile.SpooledTemporaryFile
        created = []

        def tracked(*args, **kwargs):
            output = factory(*args, **kwargs)
            created.append(output)
            return output

        with patch.object(plot_export.tempfile, "SpooledTemporaryFile", tracked):
            self.archive(self.bundle())
        after = {str(path): (path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
                 for path in self.tests.rglob("*") if path.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(len(created), 3)
        self.assertTrue(all(output.closed for output in created))

    def test_archive_cleanup_survives_cancel_and_failure_before_headers(self):
        request = plot_export.PlotExportBundleRequest.model_validate({
            "layout": "2x2", "plots": [{"slot": 1, "request": self.payload()}]})
        for fail_at in ("http.response.start", "http.response.body"):
            with self.subTest(fail_at=fail_at):
                output = io.BytesIO(b"staged ZIP bytes")
                with patch.object(plot_export, "prepare_bundle", return_value=(output, 16, 1, 1)):
                    response = plot_export.api_plot_export_bundle(request)

                async def fail(message):
                    if message["type"] == fail_at:
                        raise asyncio.CancelledError()

                async def run():
                    try:
                        await response({"type": "http", "asgi": {"spec_version": "2.4"}}, None, fail)
                    except asyncio.CancelledError:
                        pass

                asyncio.run(run())
                self.assertTrue(output.closed)

    def test_cors_exposes_bundle_plot_count_and_attachment_filename(self):
        response = self.client.post("/api/plot-export/bundle", json={
            "layout": "2x2", "plots": [{"slot": 3, "request": self.payload()}]},
            headers={"Origin": "http://127.0.0.1:3000"})
        self.archive(response)
        exposed = response.headers["access-control-expose-headers"].lower()
        self.assertIn("x-export-plots", exposed)
        self.assertIn("content-disposition", exposed)
