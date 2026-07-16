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
import time
from datetime import datetime, timezone
from pathlib import Path

import polars as pl
import pyarrow.parquet as pq

from .config import ROW_GROUP_SIZE, TESTS_DIR
from .ingest import _write_status, build_pyramid
from .locks import test_write
from .store import write_json_atomic

NAN_POLICIES = ("keep_gaps", "zero_fill", "interpolate")


def rebuild_test(name: str, ops: dict) -> None:
    with test_write(name):
        _rebuild(name, ops)


def _rebuild(name: str, ops: dict) -> None:
    test_dir = TESTS_DIR / name
    _write_status(test_dir, "rebuilding")
    t_begin = time.time()
    try:
        meta = json.loads((test_dir / "meta.json").read_text())
        tcol: str = meta["time_column"]
        fs: float = meta["fs_hz"]
        rename: dict = ops.get("rename") or {}
        drop: list = ops.get("drop") or []
        trim_t0 = ops.get("trim_t0")
        trim_t1 = ops.get("trim_t1")
        policy = ops.get("nan_policy")

        parquet_path = test_dir / "data.parquet"
        lf = pl.scan_parquet(parquet_path)
        schema = lf.collect_schema()

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

        tmp = test_dir / "data.parquet.tmp"
        if needs_collect:
            lf.collect().write_parquet(
                tmp, row_group_size=ROW_GROUP_SIZE, statistics=True)
        else:
            lf.sink_parquet(tmp, row_group_size=ROW_GROUP_SIZE,
                            statistics=True)
        os.replace(tmp, parquet_path)

        # refreshed facts
        pf = pq.ParquetFile(parquet_path)
        columns = [f.name for f in pf.schema_arrow]
        n_rows = pf.metadata.num_rows
        if n_rows < 2:
            raise ValueError("edit would leave fewer than 2 samples")
        new_tcol = rename.get(tcol, tcol)
        head = pl.read_parquet(parquet_path, columns=[new_tcol], n_rows=1)
        t_start = float(head[new_tcol][0])
        duration = round(n_rows / fs, 3)

        nan_counts, level_rows = build_pyramid(
            parquet_path, test_dir / "pyramid", new_tcol)

        meta.update({
            "columns": columns,
            "n_columns": len(columns),
            "n_rows": n_rows,
            "time_column": new_tcol,
            "t_start": t_start,
            "duration_s": duration,
            "nan_counts": {c: n for c, n in nan_counts.items() if n > 0},
            "nan_policy": policy or meta.get("nan_policy", "keep_gaps"),
            "pyramid_rows": level_rows,
            "edited_at": datetime.now(timezone.utc).isoformat(
                timespec="seconds"),
            "edit_seconds": round(time.time() - t_begin, 1),
        })
        write_json_atomic(test_dir / "meta.json", meta)

        _clip_testpoints(test_dir, name, fs, t_start, duration)
        _write_status(test_dir, "ready")
    except Exception as e:  # status file is how the UI learns of failures
        _write_status(test_dir, "error", repr(e))
        raise


def _clip_testpoints(test_dir: Path, name: str, fs: float,
                     t_start: float, duration: float) -> None:
    """Clamp saved test points to the (possibly trimmed) data range and
    recompute their row indices against the new t_start."""
    tp_path = test_dir / "testpoints.json"
    try:
        payload = json.loads(tp_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return
    t_end = t_start + duration
    kept = []
    for tp in payload.get("test_points", []):
        start = max(float(tp["start_s"]), t_start)
        end = tp.get("end_s")
        if end is not None:
            end = min(float(end), t_end)
            if end - start < 0.01:
                continue  # fully outside the kept range
        elif start >= t_end:
            continue
        tp = dict(tp)
        tp["start_s"] = round(start, 6)
        tp["start_idx"] = int(round((start - t_start) * fs))
        if end is not None:
            tp["end_s"] = round(end, 6)
            tp["end_idx"] = int(round((end - t_start) * fs))
        kept.append(tp)
    payload["test_points"] = kept
    payload["test"] = name
    write_json_atomic(tp_path, payload)
