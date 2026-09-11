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
from .provenance import normalize_uploader_name
from .test_notes import Description
from pydantic import TypeAdapter
from .quality import EXAMPLE_LIMIT, inspect_source_time
from .status import write_status
from .store import bucket_minmax, write_json_atomic

NULL_VALUES = ["", "nan", "NaN", "NAN", "null", "NULL", "None"]

# Separators tried when the file is not plain comma-CSV, most-specific first
# (a decimal-comma file uses ';', so ',' must lose to ';' when both appear).
CANDIDATE_SEPARATORS = [";", "\t", "|", ","]

# Refuse pathological expansions while still covering long real acquisition
# dropouts. Missing rows contain only a timestamp plus null signals and compress
# well in Parquet, but an unbounded clock jump must not exhaust disk space.
MAX_GAP_FILL_ROWS = 5_000_000

logger = logging.getLogger("kiha.ingest")


def detect_time_column(columns) -> str | None:
    for c in columns:
        normalized = c.strip().lower()
        if (normalized.startswith("time")
                or normalized in ("t", "t_s", "zaman")):
            return c
    return None


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


def _measured_timing(tvals: np.ndarray) -> dict | None:
    """Infer nominal timing while excluding acquisition dropouts.

    Zero deltas are deliberately included in the nominal mean after the
    positive clock step is identified. This recovers rates above a timestamp's
    resolution (for example paired 1 ms timestamps from a 2 kHz logger).
    Positive steps larger than 1.5 times the robust normal step are interpreted
    as missing sample intervals.
    """
    if len(tvals) < 2 or not np.isfinite(tvals).all():
        return None
    diffs = np.diff(tvals)
    if np.any(diffs < 0):
        return None
    positive = diffs[diffs > 0]
    if not positive.size:
        return None

    median_step = float(np.median(positive))
    mad = float(np.median(np.abs(positive - median_step)))
    lower_quartile = float(np.quantile(positive, 0.25))
    typical_positive = max(lower_quartile, median_step - 3.0 * mad)
    if not np.isfinite(typical_positive) or typical_positive <= 0:
        return None

    tolerance = max(
        abs(typical_positive) * 1e-9,
        np.finfo(np.float64).eps * 32,
    )
    normal_limit = 1.5 * typical_positive + tolerance
    normal_mask = diffs <= normal_limit
    normal_diffs = diffs[normal_mask]
    if not normal_diffs.size:
        return None
    dt = float(np.mean(normal_diffs))
    if not np.isfinite(dt) or dt <= 0:
        return None

    missing_before = np.zeros(len(tvals), dtype=np.int64)
    total_missing = 0
    for diff_index in np.flatnonzero(~normal_mask):
        intervals = max(2, int(np.rint(float(diffs[diff_index]) / dt)))
        missing = intervals - 1
        total_missing += missing
        if total_missing > MAX_GAP_FILL_ROWS:
            raise ValueError(
                "timestamp gaps imply more than "
                f"{MAX_GAP_FILL_ROWS:,} missing rows; repair the source time "
                "column or import with generated time")
        missing_before[diff_index + 1] = missing

    quantized = typical_positive > 1.5 * dt
    if quantized:
        jitter_warning = total_missing > 0
    else:
        jitter_warning = bool(
            total_missing > 0
            or np.any(np.abs(normal_diffs - dt) > 0.01 * dt)
        )
    return {
        "dt": dt,
        "quantized": quantized,
        "jitter_warning": jitter_warning,
        "missing_before": missing_before,
        "gap_count": int(np.count_nonzero(missing_before)),
        "missing_rows": int(total_missing),
    }


def _insert_missing_rows(
    parquet_path: Path,
    time_col: str,
    tvals: np.ndarray,
    missing_before: np.ndarray,
) -> list[list[int]]:
    """Stream NaN rows into measured timestamp gaps.

    Ranges are returned as half-open row indices in the expanded Parquet. The
    source rows and their timestamps remain unchanged; inserted timestamps are
    evenly spaced between the surrounding measured samples.
    """
    if not np.any(missing_before):
        return []

    tmp_path = parquet_path.with_name(parquet_path.name + ".gaps.tmp")
    gap_ranges: list[list[int]] = []
    source_offset = 0
    output_offset = 0
    try:
        with pq.ParquetFile(parquet_path) as reader:
            schema = reader.schema_arrow
            with pq.ParquetWriter(
                    tmp_path, schema, compression="zstd") as writer:
                for batch in reader.iter_batches(batch_size=INGEST_BATCH):
                    batch_rows = batch.num_rows
                    local_gaps = np.flatnonzero(
                        missing_before[
                            source_offset:source_offset + batch_rows
                        ]
                    )
                    cursor = 0
                    for local_index in local_gaps:
                        local_index = int(local_index)
                        if local_index > cursor:
                            segment = batch.slice(cursor, local_index - cursor)
                            writer.write_batch(
                                segment, row_group_size=ROW_GROUP_SIZE)
                            output_offset += segment.num_rows

                        source_index = source_offset + local_index
                        gap_rows = int(missing_before[source_index])
                        gap_start = output_offset
                        previous_time = float(tvals[source_index - 1])
                        next_time = float(tvals[source_index])
                        step = (next_time - previous_time) / (gap_rows + 1)

                        written = 0
                        while written < gap_rows:
                            chunk_rows = min(
                                ROW_GROUP_SIZE, gap_rows - written)
                            ordinal = np.arange(
                                written + 1,
                                written + chunk_rows + 1,
                                dtype=np.float64,
                            )
                            times = previous_time + ordinal * step
                            arrays = [
                                pa.array(times, type=field.type)
                                if field.name == time_col
                                else pa.nulls(chunk_rows, type=field.type)
                                for field in schema
                            ]
                            writer.write_table(
                                pa.Table.from_arrays(arrays, schema=schema),
                                row_group_size=ROW_GROUP_SIZE,
                            )
                            written += chunk_rows
                            output_offset += chunk_rows

                        gap_ranges.append(
                            [gap_start, gap_start + gap_rows])
                        cursor = local_index

                    if cursor < batch_rows:
                        segment = batch.slice(cursor, batch_rows - cursor)
                        writer.write_batch(
                            segment, row_group_size=ROW_GROUP_SIZE)
                        output_offset += segment.num_rows
                    source_offset += batch_rows

        expected_rows = len(tvals) + int(missing_before.sum())
        if source_offset != len(tvals) or output_offset != expected_rows:
            raise RuntimeError("gap expansion row count mismatch")
        os.replace(tmp_path, parquet_path)
        return gap_ranges
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


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


def build_pyramid(parquet_path: Path, pyr_dir: Path, time_col: str,
                  *, inf_counts: dict | None = None):
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
                if inf_counts is not None:
                    inf_counts[c] = inf_counts.get(c, 0) + int(np.isinf(arr).sum())
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
               assume_fs: float | None = None,
               time_mode: str = "auto",
               time_column: str | None = None,
               uploader_name: str | None = None,
               description: str = "", component_ids: dict | None = None) -> dict:
    """Ingest one test while excluding lifecycle operations for that name.

    source_name: original file name for meta.source_file — API uploads
    stream into raw.csv, so csv_path.name would lose what the user sent.
    assume_fs: sample rate to assume when the time column is unusable; a
    perfect uniform axis is generated instead of failing (default DEFAULT_FS_HZ).
    time_mode: ``auto`` detects a time column, ``column`` uses the exact
    existing ``time_column``, and ``generated`` creates/replaces it from row
    number and ``assume_fs``.
    uploader_name: optional self-reported attribution stored as immutable
    provenance in the resulting metadata and lifecycle status.
    description: optional plain text captured by the upload session. Later
    edits change meta.json only; the initial upload manifest stays immutable.
    """
    with test_write(name):
        return _ingest_csv(
            csv_path, name, copy_raw, source_name, assume_fs,
            time_mode, time_column, uploader_name, description, component_ids)


def _ingest_csv(csv_path: Path, name: str, copy_raw: bool = False,
                source_name: str | None = None,
                assume_fs: float | None = None,
                time_mode: str = "auto",
                time_column: str | None = None,
                uploader_name: str | None = None,
                description: str = "", component_ids: dict | None = None) -> dict:
    description = TypeAdapter(Description).validate_python(description)
    if component_ids is not None:
        from .components import ids
        component_ids = ids(component_ids)
    if uploader_name is not None:
        uploader_name = normalize_uploader_name(uploader_name)
    provenance = (
        {"uploader_name": uploader_name}
        if uploader_name is not None
        else {}
    )
    if description:
        provenance["description"] = description
    if component_ids is not None:
        provenance["components"] = component_ids
    csv_path = Path(csv_path)
    test_dir = TESTS_DIR / name
    test_dir.mkdir(parents=True, exist_ok=True)
    write_status(test_dir, "ingesting", **provenance)
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
        if time_mode not in {"auto", "column", "generated"}:
            raise ValueError(
                "time_mode must be 'auto', 'column', or 'generated'")
        requested_time_col = time_column or ""
        generate_axis = time_mode == "generated"
        if time_mode == "column":
            if not requested_time_col.strip():
                raise ValueError(
                    "time_column is required when time_mode='column'")
            if requested_time_col not in src_columns:
                preview = ", ".join(src_columns[:20])
                raise ValueError(
                    f"time column '{requested_time_col}' was not found; "
                    f"available columns: {preview}")
            time_col = requested_time_col
        elif time_mode == "generated":
            time_col = requested_time_col.strip() or "time_s"
        else:
            detected_time_col = detect_time_column(src_columns)
            if detected_time_col is None:
                # No source signal is silently sacrificed as a fake clock.
                # Auto mode adds elapsed seconds using the fallback rate.
                time_col = "time_s"
                generate_axis = True
            else:
                time_col = detected_time_col

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
        if not numeric_cols:
            raise ValueError("CSV has no numeric signal columns to analyze")
        for data_col in numeric_cols:
            if time_col in {f"{data_col}__min", f"{data_col}__max"}:
                raise ValueError(
                    f"time column '{time_col}' conflicts with pyramid fields "
                    f"generated for data column '{data_col}'; choose another "
                    "time-column name")

        generated_fs: float | None = None
        if generate_axis:
            generated_fs = float(
                assume_fs if assume_fs is not None else DEFAULT_FS_HZ)
            if not np.isfinite(generated_fs) or generated_fs <= 0:
                raise ValueError(
                    f"assumed sample rate must be > 0, got {generated_fs}")
            row_index_col = "__ptt_row_index"
            while row_index_col in src_columns:
                row_index_col += "_"
            lf = lf.with_row_index(row_index_col)
            time_expr = (
                pl.col(row_index_col).cast(pl.Float64) / generated_fs
            ).alias(time_col)
        else:
            time_dtype = src_schema[time_col]
            time_expr = (
                pl.col(time_col).cast(pl.Float64)
                if time_dtype.is_numeric()
                else _time_seconds_expr(time_col, decimal_comma)
            )
        lf = lf.select([time_expr]
                       + [pl.col(c).cast(pl.Float64) for c in numeric_cols])

        # 2) stream CSV -> parquet
        parquet_path = test_dir / "data.parquet"
        lf.sink_parquet(parquet_path, row_group_size=ROW_GROUP_SIZE,
                        statistics=True)

        # 3) basic facts. The full (single) time column is read once: it is
        #    the cheapest robust way to infer the normal sample interval,
        #    identify acquisition gaps across the WHOLE file, and support
        #    quantized clocks whose timestamp resolution is below the sample
        #    rate. Column order is exactly what the select above wrote.
        columns = [time_col] + numeric_cols
        tvals = pl.read_parquet(parquet_path, columns=[time_col])[time_col] \
            .to_numpy()
        n_rows = len(tvals)
        if n_rows < 2:
            raise ValueError("need at least 2 samples to form a series")
        source_n_rows = n_rows

        source_quality = (
            {"version": 1, "checked": False, "column": None, "n_rows": n_rows,
             "axis_reason": ("generated_requested" if time_mode == "generated"
                             else "no_time_column")}
            if generate_axis else inspect_source_time(tvals, time_col)
        )

        # Explicit generated time is always uniform. Measured time uses normal
        # local intervals so one long dropout cannot bias the reported Hz.
        measured_timing = (
            None if generate_axis else _measured_timing(tvals)
        )
        dt = 1.0 / generated_fs if generated_fs is not None else None
        if measured_timing is not None:
            dt = measured_timing["dt"]
            source_quality.update({
                "axis_reason": "measured",
                "gap_count": measured_timing["gap_count"],
                "gap_examples": [
                    {"row": int(i + 1),
                     "previous_s": float(tvals[i - 1]),
                     "time_s": float(tvals[i]),
                     "missing_rows": int(measured_timing["missing_before"][i])}
                    for i in np.flatnonzero(
                        measured_timing["missing_before"])[:EXAMPLE_LIMIT]
                ],
            })

        source_time_origin_s: float | None = None
        gap_ranges: list[list[int]] = []
        gap_count = 0
        missing_rows = 0
        if generate_axis:
            fs = generated_fs
            t_start = 0.0
            time_source = "generated"
            quantized = False
            jitter_warn = False
        elif dt is None:
            source_quality["axis_reason"] = "invalid_source_time"
            # Time column is unusable — non-finite/unparseable, non-increasing,
            # or a single repeated coarse timestamp (the low-resolution clock
            # case, e.g. every row logged as ``19:39,2``). The bulk samples are
            # still good, so rather than fail the whole ingest, GENERATE a
            # perfect uniform axis at the assumed rate and mark it as such.
            fs = float(
                assume_fs if assume_fs is not None else DEFAULT_FS_HZ)
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
            fs = float(1.0 / dt)
            if not np.isfinite(fs) or fs <= 0:
                raise ValueError(f"derived sample rate must be > 0, got {fs}")
            source_time_origin_s = float(tvals[0])
            t_start = 0.0
            time_source = "measured"
            quantized = measured_timing["quantized"]
            jitter_warn = measured_timing["jitter_warning"]
            gap_count = measured_timing["gap_count"]
            missing_rows = measured_timing["missing_rows"]

            # Clock and epoch values describe when acquisition happened, but
            # every analysis/edit/export path uses elapsed seconds. Preserve
            # the measured spacing while making the first sample exactly zero.
            # The original origin remains in metadata for traceability.
            tmp_parquet = parquet_path.with_name(parquet_path.name + ".tmp")
            (pl.scan_parquet(parquet_path)
             .with_columns(
                 (pl.col(time_col) - source_time_origin_s).alias(time_col))
             .sink_parquet(tmp_parquet, row_group_size=ROW_GROUP_SIZE,
                           statistics=True))
            os.replace(tmp_parquet, parquet_path)
            tvals = tvals - source_time_origin_s
            if missing_rows:
                gap_ranges = _insert_missing_rows(
                    parquet_path,
                    time_col,
                    tvals,
                    measured_timing["missing_before"],
                )
                n_rows = source_n_rows + missing_rows
                logger.warning(
                    "ingest '%s': preserved %d timestamp gap(s) by inserting "
                    "%d NaN rows at %.6g Hz",
                    name, gap_count, missing_rows, fs,
                )

        # 4) pyramid + NaN scan
        inf_counts: dict[str, int] = {}
        nan_counts, level_rows = build_pyramid(parquet_path, test_dir / "pyramid",
                                               time_col, inf_counts=inf_counts)

        meta = {
            "name": name,
            "source_file": source_name or csv_path.name,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "n_rows": n_rows,
            "n_columns": len(columns),
            "columns": columns,
            "time_column": time_col,
            "time_unit": "s",
            "fs_hz": fs,
            "duration_s": float(n_rows * dt) if dt else None,
            "t_start": t_start,
            "time_source": time_source,
            "source_time_origin_s": source_time_origin_s,
            "source_n_rows": source_n_rows,
            "source_time_quality": source_quality,
            "time_gap_count": gap_count,
            "missing_rows_inserted": missing_rows,
            "time_gap_seconds": float(missing_rows * dt),
            "time_gap_ranges": gap_ranges,
            # Observation provenance survives later interpolation/zero-fill.
            "acquisition_gap_ranges": gap_ranges,
            "csv_separator": separator,
            "decimal_comma": decimal_comma,
            "time_quantized": quantized,
            "skipped_columns": skipped,
            "jitter_warning": jitter_warn,
            "nan_counts": {c: n for c, n in nan_counts.items() if n > 0},
            "inf_counts": {c: n for c, n in inf_counts.items() if n > 0},
            "nan_policy": "keep_gaps",
            "pyramid_levels": PYRAMID_LEVELS,
            "pyramid_rows": level_rows,
            "ingest_seconds": round(time.time() - t0, 1),
        }
        if uploader_name is not None:
            meta["uploader_name"] = uploader_name
        if description:
            meta["description"] = description
        if component_ids is not None:
            meta["components"] = component_ids
            meta["components_revision"] = 0
        write_json_atomic(test_dir / "meta.json", meta)
        write_status(test_dir, "ready", **provenance)
        logger.info("ingest '%s': ready — %d rows x %d cols in %.1f s",
                    name, n_rows, len(columns), meta["ingest_seconds"])
        return meta
    except Exception as e:
        write_status(test_dir, "error", repr(e), **provenance)
        logger.exception("ingest '%s': FAILED", name)
        raise


if __name__ == "__main__":
    csv, name = sys.argv[1], sys.argv[2]
    meta = ingest_csv(Path(csv), name)
    print(json.dumps(meta, indent=2))
