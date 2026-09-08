"""Destructive test edits: column rename/drop, time trim, NaN policy.

All of them rewrite data.parquet and rebuild the pyramid, so they run as a
background task with status 'rebuilding' (same lifecycle as ingest). The
endpoint validates against current meta and publishes the status before
scheduling; this module re-checks only what could corrupt data.

NaN policy notes: 'drop rows' is deliberately NOT offered — removing rows
breaks the uniform-sample-rate assumption the whole windowed reader relies
on. 'interpolate' is not supported by the polars streaming engine, so that
one collects in RAM (fine for bench tests; a 1 h/112-col test needs ~7 GB).
"""

import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

import polars as pl
import pyarrow.parquet as pq

from . import formula
from .config import ROW_GROUP_SIZE, TESTS_DIR
from .ingest import build_pyramid
from .locks import test_write
from .status import write_status
from .store import _testpoint_bounds, write_json_atomic

NAN_POLICIES = ("keep_gaps", "zero_fill", "interpolate")


def _updated_gap_ranges(
    meta: dict,
    fs: float,
    new_t_start: float,
    n_rows: int,
    policy: str | None,
) -> list[list[int]]:
    """Shift imported gap ranges after a trim, or clear them after filling."""
    if policy and policy != "keep_gaps":
        return []
    old_t_start = float(meta.get("t_start") or 0.0)
    offset = max(0, int(round((new_t_start - old_t_start) * fs)))
    source_end = offset + n_rows
    shifted: list[list[int]] = []
    for value in meta.get("time_gap_ranges") or []:
        if (not isinstance(value, list) or len(value) != 2
                or not all(isinstance(item, int) for item in value)):
            continue
        start = max(offset, value[0])
        end = min(source_end, value[1])
        if start < end:
            shifted.append([start - offset, end - offset])
    return shifted


def rebuild_test(name: str, ops: dict) -> None:
    with test_write(name):
        _rebuild(name, ops)


def _discard(*paths: Path) -> None:
    """Best-effort removal of staging leftovers (files or directories)."""
    for p in paths:
        try:
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink(missing_ok=True)
        except OSError:
            pass


def _rebuild(name: str, ops: dict) -> None:
    """Destructive edit, staged so the live files are never left inconsistent.

    The expensive work (new data.parquet + new pyramid + fact recompute) all
    lands in *.tmp staging paths while the originals stay untouched.  Only once
    everything succeeded do the fast rename swaps run, back to back, and
    meta.json/status is written LAST as the commit signal.  A crash during the
    minutes-long build therefore leaves the original test fully intact; a crash
    in the millisecond swap window leaves status 'rebuilding', which restart
    recovery (main._recover_interrupted_ingests) flips to 'error'.  Either way
    there is no persisted state of new-data-with-stale-pyramid-and-meta.
    """
    test_dir = TESTS_DIR / name
    parquet_path = test_dir / "data.parquet"
    pyr_dir = test_dir / "pyramid"
    tmp_parquet = test_dir / "data.parquet.tmp"
    tmp_pyramid = test_dir / "pyramid.tmp"
    old_pyramid = test_dir / "pyramid.old"

    write_status(test_dir, "rebuilding")
    t_begin = time.time()
    try:
        # Clear any staging leftovers from a previously interrupted rebuild.
        _discard(tmp_parquet, tmp_pyramid, old_pyramid)

        meta = json.loads((test_dir / "meta.json").read_text())
        tcol: str = meta["time_column"]
        fs: float = meta["fs_hz"]
        rename: dict = ops.get("rename") or {}
        drop: list = ops.get("drop") or []
        trim_t0 = ops.get("trim_t0")
        trim_t1 = ops.get("trim_t1")
        policy = ops.get("nan_policy")
        formula_specs: list = ops.get("formulas") or []
        if formula_specs:
            formula_specs = formula.expand_formula_dependents(
                formula_specs, meta.get("derived_variables"))

        if formula_specs and (
                rename or drop or policy
                or trim_t0 is not None or trim_t1 is not None):
            raise ValueError(
                "formulas cannot be combined with rename, drop, trim, or "
                "NaN-policy operations in one rebuild")

        lf = pl.scan_parquet(parquet_path)
        schema = lf.collect_schema()
        compiled_formulas: list[formula.CompiledFormula] = []

        if formula_specs:
            compiled_formulas = formula.compile_formula_batch(
                formula_specs, schema.names(), tcol)
            # Deliberately one with_columns call per formula: later formulas
            # may reference a result materialized by an earlier one.
            for compiled in compiled_formulas:
                lf = lf.with_columns(compiled.polars_expr)

        if trim_t0 is not None:
            lf = lf.filter(pl.col(tcol) >= float(trim_t0))
        if trim_t1 is not None:
            lf = lf.filter(pl.col(tcol) <= float(trim_t1))
        if drop:
            lf = lf.drop(drop)
        if rename:
            lf = lf.rename(rename)

        needs_collect = False
        if policy and policy != "keep_gaps":
            float_cols = [
                rename.get(c, c)
                for c, dtype in schema.items()
                if c != tcol and c not in drop
                and dtype in (pl.Float32, pl.Float64)
            ]
            if policy == "zero_fill":
                # missing CSV cells ingest as nulls, computed gaps as NaN —
                # a fill must cover both representations
                lf = lf.with_columns(
                    [pl.col(c).fill_nan(0.0).fill_null(0.0)
                     for c in float_cols])
            elif policy == "interpolate":
                lf = lf.with_columns(
                    [pl.col(c).fill_nan(None).interpolate().alias(c)
                     for c in float_cols])
                needs_collect = True

        # 1) stage the new parquet
        if needs_collect:
            lf.collect().write_parquet(
                tmp_parquet, row_group_size=ROW_GROUP_SIZE, statistics=True)
        else:
            lf.sink_parquet(tmp_parquet, row_group_size=ROW_GROUP_SIZE,
                            statistics=True)

        # 2) recompute facts + stage the new pyramid, both from the staged
        #    parquet (validation happens here, before anything is swapped).
        #    The reader handle is closed before the swap: on Windows os.replace
        #    cannot move a file that pyarrow still holds open.
        new_tcol = rename.get(tcol, tcol)
        with pq.ParquetFile(tmp_parquet) as pf:
            columns = [f.name for f in pf.schema_arrow]
            n_rows = pf.metadata.num_rows
            if n_rows < 2:
                raise ValueError("edit would leave fewer than 2 samples")
            first = next(pf.iter_batches(batch_size=1, columns=[new_tcol]))
            t_start = float(first.column(0)[0].as_py())
        duration = float(n_rows / fs)
        edited_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        # Validate and build all metadata before the live-file swap. Legacy or
        # malformed provenance must never fail after data/pyramid are committed.
        derived_variables = formula.rewrite_provenance(
            meta.get("derived_variables"), rename, drop)
        if compiled_formulas:
            derived_variables = formula.merge_provenance(
                derived_variables, compiled_formulas, edited_at)
        derived_variables = formula.refresh_missing_dependencies(
            derived_variables, columns)
        nan_counts, level_rows = build_pyramid(tmp_parquet, tmp_pyramid,
                                               new_tcol)
        gap_ranges = _updated_gap_ranges(
            meta, fs, t_start, n_rows, policy)
        missing_rows = sum(end - start for start, end in gap_ranges)
        # Column/formula/fill edits leave row identity unchanged. Only a trim
        # may alter points; resolve its result against the OLD rows before the
        # commit so invalid point metadata cannot fail after files are swapped.
        trimmed_points = (
            _trimmed_testpoints(test_dir, name, meta, t_start, n_rows)
            if trim_t0 is not None or trim_t1 is not None else None)

        # 3) COMMIT — fast rename swaps only past this point. Each os.replace
        #    has a non-existent destination (plain atomic rename); no reader
        #    can hold a file open because this runs under test_write.
        os.replace(tmp_parquet, parquet_path)
        if pyr_dir.exists():
            os.replace(pyr_dir, old_pyramid)   # move the old pyramid aside
        os.replace(tmp_pyramid, pyr_dir)        # swap the new one in
        _discard(old_pyramid)

        meta.update({
            "columns": columns,
            "n_columns": len(columns),
            "n_rows": n_rows,
            "time_column": new_tcol,
            "t_start": t_start,
            "duration_s": duration,
            "source_n_rows": n_rows - missing_rows,
            "time_gap_count": len(gap_ranges),
            "missing_rows_inserted": missing_rows,
            "time_gap_seconds": missing_rows / fs,
            "time_gap_ranges": gap_ranges,
            "nan_counts": {c: n for c, n in nan_counts.items() if n > 0},
            "nan_policy": policy or meta.get("nan_policy", "keep_gaps"),
            "pyramid_rows": level_rows,
            "derived_variables": derived_variables,
            "edited_at": edited_at,
            "edit_seconds": round(time.time() - t_begin, 1),
        })
        write_json_atomic(test_dir / "meta.json", meta)
        # the data changed: drop the tp_stats sidecar so nothing serves
        # averages computed against the old columns/rows
        _discard(test_dir / "tp_stats.json")

        if trimmed_points is not None:
            write_json_atomic(test_dir / "testpoints.json", trimmed_points)
        write_status(test_dir, "ready")
    except Exception as e:  # status file is how the UI learns of failures
        _discard(tmp_parquet, tmp_pyramid, old_pyramid)
        write_status(test_dir, "error", repr(e))
        raise


def _trimmed_testpoints(test_dir: Path, name: str, old_meta: dict,
                        t_start: float, n_rows: int) -> dict | None:
    """Intersect saved half-open ranges with the rows retained by a trim.

    Resolve authoritative indices and implicit next-point ends BEFORE removing
    points. Otherwise an open point whose successor was trimmed away can grow
    into unrelated data, or an index-only end can keep its old row offset.
    """
    tp_path = test_dir / "testpoints.json"
    try:
        payload = json.loads(tp_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    fs = float(old_meta["fs_hz"])
    old_start = float(old_meta.get("t_start") or 0.0)
    offset = max(0, int(round((t_start - old_start) * fs)))
    points = payload.get("test_points", [])
    kept = []
    for tp in points:
        old_i0, old_i1 = _testpoint_bounds(old_meta, points, tp)
        i0 = max(0, old_i0 - offset)
        i1 = min(n_rows, old_i1 - offset)
        if i1 <= i0:
            continue
        tp = dict(tp)
        tp["start_s"] = round(t_start + i0 / fs, 6)
        tp["end_s"] = round(t_start + i1 / fs, 6)
        tp["start_idx"] = i0
        tp["end_idx"] = i1
        kept.append(tp)
    payload["test_points"] = kept
    payload["test"] = name
    return payload
