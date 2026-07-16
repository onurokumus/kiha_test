"""CSV -> parquet + min/max pyramid + meta.json.

Streaming throughout: RAM stays flat regardless of file size.

CLI:  python -m app.ingest <csv_path> <test_name>
"""

import json
import shutil
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

from .config import INGEST_BATCH, PYRAMID_LEVELS, ROW_GROUP_SIZE, TESTS_DIR
from .locks import test_write
from .store import write_json_atomic

NULL_VALUES = ["", "nan", "NaN", "NAN", "null", "NULL", "None"]


def detect_time_column(columns):
    for c in columns:
        if c.lower().startswith("time") or c.lower() in ("t", "t_s", "zaman"):
            return c
    return columns[0]


def _write_status(test_dir: Path, status: str, error: str = ""):
    payload = {"status": status}
    if error:
        payload["error"] = error
    write_json_atomic(test_dir / "status.json", payload)


def _bucket_minmax(arr: np.ndarray, factor: int):
    """Per-bucket nanmin/nanmax. Pads the tail bucket with NaN."""
    n = len(arr)
    n_buckets = -(-n // factor)
    if n_buckets * factor != n:
        arr = np.concatenate([arr, np.full(n_buckets * factor - n, np.nan)])
    a = arr.reshape(n_buckets, factor)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN buckets
        return np.nanmin(a, axis=1), np.nanmax(a, axis=1)


class _PyramidWriter:
    """Incremental writer for one pyramid level parquet file."""

    def __init__(self, path: Path, time_col: str, data_cols: list[str]):
        fields = [pa.field(time_col, pa.float64())]
        for c in data_cols:
            fields += [pa.field(f"{c}__min", pa.float32()),
                       pa.field(f"{c}__max", pa.float32())]
        self.schema = pa.schema(fields)
        self.writer = pq.ParquetWriter(path, self.schema)
        self.time_col = time_col
        self.data_cols = data_cols
        self.n_rows = 0

    def write(self, t: np.ndarray, minmax: dict):
        arrays = [pa.array(t, pa.float64())]
        for c in self.data_cols:
            mn, mx = minmax[c]
            arrays += [pa.array(mn.astype(np.float32)),
                       pa.array(mx.astype(np.float32))]
        self.writer.write_table(pa.Table.from_arrays(arrays, schema=self.schema))
        self.n_rows += len(t)

    def close(self):
        self.writer.close()


def build_pyramid(parquet_path: Path, pyr_dir: Path, time_col: str):
    """One streaming pass over data.parquet -> all pyramid levels + NaN counts."""
    pyr_dir.mkdir(exist_ok=True)
    pf = pq.ParquetFile(parquet_path)
    all_cols = [f.name for f in pf.schema_arrow]
    data_cols = [c for c in all_cols if c != time_col]

    writers = {f: _PyramidWriter(pyr_dir / f"L{f}.parquet", time_col, data_cols)
               for f in PYRAMID_LEVELS}
    nan_counts = dict.fromkeys(data_cols, 0)

    base = PYRAMID_LEVELS[0]
    max_f = PYRAMID_LEVELS[-1]
    buf: list[pa.RecordBatch] = []
    buffered = 0

    def flush(batch_tbl: pa.Table):
        t = batch_tbl.column(time_col).to_numpy(zero_copy_only=False)
        n = len(t)
        # bucket start times per level
        level_minmax = {f: {} for f in PYRAMID_LEVELS}
        for c in data_cols:
            arr = batch_tbl.column(c).to_numpy(zero_copy_only=False).astype(np.float64)
            nan_counts[c] += int(np.isnan(arr).sum())
            mn, mx = _bucket_minmax(arr, base)
            level_minmax[base][c] = (mn, mx)
            prev_mn, prev_mx = mn, mx
            for f in PYRAMID_LEVELS[1:]:
                step = f // PYRAMID_LEVELS[PYRAMID_LEVELS.index(f) - 1]
                mn2, _ = _bucket_minmax(prev_mn, step)
                _, mx2 = _bucket_minmax(prev_mx, step)
                level_minmax[f][c] = (mn2, mx2)
                prev_mn, prev_mx = mn2, mx2
        for f in PYRAMID_LEVELS:
            writers[f].write(t[::f][: len(next(iter(level_minmax[f].values()))[0])],
                             level_minmax[f])

    for batch in pf.iter_batches(batch_size=INGEST_BATCH):
        buf.append(batch)
        buffered += batch.num_rows
        # process in exact multiples of max_f; keep remainder buffered
        if buffered >= INGEST_BATCH:
            tbl = pa.Table.from_batches(buf)
            n_proc = (buffered // max_f) * max_f
            if n_proc:
                flush(tbl.slice(0, n_proc))
                tbl = tbl.slice(n_proc)
            buf = tbl.to_batches()
            buffered = tbl.num_rows
    if buffered:
        flush(pa.Table.from_batches(buf))

    for w in writers.values():
        w.close()
    return nan_counts, {f: writers[f].n_rows for f in PYRAMID_LEVELS}


def ingest_csv(csv_path: Path, name: str, copy_raw: bool = False) -> dict:
    """Ingest one test while excluding lifecycle operations for that name."""
    with test_write(name):
        return _ingest_csv(csv_path, name, copy_raw)


def _ingest_csv(csv_path: Path, name: str, copy_raw: bool = False) -> dict:
    csv_path = Path(csv_path)
    test_dir = TESTS_DIR / name
    test_dir.mkdir(parents=True, exist_ok=True)
    _write_status(test_dir, "ingesting")
    t0 = time.time()
    try:
        if copy_raw and csv_path.resolve() != (test_dir / "raw.csv").resolve():
            shutil.copyfile(csv_path, test_dir / "raw.csv")

        # 1) stream CSV -> parquet
        parquet_path = test_dir / "data.parquet"
        lf = pl.scan_csv(csv_path, null_values=NULL_VALUES,
                         infer_schema_length=10000)
        lf.sink_parquet(parquet_path, row_group_size=ROW_GROUP_SIZE,
                        statistics=True)

        # 2) basic facts
        pf = pq.ParquetFile(parquet_path)
        columns = [f.name for f in pf.schema_arrow]
        n_rows = pf.metadata.num_rows
        time_col = detect_time_column(columns)
        head = pl.read_parquet(parquet_path, columns=[time_col], n_rows=4096)
        tvals = head[time_col].to_numpy()
        # span/count is robust against per-sample quantization in the time column
        dt = float((tvals[-1] - tvals[0]) / (len(tvals) - 1)) if len(tvals) > 1 else 0.0
        if not np.isfinite(dt) or dt <= 0:
            raise ValueError(
                f"cannot determine sample rate: time column '{time_col}' is "
                "not increasing (wrong column detected or too few rows)")
        fs = round(1.0 / dt, 3)
        diffs = np.diff(tvals)
        jitter_warn = bool(np.any(np.abs(diffs - dt) > 0.01 * dt))

        # 3) pyramid + NaN scan
        nan_counts, level_rows = build_pyramid(parquet_path, test_dir / "pyramid",
                                               time_col)

        meta = {
            "name": name,
            "source_file": csv_path.name,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "n_rows": n_rows,
            "n_columns": len(columns),
            "columns": columns,
            "time_column": time_col,
            "fs_hz": fs,
            "duration_s": round(n_rows * dt, 3) if dt else None,
            "t_start": float(tvals[0]) if len(tvals) else None,
            "jitter_warning": jitter_warn,
            "nan_counts": {c: n for c, n in nan_counts.items() if n > 0},
            "nan_policy": "keep_gaps",
            "pyramid_levels": PYRAMID_LEVELS,
            "pyramid_rows": level_rows,
            "ingest_seconds": round(time.time() - t0, 1),
        }
        write_json_atomic(test_dir / "meta.json", meta)
        _write_status(test_dir, "ready")
        return meta
    except Exception as e:
        _write_status(test_dir, "error", repr(e))
        raise


if __name__ == "__main__":
    csv, name = sys.argv[1], sys.argv[2]
    meta = ingest_csv(Path(csv), name)
    print(json.dumps(meta, indent=2))
