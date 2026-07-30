import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import polars as pl
import pyarrow.parquet as pq
from fastapi import BackgroundTasks, HTTPException
from pydantic import ValidationError

from app import config, edit, formula, ingest, main, recipes, store
from app.main import (
    EditOps,
    FormulaPreviewRequest,
    FormulaRecipePut,
    FormulaSpec,
)
from ._base import DataDirTestCase


FS = 4.0
N = 32


def _evaluate(expression: str, frame: pl.DataFrame) -> list[float]:
    parsed = formula.parse_expression(expression)
    return frame.with_columns(
        parsed.polars_expr.alias("result"))["result"].to_list()


class FormulaParserTests(unittest.TestCase):
    def test_arithmetic_comparisons_conditionals_and_horizontal_functions(self):
        frame = pl.DataFrame({
            "a": [-4.0, 1.0],
            "b": [3.0, 0.0],
        })
        values = _evaluate(
            "IF(({a} < 0) or ({b} == 5), "
            "max(abs({a}), 2) + min({b}, 4), "
            "clip(sqrt({a}) ** 2, 0, 3))",
            frame,
        )
        self.assertEqual(values, [7.0, 1.0])

        boolean_values = _evaluate(
            "not ({a} >= 0 and {b} != 0)", frame)
        self.assertEqual(boolean_values, [1.0, 1.0])

    def test_math_functions_constants_and_numeric_dtype(self):
        frame = pl.DataFrame({"a": [1.0, 2.0]})
        values = _evaluate(
            "log(exp(1)) + log10(100) + sin(pi / 2) + "
            "cos(0) + tan(0) + e - e",
            frame,
        )
        np.testing.assert_allclose(values, [5.0, 5.0], atol=1e-12)

        parsed = formula.parse_expression("{a} > 1")
        result = frame.with_columns(
            parsed.polars_expr.alias("result"))["result"]
        self.assertEqual(result.dtype, pl.Float64)
        self.assertEqual(result.to_list(), [0.0, 1.0])

    def test_column_references_allow_spaces_and_dedupe_dependencies(self):
        parsed = formula.parse_expression(
            " {a} + { a } + {sensor rpm} ")
        self.assertEqual(parsed.expression, "{a} + { a } + {sensor rpm}")
        self.assertEqual(parsed.dependencies, ("a", "sensor rpm"))
        frame = pl.DataFrame({
            "a": [1.0],
            "sensor rpm": [3.0],
        })
        value = frame.with_columns(
            parsed.polars_expr.alias("result"))["result"][0]
        self.assertEqual(value, 5.0)

    def test_non_finite_results_are_normalized_to_nan(self):
        frame = pl.DataFrame({
            "numerator": [1.0, 0.0, 4.0],
            "denominator": [0.0, 0.0, 2.0],
        })
        values = np.asarray(
            _evaluate("{numerator} / {denominator}", frame),
            dtype=np.float64,
        )
        self.assertTrue(np.isnan(values[0]))  # +inf before normalization
        self.assertTrue(np.isnan(values[1]))
        self.assertEqual(values[2], 2.0)
        self.assertFalse(np.isinf(values).any())

    def test_unsafe_or_unsupported_syntax_is_rejected(self):
        expressions = [
            "__import__('os').system('echo unsafe')",
            "(1).__class__",
            "[1, 2][0]",
            "{a}[0]",
            "(lambda value: value)(1)",
            "'text'",
            "sum({a})",
            "{a} // 2",
            "{a} << 1",
            "{a} if true else 0",
            "clip(value={a}, lower_bound=0, upper_bound=1)",
        ]
        for expression in expressions:
            with self.subTest(expression=expression):
                with self.assertRaises(formula.FormulaError):
                    formula.parse_expression(expression)

    def test_invalid_references_and_complexity_limits_are_rejected(self):
        for expression in ("", "{}", "{a", "a}", "{{a}}"):
            with self.subTest(expression=expression):
                with self.assertRaises(formula.FormulaError):
                    formula.parse_expression(expression)

        with self.assertRaisesRegex(
                formula.FormulaError, "exceeds.*characters"):
            formula.parse_expression("1" * (
                formula.MAX_EXPRESSION_LENGTH + 1))
        with self.assertRaisesRegex(formula.FormulaError, "too complex"):
            formula.parse_expression(" + ".join(["1"] * 130))

    def test_batch_chaining_and_explicit_overwrite(self):
        compiled = formula.compile_formula_batch(
            [
                {"name": "sum", "expression": "{a} + {b}"},
                {"name": "scaled", "expression": "{sum} * 2"},
                {
                    "name": "a",
                    "expression": "{scaled} - {b}",
                    "replace": True,
                },
            ],
            ["time_s", "a", "b"],
            "time_s",
        )
        self.assertEqual(
            [item.dependencies for item in compiled],
            [("a", "b"), ("sum",), ("scaled", "b")],
        )
        self.assertFalse(compiled[0].replaces_existing)
        self.assertTrue(compiled[2].replaces_existing)

        frame = pl.DataFrame({
            "time_s": [0.0, 1.0],
            "a": [1.0, 2.0],
            "b": [3.0, 4.0],
        })
        for item in compiled:
            frame = frame.with_columns(item.polars_expr)
        self.assertEqual(frame["sum"].to_list(), [4.0, 6.0])
        self.assertEqual(frame["scaled"].to_list(), [8.0, 12.0])
        self.assertEqual(frame["a"].to_list(), [5.0, 8.0])

    def test_batch_rejects_unknown_forward_self_overwrite_and_time_target(self):
        cases = [
            (
                [{"name": "new", "expression": "{missing} + 1"}],
                "unknown column",
            ),
            (
                [
                    {"name": "later", "expression": "{earlier} + 1"},
                    {"name": "earlier", "expression": "{a} + 1"},
                ],
                "earlier",
            ),
            (
                [{"name": "self", "expression": "{self} + 1"}],
                "unknown column",
            ),
            (
                [{"name": "a", "expression": "{a} + 1"}],
                "replace=true",
            ),
            (
                [{"name": "time_s", "expression": "{a} + 1",
                  "replace": True}],
                "time column",
            ),
        ]
        for specs, message in cases:
            with self.subTest(specs=specs):
                with self.assertRaisesRegex(
                        formula.FormulaError, message):
                    formula.compile_formula_batch(
                        specs, ["time_s", "a", "b"], "time_s")

        too_many = [
            {"name": f"v{index}", "expression": "1"}
            for index in range(formula.MAX_FORMULAS + 1)
        ]
        with self.assertRaisesRegex(formula.FormulaError, "cannot exceed"):
            formula.compile_formula_batch(
                too_many, ["time_s", "a"], "time_s")

    def test_provenance_rewrite_marks_missing_dependencies_and_drops_targets(self):
        existing = [{
            "name": "power",
            "expression": "{torque} * {rpm}",
            "dependencies": ["torque", "rpm"],
            "created_at": "before",
            "updated_at": "before",
        }]
        renamed = formula.rewrite_provenance(
            existing, rename={"torque": "torque_nm", "power": "power_w"})
        self.assertEqual(renamed[0]["name"], "power_w")
        self.assertEqual(
            renamed[0]["expression"], "{torque_nm} * {rpm}")
        self.assertEqual(
            renamed[0]["dependencies"], ["torque_nm", "rpm"])

        source_dropped = formula.rewrite_provenance(
            renamed, drop=["rpm"])
        self.assertEqual(
            source_dropped[0]["missing_dependencies"], ["rpm"])
        self.assertEqual(
            formula.rewrite_provenance(source_dropped, drop=["power_w"]),
            [],
        )


class FormulaDataTests(DataDirTestCase):
    def setUp(self):
        super().setUp()
        self.t = np.arange(N, dtype=np.float64) / FS
        self.a = np.arange(N, dtype=np.float64) + 1.0
        self.b = np.arange(N, dtype=np.float64) * 0.5
        self.denominator = np.full(N, 2.0, dtype=np.float64)
        self.denominator[0] = 0.0
        csv = self.root / "formula.csv"
        pl.DataFrame({
            "time_s": self.t,
            "a": self.a,
            "b": self.b,
            "denominator": self.denominator,
        }).write_csv(csv)
        ingest._ingest_csv(csv, "alpha")
        store.write_json_atomic(
            self.tests / "alpha" / "testpoints.json",
            {
                "version": 1,
                "test": "alpha",
                "fs_hz": FS,
                "test_points": [{
                    "id": 1,
                    "name": "TP-01",
                    "label": "all",
                    "start_s": 0.0,
                    "end_s": N / FS,
                    "start_idx": 0,
                    "end_idx": N,
                    "notes": "",
                }],
            },
        )

    @staticmethod
    def specs():
        return [
            {
                "name": "sum",
                "expression": "{a} + {b}",
                "replace": False,
            },
            {
                "name": "double_sum",
                "expression": "{sum} * 2",
                "replace": False,
            },
        ]

    def test_preview_uses_evenly_distributed_rows_and_chained_values(self):
        payload = FormulaPreviewRequest(
            formulas=self.specs(),
            sample_size=5,
        )
        preview = main.api_preview_formulas("alpha", payload)
        indices = np.linspace(
            0, N - 1, num=5, dtype=np.int64).tolist()
        self.assertTrue(preview["valid"])
        self.assertEqual(preview["sample_size"], 5)
        self.assertEqual(preview["row_indices"], indices)
        np.testing.assert_allclose(
            preview["time"], self.t[indices], atol=1e-12)
        np.testing.assert_allclose(
            preview["formulas"][0]["values"],
            (self.a[indices] + self.b[indices]),
            atol=1e-12,
        )
        np.testing.assert_allclose(
            preview["formulas"][1]["values"],
            2 * (self.a[indices] + self.b[indices]),
            atol=1e-12,
        )
        self.assertEqual(
            preview["formulas"][1]["dependencies"], ["sum"])
        self.assertEqual(
            preview["formulas"][1]["stats"]["valid_count"], 5)

    def test_preview_returns_json_safe_missing_values(self):
        preview = main.api_preview_formulas(
            "alpha",
            FormulaPreviewRequest(
                formulas=[{
                    "name": "ratio",
                    "expression": "{a} / {denominator}",
                }],
                sample_size=N,
            ),
        )
        result = preview["formulas"][0]
        self.assertIsNone(result["values"][0])
        self.assertEqual(result["stats"]["nan_count"], 1)
        self.assertEqual(result["stats"]["valid_count"], N - 1)

    def test_formula_only_is_valid_edit_but_mixed_edits_are_rejected(self):
        for legacy in (
            {"rename": {"a": "renamed"}},
            {"drop": ["b"]},
            {"trim_t0": 1.0},
            {"nan_policy": "keep_gaps"},
        ):
            with self.subTest(legacy=legacy):
                with self.assertRaises(HTTPException) as caught:
                    main.api_edit(
                        "alpha",
                        EditOps(formulas=self.specs(), **legacy),
                        BackgroundTasks(),
                    )
                self.assertEqual(caught.exception.status_code, 400)
                self.assertIn(
                    "cannot be combined", str(caught.exception.detail))
                self.assertEqual(
                    store.get_status("alpha")["status"], "ready")

        tasks = BackgroundTasks()
        result = main.api_edit(
            "alpha", EditOps(formulas=self.specs()), tasks)
        self.assertEqual(result["status"], "rebuilding")
        self.assertEqual(len(tasks.tasks), 1)

    def test_formula_endpoint_validation_rejects_schema_errors_and_extra_keys(self):
        cases = [
            (
                [{"name": "unknown", "expression": "{nope}"}],
                "unknown column",
            ),
            (
                [{"name": "a", "expression": "{a} + 1"}],
                "replace=true",
            ),
            (
                [{
                    "name": "time_s",
                    "expression": "{a}",
                    "replace": True,
                }],
                "time column",
            ),
        ]
        for specs, message in cases:
            with self.subTest(specs=specs):
                with self.assertRaises(HTTPException) as caught:
                    main.api_edit(
                        "alpha", EditOps(formulas=specs),
                        BackgroundTasks())
                self.assertEqual(caught.exception.status_code, 400)
                self.assertIn(message, str(caught.exception.detail))
                self.assertEqual(
                    store.get_status("alpha")["status"], "ready")

        with self.assertRaises(ValidationError):
            FormulaSpec(
                name="x",
                expression="{a}",
                unexpected="rejected",
            )
        with self.assertRaises(ValidationError):
            EditOps(unexpected="rejected")

    def test_full_rebuild_materializes_columns_pyramid_window_stats_and_metadata(
            self):
        # Populate a stale stats sidecar first; every data rewrite must remove it.
        store.tp_stats("alpha", "a")
        stats_path = self.tests / "alpha" / "tp_stats.json"
        self.assertTrue(stats_path.is_file())

        specs = [
            *self.specs(),
            {
                "name": "ratio",
                "expression": "{a} / {denominator}",
            },
        ]
        edit._rebuild("alpha", {"formulas": specs})

        self.assertEqual(store.get_status("alpha")["status"], "ready")
        self.assertFalse(stats_path.exists())
        meta = store.get_meta("alpha")
        self.assertEqual(
            meta["columns"],
            [
                "time_s",
                "a",
                "b",
                "denominator",
                "sum",
                "double_sum",
                "ratio",
            ],
        )
        self.assertEqual(meta["n_columns"], 7)
        self.assertEqual(meta["n_rows"], N)
        self.assertEqual(meta["nan_counts"]["ratio"], 1)

        provenance = {
            item["name"]: item for item in meta["derived_variables"]
        }
        self.assertEqual(set(provenance), {
            "sum", "double_sum", "ratio"})
        self.assertEqual(provenance["sum"]["dependencies"], ["a", "b"])
        self.assertEqual(
            provenance["double_sum"]["dependencies"], ["sum"])
        self.assertFalse(provenance["sum"]["replaced_existing"])
        self.assertIn("created_at", provenance["sum"])
        self.assertIn("updated_at", provenance["sum"])

        data = pl.read_parquet(
            self.tests / "alpha" / "data.parquet")
        self.assertEqual(data["sum"].dtype, pl.Float64)
        np.testing.assert_allclose(
            data["a"].to_numpy(), self.a, atol=0)
        np.testing.assert_allclose(
            data["sum"].to_numpy(), self.a + self.b, atol=1e-12)
        np.testing.assert_allclose(
            data["double_sum"].to_numpy(),
            2 * (self.a + self.b),
            atol=1e-12,
        )
        ratio = data["ratio"].to_numpy()
        self.assertTrue(np.isnan(ratio[0]))
        self.assertFalse(np.isinf(ratio).any())
        np.testing.assert_allclose(
            ratio[1:], self.a[1:] / self.denominator[1:],
            atol=1e-12,
        )

        with pq.ParquetFile(
                self.tests / "alpha" / "pyramid" / "L16.parquet") as pf:
            pyramid_columns = pf.schema_arrow.names
        for name in ("sum", "double_sum", "ratio"):
            self.assertIn(f"{name}__min", pyramid_columns)
            self.assertIn(f"{name}__max", pyramid_columns)

        window = store.read_window(
            "alpha", ["sum", "ratio"], None, None, px=300)
        self.assertEqual(window["mode"], "raw")
        self.assertEqual(
            window["series"]["sum"][1],
            round(float(self.a[1] + self.b[1]), 6),
        )
        self.assertIsNone(window["series"]["ratio"][0])

        stats = store.tp_stats("alpha", "double_sum")
        self.assertEqual(len(stats), 1)
        self.assertEqual(stats[0]["n"], N)
        self.assertEqual(stats[0]["n_valid"], N)
        self.assertAlmostEqual(
            stats[0]["mean"],
            round(float((2 * (self.a + self.b)).mean()), 6),
            places=6,
        )

    def test_rebuild_rename_updates_derived_provenance_and_storage(self):
        edit._rebuild("alpha", {"formulas": self.specs()})
        first_meta = store.get_meta("alpha")
        created_at = {
            item["name"]: item["created_at"]
            for item in first_meta["derived_variables"]
        }

        edit._rebuild(
            "alpha",
            {"rename": {
                "a": "input_a",
                "sum": "total",
            }},
        )
        meta = store.get_meta("alpha")
        self.assertIn("input_a", meta["columns"])
        self.assertIn("total", meta["columns"])
        self.assertNotIn("a", meta["columns"])
        self.assertNotIn("sum", meta["columns"])

        provenance = {
            item["name"]: item for item in meta["derived_variables"]
        }
        self.assertEqual(provenance["total"]["expression"], "{input_a} + {b}")
        self.assertEqual(
            provenance["total"]["dependencies"], ["input_a", "b"])
        self.assertEqual(
            provenance["total"]["created_at"], created_at["sum"])
        self.assertEqual(
            provenance["double_sum"]["expression"], "{total} * 2")
        self.assertEqual(
            provenance["double_sum"]["dependencies"], ["total"])

        window = store.read_window(
            "alpha", ["input_a", "total"], None, None, px=300)
        self.assertGreater(len(window["t"]), 0)
        self.assertEqual(
            window["series"]["total"][0],
            round(float(self.a[0] + self.b[0]), 6),
        )


class FormulaRecipeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data_dir = Path(self.temp.name)
        patcher = patch.object(config, "DATA_DIR", self.data_dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def payload(description: str = "Power calculation"):
        return FormulaRecipePut(
            description=description,
            formulas=[
                {
                    "name": "power_w",
                    "expression": "{torque_nm} * {rpm} * 2 * pi / 60",
                },
                {
                    "name": "power_kw",
                    "expression": "{power_w} / 1000",
                },
            ],
        )

    def test_recipe_crud_is_persistent_sorted_and_preserves_created_at(self):
        created = main.api_put_formula_recipe(
            " Mechanical power ", self.payload())
        self.assertEqual(created["name"], "Mechanical power")
        self.assertEqual(created["description"], "Power calculation")
        self.assertEqual(created["formulas"][0]["name"], "power_w")
        self.assertFalse(created["formulas"][0]["replace"])

        main.api_put_formula_recipe(
            "alpha recipe", self.payload("Alphabetically first"))
        listed = main.api_list_formula_recipes()
        self.assertEqual(listed["version"], 1)
        self.assertEqual(
            [item["name"] for item in listed["recipes"]],
            ["alpha recipe", "Mechanical power"],
        )
        self.assertEqual(
            main.api_get_formula_recipe("Mechanical power"), created)

        updated = main.api_put_formula_recipe(
            "Mechanical power", self.payload("Updated"))
        self.assertEqual(updated["description"], "Updated")
        self.assertEqual(updated["created_at"], created["created_at"])
        self.assertIn("updated_at", updated)

        recipe_path = self.data_dir / recipes.RECIPE_FILE_NAME
        persisted = json.loads(recipe_path.read_text(encoding="utf-8"))
        self.assertEqual(persisted, main.api_list_formula_recipes())

        deleted = main.api_delete_formula_recipe("Mechanical power")
        self.assertEqual(
            deleted, {"ok": True, "name": "Mechanical power"})
        self.assertIsNone(recipes.get_recipe("Mechanical power"))
        with self.assertRaises(HTTPException) as caught:
            main.api_delete_formula_recipe("Mechanical power")
        self.assertEqual(caught.exception.status_code, 404)

    def test_recipe_validation_rejects_bad_names_formulas_and_extra_fields(self):
        with self.assertRaises(HTTPException) as bad_name:
            main.api_put_formula_recipe(
                "../unsafe", self.payload())
        self.assertEqual(bad_name.exception.status_code, 400)

        with self.assertRaises(HTTPException) as bad_formula:
            main.api_put_formula_recipe(
                "bad formula",
                FormulaRecipePut(
                    formulas=[{
                        "name": "x",
                        "expression": "__import__('os')",
                    }],
                ),
            )
        self.assertEqual(bad_formula.exception.status_code, 400)

        with self.assertRaises(ValidationError):
            FormulaRecipePut(
                formulas=[{"name": "x", "expression": "1"}],
                unexpected="rejected",
            )


if __name__ == "__main__":
    unittest.main()
