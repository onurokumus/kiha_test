import unittest
from contextlib import contextmanager
from unittest.mock import patch

import numpy as np
import polars as pl
from fastapi.testclient import TestClient

from app import main, split, store
from ._base import DataDirTestCase


class AutoSplitTests(DataDirTestCase):
    def make_test(self, columns, fs=10.0, t_start=0.0, times=None):
        directory = self.tests / "alpha"
        directory.mkdir()
        n_rows = len(next(iter(columns.values())))
        data = {"time": (t_start + np.arange(n_rows) / fs
                         if times is None else times), **columns}
        pl.DataFrame(data).write_parquet(directory / "data.parquet")
        store.write_json_atomic(directory / "meta.json", {
            "name": "alpha", "time_column": "time", "fs_hz": fs,
            "columns": list(data), "n_rows": n_rows, "t_start": t_start,
        })

    def test_runs_exactly_at_minimum_duration_are_kept(self):
        self.make_test({"id": [1.0] * 10 + [2.0] * 9 + [3.0] * 10})
        runs = split.autosplit("alpha", "id", min_len_s=1.0)
        self.assertEqual(
            [(run["start_idx"], run["end_idx"]) for run in runs],
            [(0, 10), (19, 29)],
        )
        self.assertEqual(runs[-1]["end_s"], 2.9)

    def test_candidates_ignore_gaps_and_reject_fractional_large_numbers(self):
        self.make_test({
            "id": [1.0, 1.0, np.nan, 2.0, 2.0, np.inf],
            "fractional": [100_000.1] * 3 + [100_001.2] * 3,
        })
        self.assertEqual(split.id_candidates("alpha"), [
            {"col": "id", "n_unique": 2},
        ])

    def test_non_finite_values_separate_runs_without_becoming_points(self):
        self.make_test({"id": [1.0, 1.0, np.inf, np.inf, 2.0, 2.0]})
        runs = split.autosplit("alpha", "id", min_len_s=0.1)
        self.assertEqual([run["label"] for run in runs], ["id=1", "id=2"])

    def test_time_column_can_be_requested_without_duplicate_selection(self):
        self.make_test({"id": [1.0] * 10})
        self.assertEqual(split.autosplit("alpha", "time"), [])

    def test_api_rejects_invalid_minimum_duration(self):
        self.make_test({"id": [1.0] * 10})
        client = TestClient(main.app)
        for value in ("nan", "inf", "-inf", "-1"):
            with self.subTest(value=value):
                response = client.post(
                    "/api/tests/alpha/split/auto",
                    params={"col": "id", "min_len_s": value},
                )
                self.assertEqual(response.status_code, 422)

    def test_preview_single_variable_preserves_legacy_proposal(self):
        self.make_test({"id": [0.0] * 8 + [1.0] * 8 + [2.0] * 8}, fs=8)
        preview = split.preview_autosplit("alpha", ["id"])
        self.assertEqual(preview["test_points"], split.autosplit("alpha", "id"))
        self.assertEqual(preview["method"], "value_changes")
        self.assertEqual(preview["columns"], ["id"])
        self.assertTrue(preview["ignore_zero"])
        self.assertEqual(preview["min_len_s"], 1)
        self.assertEqual(preview["sample_count"], 24)
        self.assertEqual(preview["fs_hz"], 8)
        self.assertEqual(preview["excluded"], {
            "missing_samples": 0, "zero_samples": 8, "short_runs": 0})

    def test_preview_any_changed_variable_splits_repeated_combinations(self):
        self.make_test({
            "id": [1.0] * 6 + [2.0] * 2 + [1.0] * 2,
            "mode": [10.0] * 2 + [20.0] * 2 + [10.0] * 6,
        })
        preview = split.preview_autosplit("alpha", ["id", "mode"], min_len_s=0.2)
        self.assertEqual(
            [(p["start_idx"], p["end_idx"], p["label"]) for p in preview["test_points"]],
            [(0, 2, "id=1, mode=10"), (2, 4, "id=1, mode=20"),
             (4, 6, "id=1, mode=10"), (6, 8, "id=2, mode=10"),
             (8, 10, "id=1, mode=10")])
        self.assertEqual([p["id"] for p in preview["test_points"]], [1, 2, 3, 4, 5])
        self.assertEqual(preview["test_points"][-1]["end_s"], 1.0)

    def test_preview_missing_and_zero_rows_break_runs_with_disjoint_counts(self):
        self.make_test({
            "id": [1.0, 1.0, np.nan, None, 1.0, 1.0, 0.0, 1.0, 1.0, np.inf, 1.0],
            "mode": [2.0, 2.0, 0.0, 2.0, 2.0, 2.0, 2.0, 0.0, 2.0, -np.inf, 2.0],
        })
        preview = split.preview_autosplit("alpha", ["id", "mode"], min_len_s=0.2)
        self.assertEqual([(p["start_idx"], p["end_idx"]) for p in preview["test_points"]],
                         [(0, 2), (4, 6)])
        self.assertEqual(preview["excluded"], {
            "missing_samples": 3, "zero_samples": 2, "short_runs": 2})
        include_zero = split.preview_autosplit(
            "alpha", ["id", "mode"], ignore_zero=False, min_len_s=0)
        self.assertEqual(include_zero["excluded"], {
            "missing_samples": 3, "zero_samples": 0, "short_runs": 0})
        self.assertIn("id=0, mode=2", [p["label"] for p in include_zero["test_points"]])
        self.assertIn("id=1, mode=0", [p["label"] for p in include_zero["test_points"]])

    def test_preview_exact_minimum_in_late_run_and_nonzero_origin(self):
        self.make_test({"id": [0.0] * 100_003 + [1.0] * 10 + [2.0] * 9}, t_start=8123.4)
        preview = split.preview_autosplit("alpha", ["id"], min_len_s=1)
        self.assertEqual(preview["excluded"]["short_runs"], 1)
        self.assertEqual(len(preview["test_points"]), 1)
        point = preview["test_points"][0]
        self.assertEqual((point["start_idx"], point["end_idx"]), (100_003, 100_013))
        self.assertEqual(round((point["start_s"] - 8123.4) * 10), 100_003)
        self.assertEqual(round((point["end_s"] - 8123.4) * 10), 100_013)

    def test_preview_preserves_native_indices_and_raw_jittered_boundaries(self):
        times = [51.2345678901, 51.3361234567, 51.4323456789, 51.5356789012]
        self.make_test({"id": [1.0, 1.0, 2.0, 2.0]}, times=times, t_start=times[0])
        points = split.preview_autosplit("alpha", ["id"], min_len_s=0.2)["test_points"]
        self.assertEqual(points[0]["start_s"], times[0])
        self.assertEqual(points[0]["end_s"], times[2])
        self.assertEqual(points[1]["start_s"], times[2])
        self.assertEqual(points[1]["end_s"], times[-1] + 0.1)
        self.assertEqual([(p["start_idx"], p["end_idx"]) for p in points], [(0, 2), (2, 4)])

    def test_preview_compares_exact_values_without_label_rounding(self):
        self.make_test({"rpm": [100_000.0000001] * 2 + [100_000.0000002] * 2,
                        "mode, rig": [1.0] * 4})
        points = split.preview_autosplit(
            "alpha", ["mode, rig", "rpm"], min_len_s=0.2)["test_points"]
        self.assertEqual(len(points), 2)
        self.assertEqual(points[0]["label"], "mode, rig=1, rpm=100000.0000001")
        self.assertEqual(points[1]["label"], "mode, rig=1, rpm=100000.0000002")

    def test_preview_zero_result_is_an_explicit_empty_proposal(self):
        self.make_test({"id": [0.0, np.nan, np.inf], "mode": [1.0] * 3})
        preview = split.preview_autosplit("alpha", ["id", "mode"])
        self.assertEqual(preview["test_points"], [])
        self.assertEqual(preview["excluded"], {
            "missing_samples": 2, "zero_samples": 1, "short_runs": 0})

    def test_preview_api_is_structured_and_never_writes(self):
        self.make_test({"mode, rig": [1.0] * 10, "id": [2.0] * 10})
        directory = self.tests / "alpha"
        store.write_json_atomic(directory / "testpoints.json", {"test_points": [{"id": 93}]})
        before = {p.name: p.read_bytes() for p in directory.iterdir()}
        response = TestClient(main.app).post("/api/tests/alpha/split/preview", json={
            "columns": ["mode, rig", "id"], "ignore_zero": False, "min_len_s": 0})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["columns"], ["mode, rig", "id"])
        self.assertEqual(result["test_points"][0]["label"], "mode, rig=1, id=2")
        self.assertEqual({p.name: p.read_bytes() for p in directory.iterdir()}, before)

    def test_preview_api_rejects_invalid_payload_and_unknown_variables(self):
        self.make_test({"id": [1.0] * 10})
        client = TestClient(main.app)
        for payload, expected in [
            ({}, 422), ({"columns": []}, 422), ({"columns": [""]}, 422),
            ({"columns": [" "]}, 400), ({"columns": ["id"] * 10}, 422),
            ({"columns": [1]}, 422), ({"columns": ["missing"]}, 400),
            ({"columns": ["time"]}, 400), ({"columns": ["id", "id"]}, 400),
            ({"columns": ["id"], "ignore_zero": "yes"}, 422),
            ({"columns": ["id"], "extra": 1}, 422),
            *[({"columns": ["id"], "min_len_s": v}, 422)
              for v in ("nan", "inf", "-inf", -1)],
        ]:
            with self.subTest(payload=payload):
                response = client.post("/api/tests/alpha/split/preview", json=payload)
                self.assertEqual(response.status_code, expected, response.text)
        response = client.post("/api/tests/missing/split/preview", json={"columns": ["id"]})
        self.assertEqual(response.status_code, 404)

    def test_preview_output_cap_rejects_instead_of_truncating(self):
        self.make_test({"id": np.arange(split.MAX_SPLIT_POINTS + 1, dtype=float) + 1})
        response = TestClient(main.app).post("/api/tests/alpha/split/preview", json={
            "columns": ["id"], "min_len_s": 0})
        self.assertEqual(response.status_code, 400)
        self.assertIn("1,000", response.json()["detail"])
        self.assertIn("minimum duration", response.json()["detail"])
        self.assertEqual(len(split.autosplit("alpha", "id", min_len_s=0)),
                         split.MAX_SPLIT_POINTS + 1)

    def test_preview_output_cap_accepts_exact_limit(self):
        self.make_test({"id": np.arange(split.MAX_SPLIT_POINTS, dtype=float) + 1})
        points = split.preview_autosplit("alpha", ["id"], min_len_s=0)["test_points"]
        self.assertEqual(len(points), split.MAX_SPLIT_POINTS)

    def test_preview_api_rejects_bad_sample_rates_and_timestamps(self):
        self.make_test({"id": [1.0, 1.0]})
        directory = self.tests / "alpha"
        meta = store.get_meta("alpha")
        client = TestClient(main.app)
        for fs in (None, 0, -1, "nan", "inf"):
            with self.subTest(fs=fs):
                store.write_json_atomic(directory / "meta.json", {**meta, "fs_hz": fs})
                response = client.post("/api/tests/alpha/split/preview", json={"columns": ["id"]})
                self.assertEqual(response.status_code, 400)
        store.write_json_atomic(directory / "meta.json", meta)
        for times in ([0.0, np.nan], [0.0, np.inf], [0.1, 0.0]):
            with self.subTest(times=times):
                pl.DataFrame({"time": times, "id": [1.0, 1.0]}).write_parquet(directory / "data.parquet")
                response = client.post("/api/tests/alpha/split/preview", json={"columns": ["id"]})
                self.assertEqual(response.status_code, 400)

    def test_preview_rejects_collapsed_clock_boundaries_without_rounding(self):
        self.make_test({"id": [1.0, 2.0, 2.0]}, times=[0.0, 0.0, 0.2])
        with self.assertRaisesRegex(ValueError, "source-clock resolution"):
            split.preview_autosplit("alpha", ["id"], min_len_s=0)

    def test_preview_busy_precheck_does_not_wait_for_data_lock(self):
        self.make_test({"id": [1.0] * 10})
        store.write_json_atomic(self.tests / "alpha" / "status.json", {"status": "rebuilding"})
        with patch.object(main, "data_read", side_effect=AssertionError("must not wait")):
            response = TestClient(main.app).post("/api/tests/alpha/split/preview", json={"columns": ["id"]})
        self.assertEqual(response.status_code, 409)

    def test_preview_rechecks_busy_status_under_data_lock(self):
        self.make_test({"id": [1.0] * 10})

        @contextmanager
        def become_busy(_name):
            store.write_json_atomic(self.tests / "alpha" / "status.json", {"status": "rebuilding"})
            yield

        with patch.object(main, "data_read", become_busy), \
                patch.object(split, "preview_autosplit", side_effect=AssertionError("must not read")):
            response = TestClient(main.app).post("/api/tests/alpha/split/preview", json={"columns": ["id"]})
        self.assertEqual(response.status_code, 409)


if __name__ == "__main__":
    unittest.main()
