"""Safe Tecplot-style derived-variable formulas.

The public formula shape is ``{"name": ..., "expression": ..., "replace":
false}``.  Column names in the expression are written in braces, for example::

    {"name": "power_w",
     "expression": "{torque_nm} * {rpm} * 2 * pi / 60"}

Expressions are parsed with :mod:`ast` and translated node-by-node into Polars
expressions.  They are never passed to Python ``eval`` (or to any other dynamic
code execution mechanism).
"""

from __future__ import annotations

import ast
import math
import operator
import re
from dataclasses import dataclass
from functools import reduce
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq


MAX_EXPRESSION_LENGTH = 4096
MAX_AST_NODES = 256
MAX_FORMULAS = 64
COLUMN_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")
_COLUMN_REFERENCE_RE = re.compile(r"\{([^{}]+)\}")


class FormulaError(ValueError):
    """A formula cannot be parsed or applied to the current test schema."""


@dataclass(frozen=True)
class ParsedExpression:
    expression: str
    dependencies: tuple[str, ...]
    polars_expr: pl.Expr


@dataclass(frozen=True)
class CompiledFormula:
    name: str
    expression: str
    replace: bool
    dependencies: tuple[str, ...]
    replaces_existing: bool
    polars_expr: pl.Expr


def _spec_value(spec: Any, key: str, default: Any = None) -> Any:
    if isinstance(spec, Mapping):
        return spec.get(key, default)
    return getattr(spec, key, default)


def validate_column_name(name: str) -> None:
    if not isinstance(name, str):
        raise FormulaError("formula target name must be a string")
    if not name or not COLUMN_NAME_RE.fullmatch(name):
        raise FormulaError(
            f"invalid formula target '{name}': use letters, digits, '_', "
            "'.', '-'")


def _replace_column_references(
    expression: str,
) -> tuple[str, dict[str, str], tuple[str, ...]]:
    """Replace ``{real column}`` tokens with parser-safe identifiers."""
    if not isinstance(expression, str) or not expression.strip():
        raise FormulaError("formula expression cannot be empty")
    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise FormulaError(
            f"formula expression exceeds {MAX_EXPRESSION_LENGTH} characters")

    identifiers: dict[str, str] = {}
    dependency_order: list[str] = []

    def replace(match: re.Match[str]) -> str:
        column = match.group(1).strip()
        if not column:
            raise FormulaError("empty column reference '{}' is not allowed")
        identifier = f"__formula_column_{len(identifiers)}"
        identifiers[identifier] = column
        if column not in dependency_order:
            dependency_order.append(column)
        return identifier

    substituted = _COLUMN_REFERENCE_RE.sub(replace, expression.strip())
    if "{" in substituted or "}" in substituted:
        raise FormulaError(
            "invalid column reference: wrap each complete column name in "
            "matching braces, for example {rpm}")
    return substituted, identifiers, tuple(dependency_order)


class _ExpressionCompiler:
    """Translate a tightly allowlisted Python expression AST to Polars."""

    def __init__(self, identifiers: Mapping[str, str]) -> None:
        self.identifiers = identifiers

    def compile(self, node: ast.AST) -> pl.Expr:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                return pl.lit(node.value)
            if isinstance(node.value, (int, float)):
                try:
                    value = float(node.value)
                except OverflowError:
                    raise FormulaError(
                        "numeric literal is too large") from None
                if not math.isfinite(value):
                    raise FormulaError(
                        "non-finite numeric literals are not allowed; use nan "
                        "for an intentional missing value")
                return pl.lit(value, dtype=pl.Float64)
            raise FormulaError("only numeric literals are allowed")

        if isinstance(node, ast.Name):
            if node.id in self.identifiers:
                return pl.col(self.identifiers[node.id])
            constant = node.id.lower()
            if constant == "pi":
                return pl.lit(math.pi, dtype=pl.Float64)
            if constant == "e":
                return pl.lit(math.e, dtype=pl.Float64)
            if constant == "nan":
                return pl.lit(float("nan"), dtype=pl.Float64)
            if constant in {"true", "false"}:
                return pl.lit(constant == "true")
            raise FormulaError(
                f"unknown name '{node.id}'; columns must be wrapped in braces")

        if isinstance(node, ast.UnaryOp):
            value = self.compile(node.operand)
            if isinstance(node.op, ast.UAdd):
                return value
            if isinstance(node.op, ast.USub):
                return -value
            if isinstance(node.op, (ast.Not, ast.Invert)):
                return ~value
            raise FormulaError("unsupported unary operator")

        if isinstance(node, ast.BinOp):
            left = self.compile(node.left)
            right = self.compile(node.right)
            operations: tuple[tuple[type[ast.operator], Any], ...] = (
                (ast.Add, operator.add),
                (ast.Sub, operator.sub),
                (ast.Mult, operator.mul),
                (ast.Div, operator.truediv),
                (ast.Mod, operator.mod),
                (ast.Pow, operator.pow),
                (ast.BitAnd, operator.and_),
                (ast.BitOr, operator.or_),
            )
            for node_type, operation in operations:
                if isinstance(node.op, node_type):
                    return operation(left, right)
            raise FormulaError(
                "unsupported binary operator; use +, -, *, /, %, **, & or |")

        if isinstance(node, ast.BoolOp):
            values = [self.compile(value) for value in node.values]
            if isinstance(node.op, ast.And):
                return reduce(operator.and_, values)
            if isinstance(node.op, ast.Or):
                return reduce(operator.or_, values)
            raise FormulaError("unsupported boolean operator")

        if isinstance(node, ast.Compare):
            left = self.compile(node.left)
            comparisons: list[pl.Expr] = []
            operations: tuple[tuple[type[ast.cmpop], Any], ...] = (
                (ast.Lt, operator.lt),
                (ast.LtE, operator.le),
                (ast.Gt, operator.gt),
                (ast.GtE, operator.ge),
                (ast.Eq, operator.eq),
                (ast.NotEq, operator.ne),
            )
            for op_node, comparator_node in zip(
                    node.ops, node.comparators, strict=True):
                right = self.compile(comparator_node)
                operation = next(
                    (operation for node_type, operation in operations
                     if isinstance(op_node, node_type)),
                    None,
                )
                if operation is None:
                    raise FormulaError(
                        "unsupported comparison; use <, <=, >, >=, == or !=")
                comparisons.append(operation(left, right))
                left = right
            return reduce(operator.and_, comparisons)

        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise FormulaError("only direct calls to allowlisted functions "
                                   "are supported")
            if node.keywords:
                raise FormulaError("formula functions do not accept keywords")
            function = node.func.id.lower()
            args = [self.compile(arg) for arg in node.args]
            unary = {
                "abs": lambda value: value.abs(),
                "sqrt": lambda value: value.sqrt(),
                "log": lambda value: value.log(),
                "log10": lambda value: value.log10(),
                "exp": lambda value: value.exp(),
                "sin": lambda value: value.sin(),
                "cos": lambda value: value.cos(),
                "tan": lambda value: value.tan(),
            }
            if function in unary:
                if len(args) != 1:
                    raise FormulaError(
                        f"{node.func.id}() requires exactly one argument")
                return unary[function](args[0])
            if function in {"min", "max"}:
                if not args:
                    raise FormulaError(
                        f"{node.func.id}() requires at least one argument")
                horizontal = (
                    pl.min_horizontal if function == "min"
                    else pl.max_horizontal)
                return horizontal(args)
            if function == "clip":
                if len(args) != 3:
                    raise FormulaError(
                        "clip() requires value, lower bound, upper bound")
                return args[0].clip(args[1], args[2])
            if function in {"if", "where"}:
                if len(args) != 3:
                    raise FormulaError(
                        f"{node.func.id}() requires condition, true value, "
                        "false value")
                return pl.when(args[0]).then(args[1]).otherwise(args[2])
            raise FormulaError(
                f"function '{node.func.id}' is not supported")

        raise FormulaError(
            f"unsupported expression element: {type(node).__name__}")


def parse_expression(expression: str) -> ParsedExpression:
    substituted, identifiers, dependencies = _replace_column_references(
        expression)
    try:
        tree = ast.parse(substituted, mode="eval")
    except SyntaxError as exc:
        message = exc.msg or "invalid syntax"
        raise FormulaError(f"invalid formula syntax: {message}") from None
    if sum(1 for _ in ast.walk(tree)) > MAX_AST_NODES:
        raise FormulaError(
            f"formula is too complex (maximum {MAX_AST_NODES} syntax nodes)")
    compiled = _ExpressionCompiler(identifiers).compile(tree.body)
    # Every materialized result has one predictable numeric dtype.  Cast
    # before is_infinite so boolean comparison results are supported too.
    as_float = compiled.cast(pl.Float64)
    finite = (
        pl.when(as_float.is_infinite())
        .then(pl.lit(float("nan"), dtype=pl.Float64))
        .otherwise(as_float)
    )
    return ParsedExpression(
        expression=expression.strip(),
        dependencies=dependencies,
        polars_expr=finite,
    )


def compile_formula_batch(
    specs: Sequence[Any],
    columns: Iterable[str],
    time_column: str,
) -> list[CompiledFormula]:
    """Compile and schema-check a batch in its declared evaluation order."""
    if not specs:
        raise FormulaError("at least one formula is required")
    if len(specs) > MAX_FORMULAS:
        raise FormulaError(
            f"a formula batch cannot exceed {MAX_FORMULAS} formulas")

    available = set(columns)
    compiled: list[CompiledFormula] = []
    for index, spec in enumerate(specs):
        try:
            name = _spec_value(spec, "name")
            validate_column_name(name)
            if name == time_column:
                raise FormulaError("the time column cannot be replaced")
            if time_column in {f"{name}__min", f"{name}__max"}:
                raise FormulaError(
                    f"formula target '{name}' conflicts with the time column "
                    "in plot-pyramid storage")
            expression = _spec_value(spec, "expression")
            replace = bool(_spec_value(spec, "replace", False))
            parsed = parse_expression(expression)
            unknown = [
                dependency for dependency in parsed.dependencies
                if dependency not in available
            ]
            if unknown:
                raise FormulaError(
                    f"unknown column reference(s): {unknown}; formulas can "
                    "only use source columns or earlier formulas in the batch")
            replaces_existing = name in available
            if replaces_existing and not replace:
                raise FormulaError(
                    f"target column '{name}' already exists; set replace=true "
                    "to overwrite it")
            compiled.append(CompiledFormula(
                name=name,
                expression=parsed.expression,
                replace=replace,
                dependencies=parsed.dependencies,
                replaces_existing=replaces_existing,
                polars_expr=parsed.polars_expr.alias(name),
            ))
            available.add(name)
        except FormulaError as exc:
            raise FormulaError(f"formula {index + 1}: {exc}") from None
    return compiled


def validate_recipe_formulas(specs: Sequence[Any]) -> list[dict]:
    """Syntax-check a test-independent recipe and return normalized formulas."""
    if not specs:
        raise FormulaError("a recipe must contain at least one formula")
    if len(specs) > MAX_FORMULAS:
        raise FormulaError(
            f"a recipe cannot exceed {MAX_FORMULAS} formulas")
    defined: set[str] = set()
    normalized: list[dict] = []
    for index, spec in enumerate(specs):
        try:
            name = _spec_value(spec, "name")
            validate_column_name(name)
            replace = bool(_spec_value(spec, "replace", False))
            if name in defined and not replace:
                raise FormulaError(
                    f"target '{name}' is repeated; the later formula must set "
                    "replace=true")
            parsed = parse_expression(_spec_value(spec, "expression"))
            normalized.append({
                "name": name,
                "expression": parsed.expression,
                "replace": replace,
            })
            defined.add(name)
        except FormulaError as exc:
            raise FormulaError(f"formula {index + 1}: {exc}") from None
    return normalized


def _representative_indices(n_rows: int, sample_size: int) -> np.ndarray:
    count = min(n_rows, max(1, sample_size))
    if count == n_rows:
        return np.arange(n_rows, dtype=np.int64)
    return np.unique(np.linspace(
        0, n_rows - 1, num=count, dtype=np.int64))


def _read_representative_rows(
    parquet_path: Path,
    columns: Sequence[str],
    indices: np.ndarray,
) -> pl.DataFrame:
    """Read evenly distributed rows without loading whole source columns."""
    frames: list[pl.DataFrame] = []
    with pq.ParquetFile(parquet_path) as parquet:
        row_group_start = 0
        index_cursor = 0
        for group_index in range(parquet.num_row_groups):
            row_count = parquet.metadata.row_group(group_index).num_rows
            row_group_end = row_group_start + row_count
            end_cursor = int(np.searchsorted(
                indices, row_group_end, side="left"))
            if end_cursor > index_cursor:
                local = (
                    indices[index_cursor:end_cursor] - row_group_start
                ).astype(np.int64, copy=False)
                table = parquet.read_row_group(
                    group_index, columns=list(columns))
                frames.append(pl.from_arrow(table.take(pa.array(local))))
                index_cursor = end_cursor
            row_group_start = row_group_end
            if index_cursor >= len(indices):
                break
    if not frames:
        return pl.DataFrame(schema={
            column: pl.Float64 for column in columns
        })
    return pl.concat(frames, how="vertical")


def _finite_json_values(series: pl.Series) -> tuple[list[float | None], dict]:
    values = series.cast(pl.Float64).to_numpy()
    json_values: list[float | None] = []
    finite_values: list[float] = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = float("nan")
        if math.isfinite(number):
            json_values.append(number)
            finite_values.append(number)
        else:
            json_values.append(None)
    valid_count = len(finite_values)
    stats = {
        "count": len(json_values),
        "valid_count": valid_count,
        "nan_count": len(json_values) - valid_count,
        "min": min(finite_values) if finite_values else None,
        "max": max(finite_values) if finite_values else None,
        "mean": (
            math.fsum(value / valid_count for value in finite_values)
            if finite_values else None
        ),
    }
    return json_values, stats


def preview_formulas(
    parquet_path: Path,
    meta: Mapping[str, Any],
    specs: Sequence[Any],
    sample_size: int = 64,
) -> dict:
    """Validate and evaluate formulas on evenly spaced rows across a test."""
    columns = list(meta["columns"])
    time_column = str(meta["time_column"])
    compiled = compile_formula_batch(specs, columns, time_column)
    n_rows = int(meta["n_rows"])
    if n_rows < 1:
        raise FormulaError("the test has no rows to preview")

    original = set(columns)
    required = [time_column]
    for item in compiled:
        for dependency in item.dependencies:
            if dependency in original and dependency not in required:
                required.append(dependency)
    indices = _representative_indices(n_rows, sample_size)
    try:
        frame = _read_representative_rows(
            parquet_path, required, indices)
        time_values, _ = _finite_json_values(frame[time_column])
        results: list[dict] = []
        for item in compiled:
            frame = frame.with_columns(item.polars_expr)
            values, stats = _finite_json_values(frame[item.name])
            results.append({
                "name": item.name,
                "expression": item.expression,
                "replace": item.replace,
                "dependencies": list(item.dependencies),
                "replaces_existing": item.replaces_existing,
                "stats": stats,
                "values": values,
            })
    except (pl.exceptions.PolarsError, pa.ArrowException, OSError) as exc:
        raise FormulaError(f"formula preview failed: {exc}") from None

    return {
        "valid": True,
        "sample_size": len(indices),
        "row_indices": indices.tolist(),
        "time": time_values,
        "formulas": results,
    }


def rewrite_provenance(
    value: Any,
    rename: Mapping[str, str] | None = None,
    drop: Iterable[str] | None = None,
) -> list[dict]:
    """Keep persisted formula provenance coherent after column edits."""
    rename = dict(rename or {})
    dropped = set(drop or [])
    if isinstance(value, Mapping):
        records = [
            {"name": key, **record}
            for key, record in value.items()
            if isinstance(record, Mapping)
        ]
    elif isinstance(value, list):
        records = [
            dict(record) for record in value if isinstance(record, Mapping)
        ]
    else:
        records = []

    rewritten: list[dict] = []
    for record in records:
        old_name = record.get("name")
        if not isinstance(old_name, str) or old_name in dropped:
            continue
        updated = dict(record)
        updated["name"] = rename.get(old_name, old_name)
        dependencies = [
            rename.get(dependency, dependency)
            for dependency in record.get("dependencies", [])
            if isinstance(dependency, str)
        ]
        updated["dependencies"] = list(dict.fromkeys(dependencies))
        expression = record.get("expression")
        if isinstance(expression, str) and rename:
            updated["expression"] = _COLUMN_REFERENCE_RE.sub(
                lambda match: (
                    "{" + rename.get(
                        match.group(1).strip(), match.group(1).strip()) + "}"
                ),
                expression,
            )
        updated["missing_dependencies"] = [
            dependency for dependency in updated["dependencies"]
            if dependency in dropped
        ]
        if not updated["missing_dependencies"]:
            updated.pop("missing_dependencies", None)
        rewritten.append(updated)
    return rewritten


def merge_provenance(
    existing: Any,
    compiled: Sequence[CompiledFormula],
    timestamp: str,
) -> list[dict]:
    """Merge a completed formula batch into deterministic metadata records."""
    records = rewrite_provenance(existing)
    positions = {
        record["name"]: index
        for index, record in enumerate(records)
        if isinstance(record.get("name"), str)
    }
    for item in compiled:
        prior_index = positions.get(item.name)
        prior = records[prior_index] if prior_index is not None else {}
        record = {
            "name": item.name,
            "expression": item.expression,
            "dependencies": list(item.dependencies),
            "engine": "safe-polars-v1",
            "replace": item.replace,
            "replaced_existing": item.replaces_existing,
            "created_at": prior.get("created_at", timestamp),
            "updated_at": timestamp,
        }
        if prior_index is None:
            positions[item.name] = len(records)
            records.append(record)
        else:
            records[prior_index] = record
    return records
