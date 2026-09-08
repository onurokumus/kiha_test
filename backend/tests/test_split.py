import unittest

import numpy as np
import polars as pl
from fastapi.testclient import TestClient

from app import main, split, store
from ._base import DataDirTestCase


class AutoSplitTests(DataDirTestCase):
    def make_test(self, columns, fs=10.0):
        directory = self.tests / "alpha"
        directory.mkdir()
        n_rows = len(next(iter(columns.values())))
        data = {"time": np.arange(n_rows) / fs, **columns}
        pl.DataFrame(data).write_parquet(directory / "data.parquet")
        store.write_json_atomic(directory / "meta.json", {
            "name": "alpha", "time_column": "time", "fs_hz": fs,
            "columns": list(data), "n_rows": n_rows, "t_start": 0.0,
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


if __name__ == "__main__":
    unittest.main()
