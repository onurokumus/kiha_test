"""CSV -> parquet + min/max pyramid + meta.json.

Streaming throughout: RAM stays flat regardless of file size.

CLI:  python -m app.ingest <csv_path> <test_name>
"""

import json
import logging
import os
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

from .config import (DEFAULT_FS_HZ, INGEST_BATCH, PYRAMID_LEVELS,
                     ROW_GROUP_SIZE, TESTS_DIR)
from .locks import test_write
from .status import write_status
from .store import bucket_minmax, write_json_atomic

NULL_VALUES = ["", "nan", "NaN", "NAN", "null", "NULL", "None"]

# Separators tried when the file is not plain comma-CSV, most-specific first
# (a decimal-comma file uses ';', so ',' must lose to ';' when both appear).
CANDIDATE_SEPARATORS = [";", "\t", "|", ","]

logger = logging.getLogger("kiha.ingest")


def detect_time_column(columns):
    for c in columns:
        if c.lower().startswith("time") or c.lower() in ("t", "t_s", "zaman"):
            return c
    return columns[0]


def sniff_dialect(csv_path: Path) -> tuple[str, bool]:
    """Detect (separator, decimal_comma) from the first lines of the file.

    Real rig exports are not all plain comma-CSV: KiHa ground-test files are
    semicolon-separated with decimal commas (``19:39,2``, ``32,50617``).  The
    field separator is chosen as the candidate that both appears in the header
    and yields the most consistent field count across the first data rows;
    decimal comma is inferred only when the separator is not itself a comma and
    ``digit,digit`` occurs in the data (a European decimal, not a delimiter)."""
    lines: list[str] = []
    with open(csv_path, encoding="utf-8", errors="replace") as fh:
        for _ in range(20):
            line = fh.readline()
            if not line:
                break
            if line.strip():
                lines.append(line.rstrip("\n").rstrip("\r"))
    if not lines:
        return ",", False
    header = lines[0]
    data = lines[1:]

    best_sep, best_score = ",", -1
    for sep in CANDIDATE_SEPARATORS:
        header_count = header.count(sep)
        if header_count == 0:
            continue
        # Reward a separator whose field count is stable across data rows.
        consistent = sum(1 for d in data if d.count(sep) == header_count)
        score = header_count * (1 + consistent)
        if score > best_score:
            best_sep, best_score = sep, score

    decimal_comma = False
    if best_sep != "," and re.search(r"\d,\d", "\n".join(data or [header])):
        decimal_comma = True
    return best_sep, decimal_comma


def _time_seconds_expr(col: str, decimal_comma: bool) -> pl.Expr:
    """Polars expression turning a time column into float seconds.

    Handles a plain numeric column (already seconds) and clock strings
    ``[HH:]MM:SS[.f]`` (decimal comma normalised to a dot first), so a
    ``19:39,2`` timestamp becomes 1179.2 s.  Anything unparseable becomes
    null, which the sample-rate check then rejects with a clear error."""
    text = pl.col(col).cast(pl.Utf8).str.strip_chars()
    if decimal_comma:
        text = text.str.replace_all(",", ".")
    groups = text.str.extract_groups(
        r"^(?:(?<h>\d+):)?(?<m>\d+):(?<s>\d+(?:\.\d+)?)$")
    clock = (
        pl.coalesce(groups.struct.field("h").cast(pl.Float64, strict=False),
                    pl.lit(0.0)) * 3600.0
        + groups.struct.field("m").cast(pl.Float64, strict=False) * 60.0
        + groups.struct.field("s").cast(pl.Float64, strict=False))
    return pl.coalesce(clock, text.cast(pl.Float64, strict=False)).alias(col)


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
    """One streaming pass over data.parquet -> all pyramid levels + NaN counts.

    The reader handle is closed before returning (via ``with``) so a caller
    that renames/replaces ``parquet_path`` straight after — the staged edit
    rebuild — does not hit a Windows sharing violation."""
    pyr_dir.mkdir(exist_ok=True)
    with pq.ParquetFile(parquet_path) as pf:
        all_cols = [f.name for f in pf.schema_arrow]
        data_cols = [c for c in all_cols if c != time_col]

        writers = {f: _PyramidWriter(pyr_dir / f"L{f}.parquet", time_col,
                                     data_cols)
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
                mn, mx = bucket_minmax(arr, base)
                level_minmax[base][c] = (mn, mx)
                prev_mn, prev_mx = mn, mx
                for f in PYRAMID_LEVELS[1:]:
                    step = f // PYRAMID_LEVELS[PYRAMID_LEVELS.index(f) - 1]
                    mn2, _ = bucket_minmax(prev_mn, step)
                    _, mx2 = bucket_minmax(prev_mx, step)
                    level_minmax[f][c] = (mn2, mx2)
                    prev_mn, prev_mx = mn2, mx2
            for f in PYRAMID_LEVELS:
                writers[f].write(
                    t[::f][: len(next(iter(level_minmax[f].values()))[0])],
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


def ingest_csv(csv_path: Path, name: str, copy_raw: bool = False,
               source_name: str | None = None,
               assume_fs: float | None = None) -> dict:
    """Ingest one test while excluding lifecycle operations for that name.

    source_name: original file name for meta.source_file — API uploads
    stream into raw.csv, so csv_path.name would lose what the user sent.
    assume_fs: sample rate to assume when the time column is unusable; a
    perfect uniform axis is generated instead of failing (default DEFAULT_FS_HZ).
    """
    with test_write(name):
        return _ingest_csv(csv_path, name, copy_raw, source_name, assume_fs)


def _ingest_csv(csv_path: Path, name: str, copy_raw: bool = False,
                source_name: str | None = None,
                assume_fs: float | None = None) -> dict:
    csv_path = Path(csv_path)
    test_dir = TESTS_DIR / name
    test_dir.mkdir(parents=True, exist_ok=True)
    write_status(test_dir, "ingesting")
    logger.info("ingest '%s': started (%s)", name, csv_path.name)
    t0 = time.time()
    try:
        if copy_raw and csv_path.resolve() != (test_dir / "raw.csv").resolve():
            shutil.copyfile(csv_path, test_dir / "raw.csv")

        # 1) sniff the dialect (separator + decimal comma) so semicolon /
        #    decimal-comma rig exports parse into real columns, not one blob.
        separator, decimal_comma = sniff_dialect(csv_path)
        lf = pl.scan_csv(csv_path, separator=separator,
                         decimal_comma=decimal_comma, null_values=NULL_VALUES,
                         infer_schema_length=10000, truncate_ragged_lines=True)
        src_schema = lf.collect_schema()
        src_columns = list(src_schema.names())
        if not src_columns:
            raise ValueError("CSV appears to be empty")
        time_col = detect_time_column(src_columns)

        # Keep the time column (parsed to seconds) + every numeric column.
        # Non-numeric columns (text notes, bool/datetime, an unparsed clock
        # column that is not the time column) are RECORDED and skipped rather
        # than crashing the whole ingest — hard requirement #5 (no fixed
        # schema).  All kept data columns are cast to Float64 so the parquet,
        # pyramid, and every downstream reader see a uniform numeric schema.
        numeric_cols = [c for c in src_columns
                        if c != time_col and src_schema[c].is_numeric()]
        skipped = {c: str(src_schema[c]) for c in src_columns
                   if c != time_col and c not in numeric_cols}

        time_dtype = src_schema[time_col]
        time_expr = (pl.col(time_col).cast(pl.Float64) if time_dtype.is_numeric()
                     else _time_seconds_expr(time_col, decimal_comma))
        lf = lf.select([time_expr]
                       + [pl.col(c).cast(pl.Float64) for c in numeric_cols])

        # 2) stream CSV -> parquet
        parquet_path = test_dir / "data.parquet"
        lf.sink_parquet(parquet_path, row_group_size=ROW_GROUP_SIZE,
                        statistics=True)

        # 3) basic facts. The full (single) time column is read once: it is
        #    the cheapest robust way to derive fs from the total span (immune
        #    to per-sample quantization) and to scan the WHOLE file for jitter
        #    rather than just the first seconds. Column order is exactly what
        #    the select above wrote, so there is no need to reopen the parquet
        #    (leaving a pq handle open blocks os.replace/rmtree on Windows).
        columns = [time_col] + numeric_cols
        tvals = pl.read_parquet(parquet_path, columns=[time_col])[time_col] \
            .to_numpy()
        n_rows = len(tvals)
        if n_rows < 2:
            raise ValueError("need at least 2 samples to form a series")

        # Derive dt from the total span (robust to per-sample quantization).
        dt = None
        if np.isfinite(tvals).all():
            span_dt = float((tvals[-1] - tvals[0]) / (n_rows - 1))
            if np.isfinite(span_dt) and span_dt > 0:
                dt = span_dt

        if dt is None:
            # Time column is unusable — non-finite/unparseable, non-increasing,
            # or a single repeated coarse timestamp (the low-resolution clock
            # case, e.g. every row logged as ``19:39,2``). The bulk samples are
            # still good, so rather than fail the whole ingest, GENERATE a
            # perfect uniform axis at the assumed rate and mark it as such.
            fs = round(float(assume_fs if assume_fs else DEFAULT_FS_HZ), 6)
            if not np.isfinite(fs) or fs <= 0:
                raise ValueError(f"assumed sample rate must be > 0, got {fs}")
            dt = 1.0 / fs
            tvals = np.arange(n_rows, dtype=np.float64) * dt
            t_start = 0.0
            time_source = "generated"
            quantized = False
            jitter_warn = False
            logger.warning(
                "ingest '%s': time column '%s' unusable — generated a uniform "
                "%.6g Hz axis (%d samples)", name, time_col, fs, n_rows)
            # Rewrite the time column with the synthetic axis so the pyramid
            # built next and every reader see uniform time. Same scan->sink->
            # os.replace pattern edit._rebuild uses (proven safe on Windows;
            # runs under test_write so no other reader holds the file).
            tmp_parquet = parquet_path.with_name(parquet_path.name + ".tmp")
            (pl.scan_parquet(parquet_path)
             .with_columns((pl.int_range(0, pl.len(), dtype=pl.Int64)
                            .cast(pl.Float64) * dt).alias(time_col))
             .sink_parquet(tmp_parquet, row_group_size=ROW_GROUP_SIZE,
                           statistics=True))
            os.replace(tmp_parquet, parquet_path)
        else:
            fs = round(1.0 / dt, 3)
            t_start = float(tvals[0])
            time_source = "measured"
            # Quantization-aware jitter: a clock column logged at coarse
            # resolution (e.g. 0.1 s) makes per-sample diffs a 0/step staircase
            # that is NOT real jitter. When the smallest positive step is well
            # above dt, only flag backward time or gaps bigger than a few
            # quantization steps; otherwise use the fine per-sample check.
            diffs = np.diff(tvals)
            positive = diffs[diffs > 0]
            qstep = float(positive.min()) if positive.size else dt
            quantized = qstep > 1.5 * dt
            if quantized:
                jitter_warn = bool(
                    np.any(diffs < 0)
                    or (positive.size and positive.max() > 3 * qstep))
            else:
                jitter_warn = bool(np.any(np.abs(diffs - dt) > 0.01 * dt))

        # 4) pyramid + NaN scan
        nan_counts, level_rows = build_pyramid(parquet_path, test_dir / "pyramid",
                                               time_col)

        meta = {
            "name": name,
            "source_file": source_name or csv_path.name,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "n_rows": n_rows,
            "n_columns": len(columns),
            "columns": columns,
            "time_column": time_col,
            "fs_hz": fs,
            "duration_s": round(n_rows * dt, 3) if dt else None,
            "t_start": t_start,
            "time_source": time_source,
            "csv_separator": separator,
            "decimal_comma": decimal_comma,
            "time_quantized": quantized,
            "skipped_columns": skipped,
            "jitter_warning": jitter_warn,
            "nan_counts": {c: n for c, n in nan_counts.items() if n > 0},
            "nan_policy": "keep_gaps",
            "pyramid_levels": PYRAMID_LEVELS,
            "pyramid_rows": level_rows,
            "ingest_seconds": round(time.time() - t0, 1),
        }
        write_json_atomic(test_dir / "meta.json", meta)
        write_status(test_dir, "ready")
        logger.info("ingest '%s': ready — %d rows x %d cols in %.1f s",
                    name, n_rows, len(columns), meta["ingest_seconds"])
        return meta
    except Exception as e:
        write_status(test_dir, "error", repr(e))
        logger.exception("ingest '%s': FAILED", name)
        raise


if __name__ == "__main__":
    csv, name = sys.argv[1], sys.argv[2]
    meta = ingest_csv(Path(csv), name)
    print(json.dumps(meta, indent=2))
