"""Read side: test registry, windowed range reads, pyramid level selection."""

import csv
from contextlib import closing
import io
import json
import math
import os
import tempfile
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import polars as pl
import pyarrow as pa
import pyarrow.csv as pa_csv
import pyarrow.parquet as pq

from . import analysis_metadata as analysis
from . import export_progress as progress
from .config import MAX_POINTS_RAW, POINT_BUDGET_CAP, PYRAMID_LEVELS, TESTS_DIR
from .quality import quality_summary


def list_tests() -> list[dict]:
    out = []
    if not TESTS_DIR.exists():
        return out
    for d in sorted(TESTS_DIR.iterdir()):
        if not d.is_dir():
            continue
        status = _read_json(d / "status.json") or {"status": "unknown"}
        meta = _read_json(d / "meta.json") or {}
        lifecycle = status.get("status")
        receiving_bytes = status.get("received_bytes")
        out.append({"name": d.name, "status": lifecycle,
                    "error": status.get("error"),
                    "data_quality": quality_summary(meta),
                    "quality_revision": _file_revision(d / "meta.json"),
                    "n_rows": meta.get("n_rows"), "fs_hz": meta.get("fs_hz"),
                    "duration_s": meta.get("duration_s"),
                    "n_columns": meta.get("n_columns"),
                    "time_column": meta.get("time_column"),
                    "time_source": meta.get("time_source"),
                    "time_unit": meta.get("time_unit"),
                    "source_n_rows": meta.get("source_n_rows"),
                    "time_gap_count": meta.get("time_gap_count"),
                    "missing_rows_inserted":
                        meta.get("missing_rows_inserted"),
                    "time_gap_seconds": meta.get("time_gap_seconds"),
                    # Upload-history fields.  created_at falls back to the
                    # directory creation time so receiving/ingesting/failed
                    # tests (no meta.json yet) still sort chronologically.
                    "source_file": (meta.get("source_file")
                                    or status.get("source_file")),
                    "uploader_name": (meta.get("uploader_name")
                                      or status.get("uploader_name")),
                    "description": meta.get("description", status.get("description", "")),
                    "components": meta.get("components", status.get("components")),
                    **({"component_sets": meta.get("component_sets", status.get("component_sets")),
                        "component_sets_revision": meta.get("component_sets_revision", status.get("component_sets_revision", 0))}
                       if "component_sets" in meta or "component_sets" in status else {}),
                    "created_at": meta.get("created_at") or _dir_created_at(d),
                    "edited_at": meta.get("edited_at"),
                    "ingest_seconds": meta.get("ingest_seconds"),
                    # A random-access staging file can be sparse and its
                    # logical st_size can jump ahead of durable progress.
                    # Receiving progress therefore comes only from committed
                    # chunk receipts published in status.json.
                    "size_bytes": (
                        int(receiving_bytes)
                        if lifecycle == "receiving"
                        and isinstance(receiving_bytes, int)
                        else _cached_dir_size(d, lifecycle)),
                    "upload_id": status.get("upload_id"),
                    "received_bytes": status.get("received_bytes"),
                    "total_bytes": status.get("total_bytes"),
                    "received_chunks": status.get("received_chunks"),
                    "total_chunks": status.get("total_chunks")})
    return out


def _file_revision(path: Path) -> str | None:
    """An opaque detail-refresh key; edited_at has only second precision."""
    try:
        return str(path.stat().st_mtime_ns)
    except OSError:
        return None


# name -> (dir mtime_ns, size_bytes) for the last full rglob.  list_tests runs
# on every 2 s poll and before every upload batch; rglob-ing every file of every
# test each time is O(library size) for a value only the Uploads page reads
# (bug 2.9).  A 'ready' test's files change only via a rebuild (which flips its
# status away from 'ready' first) or an atomic JSON write — every such change
# renames a file in the dir and so bumps the dir's own mtime, making the mtime a
# safe cache key.  Concurrent readers hold catalog_read (shared) and dict ops are
# atomic under the GIL, so a race at worst recomputes; a stale key can't be wrong
# because it is mtime-gated.
_size_cache: dict[str, tuple[int, int]] = {}


def _cached_dir_size(d: Path, status: str | None) -> int:
    # Only 'ready' tests have stable files.  A test mid-write (receiving/
    # ingesting/rebuilding) can grow via in-place appends that do NOT bump the
    # dir mtime (raw.csv during receiving, data.parquet during ingest), so its
    # size must be recomputed live — that live size is exactly what the Uploads
    # progress row shows.
    if status != "ready":
        return _dir_size(d)
    try:
        mtime = d.stat().st_mtime_ns
    except OSError:
        return _dir_size(d)
    cached = _size_cache.get(d.name)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    size = _dir_size(d)
    _size_cache[d.name] = (mtime, size)
    return size


def _dir_created_at(d: Path) -> str | None:
    """Directory creation time as UTC ISO (same format meta.created_at uses,
    so lexicographic sort works across both sources)."""
    try:
        st = d.stat()
    except OSError:
        return None
    ts = getattr(st, "st_birthtime", None) or st.st_ctime
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(
        timespec="seconds")


def _dir_size(d: Path) -> int:
    """Total bytes on disk. For a 'receiving' test this grows live with the
    transfer (raw.csv is being appended), which the UI uses as progress."""
    total = 0
    for p in d.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            pass  # file replaced/removed mid-walk (status.json swaps)
    return total


def _read_json(p: Path):
    try:
        return json.loads(p.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def write_json_atomic(path: Path, payload: dict) -> None:
    """Replace a JSON file atomically so lock-free status reads stay valid."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        # Windows: os.replace raises PermissionError while any reader has the
        # destination open (readers don't pass FILE_SHARE_DELETE). Status
        # polling reads these files every couple of seconds, so retry briefly
        # instead of letting a background job die on a transient collision.
        for attempt in range(6):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.02 * (attempt + 1))
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def get_meta(name: str) -> dict | None:
    return _read_json(TESTS_DIR / name / "meta.json")


def get_status(name: str) -> dict:
    return _read_json(TESTS_DIR / name / "status.json") or {"status": "missing"}


def to_json_list(arr) -> list:
    """Single finite-safe JSON serializer for every window/TP response.

    Rounds to 6 dp and maps NaN *and* ±inf to None: Starlette's JSONResponse
    renders with allow_nan=False, so any non-finite value (an inf CSV cell, an
    overflowed filter/detrend) would otherwise raise and 500 the whole window.
    np.round + tolist does the rounding in C; only the None-substitution runs
    in Python, and the all-finite fast path skips even that (bug 2.7). Replaces
    the old _nan_to_none / _json_numbers pair (bug 6.10)."""
    a = np.asarray(arr, dtype=np.float64)
    finite = np.isfinite(a)
    values = np.round(a, 6).tolist()
    if bool(finite.all()):
        return values
    return [v if f else None for v, f in zip(values, finite.tolist())]


def plot_budget(px: int) -> int:
    """Points to aim for in one window response: ~2 per pixel, floored at 1000
    and capped at POINT_BUDGET_CAP. Drives BOTH the raw-vs-envelope threshold
    and (bug 2.3) the pyramid-level + bucket-merge selection, so a small grid
    cell no longer receives an 8000-point envelope it cannot display."""
    return min(max(2 * px, 1000), POINT_BUDGET_CAP)


def pick_pyramid_level(n_raw: int, budget: int) -> int:
    """Finest pyramid level whose bucket count fits `budget` (coarsest if none
    do). Selecting against the per-plot budget rather than the hard cap is the
    payload half of bug 2.3."""
    for f in PYRAMID_LEVELS:
        if n_raw / f <= budget:
            return f
    return PYRAMID_LEVELS[-1]


def resolve_window_display(n_raw: int, budget: int,
                           display: str = "auto") -> tuple[str, int]:
    """Resolve a requested plot representation to ``(mode, level)``.

    ``auto`` preserves the original zoom/sample-count switch. ``line`` always
    returns one y-value per x-value; ranges above the response hard cap are
    stride-sampled so a manual choice cannot send millions of browser points.
    ``envelope`` always returns min/max buckets from the pyramid.
    """
    if display not in {"auto", "line", "envelope"}:
        raise ValueError(
            "display must be one of: auto, line, envelope")

    if display == "line":
        return "raw", max(1, math.ceil(n_raw / POINT_BUDGET_CAP))

    raw_limit = max(MAX_POINTS_RAW, budget)
    if display == "auto" and n_raw <= raw_limit:
        return "raw", 1

    return "envelope", pick_pyramid_level(n_raw, budget)


def bucket_minmax(arr: np.ndarray, factor: int):
    """Per-bucket nanmin/nanmax. Pads the tail bucket with NaN. Used by the
    ingest pyramid build and by dsp's filtered/spectrum envelope reductions."""
    n = len(arr)
    n_buckets = -(-n // factor)
    if n_buckets * factor != n:
        arr = np.concatenate([arr, np.full(n_buckets * factor - n, np.nan)])
    a = arr.reshape(n_buckets, factor)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN buckets
        return np.nanmin(a, axis=1), np.nanmax(a, axis=1)


def merge_over_cap(t: np.ndarray, series: dict, budget: int):
    """Merge adjacent envelope buckets (min of mins / max of maxes) until the
    trace fits `budget`. Striding would drop spikes; merging preserves extrema.
    `series` maps col -> (min_arr, max_arr). Returns (t, series, merge_factor).

    Shared by store.read_window and dsp.filtered_window so the over-cap rule
    lives in one place (bug 6.1)."""
    merge = max(1, math.ceil(len(t) / budget))
    if merge > 1:
        nb = -(-len(t) // merge)
        pad = nb * merge - len(t)

        def _merged(arr, fn):
            if pad:
                arr = np.concatenate([arr, np.full(pad, np.nan)])
            with np.errstate(invalid="ignore"):
                return fn(arr.reshape(nb, merge), axis=1)

        series = {c: (_merged(mn, np.nanmin), _merged(mx, np.nanmax))
                  for c, (mn, mx) in series.items()}
        t = t[::merge][:nb]
    return t, series, merge


def window_bounds(meta: dict, t0: float | None,
                  t1: float | None) -> tuple[int, int]:
    """Clamp a [t0, t1] time window to a half-open sample-row range.

    Single source of the time->index rule for every window-serving path
    (read_window / read_xy / dsp filters+spectra / CSV export)."""
    fs = meta["fs_hz"]
    n_rows = meta["n_rows"]
    t_start = meta.get("t_start") or 0.0
    duration = n_rows / fs
    lo = t_start if t0 is None else max(t0, t_start)
    hi = t_start + duration if t1 is None else min(t1, t_start + duration)
    i0 = max(0, int((lo - t_start) * fs))
    i1 = min(n_rows, int(math.ceil((hi - t_start) * fs)) + 1)
    return i0, max(i0, i1)


def read_window(name: str, cols: list[str], t0: float | None,
                t1: float | None, px: int,
                display: str = "auto") -> dict:
    """Serve a plot window using the requested line/envelope representation."""
    meta = get_meta(name)
    if meta is None:
        raise FileNotFoundError(name)
    tcol = meta["time_column"]
    i0, i1 = window_bounds(meta, t0, t1)
    n_raw = i1 - i0

    budget = plot_budget(px)
    test_dir = TESTS_DIR / name
    resolved_mode, level = resolve_window_display(n_raw, budget, display)

    if resolved_mode == "raw":
        query = pl.scan_parquet(test_dir / "data.parquet").slice(i0, n_raw)
        if level > 1:
            query = query.gather_every(level)
        df = query.select([tcol] + cols).collect()
        return {
            "mode": "raw", "level": level, "n_raw": n_raw,
            "i0": i0, "i1": i1,
            "analysis": analysis.source_context(name, meta, cols, i0, i1, t0=t0, t1=t1),
            "t": to_json_list(df[tcol].to_numpy()),
            "series": {c: to_json_list(df[c].to_numpy().astype(np.float64))
                       for c in cols},
        }

    b0, b1 = i0 // level, -(-i1 // level)
    lvl_cols = [tcol]
    for c in cols:
        lvl_cols += [f"{c}__min", f"{c}__max"]
    df = (pl.scan_parquet(test_dir / "pyramid" / f"L{level}.parquet")
          .slice(b0, b1 - b0).select(lvl_cols).collect())

    t = df[tcol].to_numpy()
    series = {c: (df[f"{c}__min"].to_numpy().astype(np.float64),
                  df[f"{c}__max"].to_numpy().astype(np.float64))
              for c in cols}

    # merge adjacent buckets if the level's trace still exceeds the budget
    t, series, merge = merge_over_cap(t, series, budget)

    return {
        "mode": "envelope", "level": level * merge, "n_raw": n_raw,
        "i0": i0, "i1": i1,
        "analysis": analysis.source_context(name, meta, cols, i0, i1, t0=t0, t1=t1),
        "t": to_json_list(t),
        "series": {c: {"min": to_json_list(mn), "max": to_json_list(mx)}
                   for c, (mn, mx) in series.items()},
    }


def _testpoint_bounds(meta: dict, testpoints: list[dict],
                      tp: dict) -> tuple[int, int]:
    """Resolve one test point to an exact, half-open parquet row range.

    Saved row indices are authoritative.  Older imported test-point files may
    only contain times, in which case the same sample-rate conversion used by
    the split editor is applied.  An open-ended point stops at the next point
    or at the end of the dataset.
    """
    fs = float(meta["fs_hz"])
    n_rows = int(meta["n_rows"])
    t_start = float(meta.get("t_start") or 0.0)

    def start_index(point: dict) -> int:
        value = point.get("start_idx")
        if value is not None:
            return int(value)
        return int(round((float(point["start_s"]) - t_start) * fs))

    i0 = start_index(tp)
    end_idx = tp.get("end_idx")
    if end_idx is not None:
        i1 = int(end_idx)
    elif tp.get("end_s") is not None:
        i1 = int(round((float(tp["end_s"]) - t_start) * fs))
    else:
        later = [point for point in testpoints
                 if point is not tp
                 and float(point["start_s"]) > float(tp["start_s"])]
        i1 = start_index(min(later, key=lambda point: float(point["start_s"]))) \
            if later else n_rows

    i0 = min(max(i0, 0), n_rows)
    i1 = min(max(i1, 0), n_rows)
    if i1 <= i0:
        raise ValueError(
            f"test point {tp.get('id')} has an empty or reversed range")
    return i0, i1


def _iter_parquet_slice(path: Path, columns: list[str],
                        i0: int, i1: int):
    """Yield ``(absolute_row_start, RecordBatch)`` for exactly [i0, i1).

    Selecting row groups before iterating avoids scanning from row zero for a
    late test point, and fixed-size batches keep memory independent of point
    duration.
    """
    with pq.ParquetFile(path) as parquet:
        progress.checkpoint()
        row_start = 0
        for row_group in range(parquet.num_row_groups):
            progress.checkpoint()
            n_group = parquet.metadata.row_group(row_group).num_rows
            group_end = row_start + n_group
            if group_end <= i0:
                row_start = group_end
                continue
            if row_start >= i1:
                break

            batch_start = row_start
            for batch in parquet.iter_batches(
                    row_groups=[row_group], columns=columns, batch_size=65536):
                progress.checkpoint()
                batch_end = batch_start + batch.num_rows
                take0 = max(i0, batch_start)
                take1 = min(i1, batch_end)
                if take1 > take0:
                    yield take0, batch.slice(take0 - batch_start, take1 - take0)
                batch_start = batch_end
                if batch_start >= i1:
                    break
            row_start = group_end


def _batch_float64(batch, column: str) -> np.ndarray:
    values = batch.column(batch.schema.get_field_index(column))
    try:
        return values.to_numpy(zero_copy_only=False).astype(
            np.float64, copy=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"column '{column}' is not numeric") from exc


def testpoint_range(name: str, tp_id: int) -> tuple[int, int]:
    """Public row-range resolution for one saved test point (CSV export)."""
    meta = get_meta(name)
    if meta is None:
        raise FileNotFoundError(name)
    points = read_testpoints(name).get("test_points", [])
    tp = next((p for p in points if p.get("id") == tp_id), None)
    if tp is None:
        raise KeyError(tp_id)
    return _testpoint_bounds(meta, points, tp)


TP_ID_COLUMN = "test_point_id"


def tp_export_source_alias(columns: list[str]) -> str:
    """Stable across column subsets; never overwrite an acquisition channel."""
    alias = f"source_{TP_ID_COLUMN}"
    while alias in columns:
        alias = f"source_{alias}"
    return alias


def stream_csv(name: str, columns: list[str], i0: int, i1: int,
               tp_id: int | None = None, source_id_alias: str | None = None):
    """Yield CSV bytes (header first) for rows [i0, i1) of data.parquet.

    Plain generator with no locking of its own: the caller must hold the
    read locks for the stream's whole lifetime (a StreamingResponse body
    runs after its endpoint returned — see locks.data_read)."""
    path = TESTS_DIR / name / "data.parquet"
    headers = list(columns)
    if tp_id is not None:
        if TP_ID_COLUMN in headers:
            if not source_id_alias or source_id_alias in headers:
                raise ValueError("a unique source ID alias is required")
            headers[headers.index(TP_ID_COLUMN)] = source_id_alias
        headers.append(TP_ID_COLUMN)
    wrote_header = False
    for _, batch in _iter_parquet_slice(path, columns, i0, i1):
        # select() pins the column order — parquet readers may return the
        # file's schema order, and a CSV header must match the data.
        buf = io.BytesIO()
        batch = batch.select(columns)
        if tp_id is not None:
            # IDs are identifiers, not measurements. Strings also preserve
            # legacy integer IDs outside Arrow's int64 range without overflow.
            batch = pa.RecordBatch.from_arrays(
                [*batch.columns, pa.repeat(str(tp_id), batch.num_rows)],
                names=headers)
        pa_csv.write_csv(
            batch, buf,
            write_options=pa_csv.WriteOptions(include_header=not wrote_header))
        wrote_header = True
        yield buf.getvalue()
    if not wrote_header:
        # empty range: still emit the header so the download is valid CSV
        text = io.StringIO()
        csv.writer(text, lineterminator="\n").writerow(headers)
        yield text.getvalue().encode()


def read_testpoint_trace(name: str, tp_id: int, cols: list[str],
                         max_points: int = 3000) -> dict:
    """Return exact-boundary traces with time relative to one test point.

    Small points are returned raw.  Longer points are reduced with an ordered
    min/max trace: first and last samples are retained, and each interior
    bucket contributes its minimum and maximum at their real sample times.
    Unlike stride sampling this cannot silently discard a narrow vibration
    spike.  ``max_points`` is a strict cap for every returned trace.
    """
    if not 4 <= int(max_points) <= POINT_BUDGET_CAP:
        raise ValueError(
            f"max_points must be between 4 and {POINT_BUDGET_CAP}")

    meta = get_meta(name)
    if meta is None:
        raise FileNotFoundError(name)
    points = read_testpoints(name).get("test_points", [])
    tp = next((point for point in points if point.get("id") == tp_id), None)
    if tp is None:
        raise KeyError(tp_id)

    i0, i1 = _testpoint_bounds(meta, points, tp)
    n_raw = i1 - i0
    tcol = meta["time_column"]
    columns = list(dict.fromkeys([tcol, *cols]))
    batches = _iter_parquet_slice(
        TESTS_DIR / name / "data.parquet", columns, i0, i1)

    if n_raw <= max_points:
        t_parts: list[np.ndarray] = []
        value_parts: dict[str, list[np.ndarray]] = {col: [] for col in cols}
        for _, batch in batches:
            t_parts.append(_batch_float64(batch, tcol))
            for col in cols:
                value_parts[col].append(_batch_float64(batch, col))
        if not t_parts:
            raise ValueError(f"test point {tp_id} contains no samples")
        absolute_t = np.concatenate(t_parts)
        origin = float(absolute_t[0])
        relative_t = absolute_t - origin
        series = {
            col: {"t": to_json_list(relative_t),
                  "y": to_json_list(np.concatenate(value_parts[col]))}
            for col in cols
        }
        mode = "raw"
        level = 1
        last_t = float(absolute_t[-1])
    else:
        # Two slots per bucket are required for min and max.  The endpoints
        # each consume one slot and are deliberately excluded from buckets.
        interior_n = n_raw - 2
        bucket_count = min(interior_n, (max_points - 2) // 2)
        edges = 1 + (np.arange(bucket_count + 1, dtype=np.int64)
                     * interior_n // bucket_count)
        level = int(math.ceil(interior_n / bucket_count))

        bucket_t = np.full(bucket_count, np.nan)
        min_value = {col: np.full(bucket_count, np.nan) for col in cols}
        max_value = {col: np.full(bucket_count, np.nan) for col in cols}
        min_index = {col: np.full(bucket_count, -1, dtype=np.int64)
                     for col in cols}
        max_index = {col: np.full(bucket_count, -1, dtype=np.int64)
                     for col in cols}
        min_t = {col: np.full(bucket_count, np.nan) for col in cols}
        max_t = {col: np.full(bucket_count, np.nan) for col in cols}
        first_value: dict[str, float] = {}
        last_value: dict[str, float] = {}
        origin = math.nan
        last_t = math.nan

        for absolute_start, batch in batches:
            t = _batch_float64(batch, tcol)
            rel_start = absolute_start - i0
            rel_end = rel_start + len(t)
            values = {col: _batch_float64(batch, col) for col in cols}

            if rel_start == 0:
                origin = float(t[0])
                for col in cols:
                    first_value[col] = float(values[col][0])
            if rel_start <= n_raw - 1 < rel_end:
                offset = n_raw - 1 - rel_start
                last_t = float(t[offset])
                for col in cols:
                    last_value[col] = float(values[col][offset])

            # Interior (endpoint-excluded) portion of this batch, in relative
            # [1, n_raw-1) index space and as a batch-local slice.
            interior_start = max(rel_start, 1)
            interior_end = min(rel_end, n_raw - 1)
            if interior_end <= interior_start:
                continue
            local0 = interior_start - rel_start
            local1 = interior_end - rel_start
            t_int = t[local0:local1]
            n_int = local1 - local0

            # Bucket id of every interior sample (non-decreasing → maximal
            # constant runs are contiguous), then the start offset of each run.
            # np.*.reduceat over these run starts vectorizes the per-bucket
            # min/max that used to be a Python loop with several small numpy
            # calls per bucket per column (2.6). run_bucket is unique within a
            # batch (buckets are contiguous), so a bucket spans at most the two
            # batches on either side of a seam; the scalar merge below keeps the
            # running min/max across that seam exactly as before.
            buckets = np.searchsorted(
                edges, np.arange(interior_start, interior_end),
                side="right") - 1
            np.clip(buckets, 0, bucket_count - 1, out=buckets)
            run_start = np.concatenate(
                ([0], np.flatnonzero(np.diff(buckets)) + 1))
            run_bucket = buckets[run_start]
            n_runs = len(run_start)
            run_id = np.repeat(np.arange(n_runs),
                               np.diff(np.append(run_start, n_int)))
            positions = np.arange(n_int)

            # bucket_t = time of a bucket's first interior sample, fixed by the
            # first batch that reaches it (isfinite guard = "not yet set").
            unset = ~np.isfinite(bucket_t[run_bucket])
            bucket_t[run_bucket[unset]] = t_int[run_start[unset]]

            for col in cols:
                v_int = values[col][local0:local1]
                finite = np.isfinite(v_int)
                lo_masked = np.where(finite, v_int, np.inf)
                hi_masked = np.where(finite, v_int, -np.inf)
                run_min = np.minimum.reduceat(lo_masked, run_start)
                run_max = np.maximum.reduceat(hi_masked, run_start)
                run_finite = np.add.reduceat(
                    finite.astype(np.int64), run_start)
                # First position of each run's min/max (ties → earliest, as the
                # old argmin/argmax over the finite subset did). Non-extremum
                # samples map to n_int so the min-of-positions ignores them.
                lo_pos = np.minimum.reduceat(
                    np.where(lo_masked == run_min[run_id], positions, n_int),
                    run_start)
                hi_pos = np.minimum.reduceat(
                    np.where(hi_masked == run_max[run_id], positions, n_int),
                    run_start)
                mv, mi, mt = min_value[col], min_index[col], min_t[col]
                xv, xi, xt = max_value[col], max_index[col], max_t[col]
                for r in range(n_runs):
                    if run_finite[r] == 0:
                        continue
                    bucket = int(run_bucket[r])
                    lo_local = int(lo_pos[r])
                    hi_local = int(hi_pos[r])
                    lo_value = float(run_min[r])
                    hi_value = float(run_max[r])
                    if mi[bucket] < 0 or lo_value < mv[bucket]:
                        mv[bucket] = lo_value
                        mi[bucket] = interior_start + lo_local
                        mt[bucket] = float(t_int[lo_local])
                    if xi[bucket] < 0 or hi_value > xv[bucket]:
                        xv[bucket] = hi_value
                        xi[bucket] = interior_start + hi_local
                        xt[bucket] = float(t_int[hi_local])

        if not math.isfinite(origin) or not math.isfinite(last_t):
            raise ValueError(f"test point {tp_id} contains invalid time data")

        series = {}
        for col in cols:
            trace_t = [origin]
            trace_y = [first_value[col]]
            for bucket in range(bucket_count):
                lo_idx = int(min_index[col][bucket])
                hi_idx = int(max_index[col][bucket])
                if lo_idx < 0:
                    trace_t.append(float(bucket_t[bucket]))
                    trace_y.append(math.nan)
                    continue
                extrema = [(lo_idx, min_t[col][bucket],
                            min_value[col][bucket])]
                if hi_idx != lo_idx:
                    extrema.append((hi_idx, max_t[col][bucket],
                                    max_value[col][bucket]))
                extrema.sort(key=lambda item: item[0])
                for _, sample_t, value in extrema:
                    trace_t.append(float(sample_t))
                    trace_y.append(float(value))
            trace_t.append(last_t)
            trace_y.append(last_value[col])
            relative_t = np.asarray(trace_t, dtype=np.float64) - origin
            series[col] = {"t": to_json_list(relative_t),
                           "y": to_json_list(
                               np.asarray(trace_y, dtype=np.float64))}
        mode = "envelope"

    return {
        "mode": mode,
        "level": level,
        "n_raw": n_raw,
        "i0": i0,
        "i1": i1,
        "point_budget": int(max_points),
        "time_origin_s": round(origin, 6),
        "duration_s": round(last_t - origin, 6),
        "test": name,
        "test_point": {
            "id": tp.get("id"),
            "name": tp.get("name", ""),
            "label": tp.get("label", ""),
        },
        "series": {col: {**values, "analysis": {
            **analysis.source_context(name, meta, [col], i0, i1, tp_id=tp_id),
            "method_version": "kiha-time-trace-v1", "filter": None,
            "source_centers": {"first_time_s": origin, "last_time_s": last_t},
            "display": {"mode": mode, "level": level, "point_budget": int(max_points)},
        }} for col, values in series.items()},
    }


XY_METHOD_VERSION = "kiha-xy-v2"


def iter_xy_batches(name: str, columns: list[str], i0: int, i1: int):
    """Exact source rows shared by XY display and export; caller holds data_read.

    Deduplicate before Arrow reads (X=Y or an axis is time). Close the underlying
    iterator even if serialization fails or a consumer stops early.
    """
    with closing(_iter_parquet_slice(TESTS_DIR / name / "data.parquet",
                                    list(dict.fromkeys(columns)), i0, i1)) as batches:
        yield from batches


def read_xy(name: str, x_col: str, y_cols: list[str], t0: float | None,
            t1: float | None, max_pts: int = 3000, tp_id: int | None = None) -> dict:
    """Stride display of original finite pairs, with complete native row counts.

    No coordinate rounding, sorting, interpolation or joining. Stride is applied
    relative to i0 before finite-pair removal. If it misses every finite pair,
    retain the first finite pair so a valid source cannot appear empty.
    """
    meta = get_meta(name)
    if meta is None:
        raise FileNotFoundError(name)
    if tp_id is not None and (t0 is not None or t1 is not None):
        raise ValueError("tp_id cannot be combined with t0 or t1")
    if any(t is not None and not math.isfinite(t) for t in (t0, t1)):
        raise ValueError("time bounds must be finite")
    if t0 is not None and t1 is not None and t0 > t1:
        raise ValueError("t0 must not exceed t1")
    if not isinstance(max_pts, int) or isinstance(max_pts, bool) or max_pts < 1:
        raise ValueError("max_pts must be a positive integer")
    i0, i1 = testpoint_range(name, tp_id) if tp_id is not None else window_bounds(meta, t0, t1)
    n_raw = i1 - i0
    stride = max(1, math.ceil(n_raw / max_pts))
    tcol = meta["time_column"]
    series = {y: {"x": [], "y": [], "sample_indices": [], "finite_count": 0,
                  "missing_pair_count": 0, "fallback_first_finite": False} for y in y_cols}
    first = {}; seen = 0; first_time = last_time = None
    with closing(iter_xy_batches(name, [tcol, x_col, *y_cols], i0, i1)) as batches:
        for start, batch in batches:
            times = _batch_float64(batch, tcol)
            if seen == 0:
                first_time = float(times[0]) if math.isfinite(times[0]) else None
            last_time = float(times[-1]) if math.isfinite(times[-1]) else None
            seen += batch.num_rows
            indices = np.arange(start, start + batch.num_rows, dtype=np.int64)
            sampled = (indices - i0) % stride == 0
            xv = _batch_float64(batch, x_col)
            for y, result in series.items():
                yv = xv if y == x_col else _batch_float64(batch, y)
                finite = np.isfinite(xv) & np.isfinite(yv)
                count = int(finite.sum())
                result['finite_count'] += count
                if count and y not in first:
                    index = int(np.flatnonzero(finite)[0])
                    first[y] = (int(indices[index]), float(xv[index]), float(yv[index]))
                keep = finite & sampled
                result['x'].extend(xv[keep].tolist())
                result['y'].extend(yv[keep].tolist())
                result['sample_indices'].extend(indices[keep].tolist())
    if seen != n_raw:
        raise ValueError("stored row count does not match XY source bounds")
    for y, result in series.items():
        result['missing_pair_count'] = n_raw - result['finite_count']
        if not result['x'] and y in first:
            index, x, value = first[y]
            result.update(x=[x], y=[value], sample_indices=[index], fallback_first_finite=True)
    return {"stride": stride, "n_raw": n_raw, "series": series,
            "analysis": analysis.source_context(name, meta, [x_col, *y_cols], i0, i1, tp_id=tp_id, t0=t0, t1=t1),
            "method_version": XY_METHOD_VERSION, "source": "stored", "prefilter": "none",
            "missing_values": "omit_nonfinite_pairs", "reduction": "stride",
            "i0": i0, "i1": i1, "tp_id": tp_id, "time_column": tcol,
            "time_start_s": first_time, "time_end_s": last_time,
            "fs_hz": meta.get("fs_hz"), "x_col": x_col}


def _tp_stats_fingerprint(name: str) -> list[int]:
    """Identity of everything tp_stats depends on: mtime_ns of
    testpoints.json (0 if absent) and data.parquet. TP saves, uploads and
    rebuilds all atomically replace their file, so a changed fingerprint
    invalidates the sidecar — including hand-edits of testpoints.json
    (the file is documented as human-editable)."""
    test_dir = TESTS_DIR / name

    def mtime_ns(p: Path) -> int:
        try:
            return p.stat().st_mtime_ns
        except OSError:
            return 0

    return [mtime_ns(test_dir / "testpoints.json"),
            mtime_ns(test_dir / "data.parquet")]


TP_STATS_CACHE_VERSION = 2
TP_SUMMARY_METHOD = "finite-population-v1"


def tp_stats(name: str, col: str) -> list[dict]:
    """Per-TP rounded scatter aggregates and unrounded mean/population SD,
    cached per column in a tp_stats.json sidecar. The frontend requests
    scatter axis/filter columns across ready tests, plus visible time-plot
    columns for selected tests. Without the cache each request re-scanned
    the full raw column (~60 MB for a 1 h test)."""
    cache_path = TESTS_DIR / name / "tp_stats.json"
    fingerprint = _tp_stats_fingerprint(name)
    cache = _read_json(cache_path)
    if (not isinstance(cache, dict)
            or cache.get("version") != TP_STATS_CACHE_VERSION
            or cache.get("fingerprint") != fingerprint
            or not isinstance(cache.get("columns"), dict)):
        cache = {"version": TP_STATS_CACHE_VERSION,
                 "fingerprint": fingerprint, "columns": {}}
    cached = cache["columns"].get(col)
    if cached is not None:
        return cached
    stats = _compute_tp_stats(name, col)
    cache["columns"][col] = stats
    # Written under the caller's per-test READ lock: writers (delete/rename/
    # rebuild/TP saves) are excluded, and concurrent readers at worst
    # overwrite each other's freshly added column — recomputed on the next
    # request, never wrong.
    write_json_atomic(cache_path, cache)
    return stats


def rebuild_tp_stats(name: str) -> int:
    """Recompute the tp_stats sidecar from scratch and atomically replace it.

    Recomputes exactly the columns the sidecar currently holds (the ones the
    UI has actually used) against the CURRENT test points + data, builds the
    fresh cache entirely in memory, then writes it in one atomic swap — so
    every reader keeps getting the old-but-valid averages until the instant it
    lands.  Returns the number of columns recomputed.

    Caller must hold at least a per-test read lock (data_read): _compute_tp_stats
    reads data.parquet, which an edit rebuild could otherwise swap mid-read.
    """
    if get_meta(name) is None:
        raise FileNotFoundError(name)
    cache_path = TESTS_DIR / name / "tp_stats.json"
    existing = _read_json(cache_path)
    columns = (list(existing["columns"].keys())
               if isinstance(existing, dict)
               and isinstance(existing.get("columns"), dict) else [])
    fresh = {"version": TP_STATS_CACHE_VERSION,
             "fingerprint": _tp_stats_fingerprint(name),
             "columns": {col: _compute_tp_stats(name, col)
                         for col in columns}}
    write_json_atomic(cache_path, fresh)
    return len(columns)


def _population_moments(valid: np.ndarray) -> tuple[float | None, float | None]:
    """Arithmetic mean and population SD (ddof=0) of finite float64 samples.

    Normal NumPy accumulation preserves accuracy for high-offset signals.
    Rescale only if a sum/squared deviation overflows or underflows; a finite
    engineering column must not turn a stats response into invalid JSON.
    """
    if len(valid) == 0:
        return None, None
    with np.errstate(over="ignore", invalid="ignore", under="ignore"):
        mean = float(np.mean(valid, dtype=np.float64))
        std = float(np.std(valid, dtype=np.float64, ddof=0))
    if (not np.isfinite(mean) or not np.isfinite(std)
            or (std == 0 and np.any(valid != valid[0]))):
        scale = float(np.max(np.abs(valid)))
        scaled = valid / scale if scale else valid
        if not np.isfinite(mean):
            mean = float(np.mean(scaled, dtype=np.float64)) * scale
        # SD <= max(abs(x)) for a population. Clamp rounding at that bound.
        std = min(1.0, float(np.std(scaled, dtype=np.float64, ddof=0))) * scale
    return mean, std


def _compute_tp_stats(name: str, col: str) -> list[dict]:
    """Per-test-point aggregates and summary of one column (finite values only).

    Row ranges come from the same _testpoint_bounds resolver the CSV export and
    TP-trace paths use, so a test point covers exactly the same samples in the
    scatter aggregate as in its downloaded/plotted trace (bug 6.3)."""
    meta = get_meta(name)
    if meta is None:
        raise FileNotFoundError(name)
    tps = sorted(read_testpoints(name)["test_points"],
                 key=lambda tp: tp["start_s"])

    v = (pl.scan_parquet(TESTS_DIR / name / "data.parquet")
         .select([col]).collect())[col].to_numpy().astype(np.float64)

    out = []
    for tp in tps:
        try:
            si, ei = _testpoint_bounds(meta, tps, tp)
            sl = v[si:ei]
        except ValueError:
            si, ei = None, None
            sl = v[:0]  # empty/reversed range -> reported as n=0
        valid = sl[np.isfinite(sl)]
        mean, std = _population_moments(valid)
        stat = {"id": tp["id"], "name": tp["name"], "label": tp["label"],
                "n": int(len(sl)), "n_valid": int(len(valid)),
                "summary": {"method": TP_SUMMARY_METHOD, "mean": mean,
                            "std_population": std, "i0": si, "i1": ei}}
        if len(valid):
            # Preserve the existing rounded scatter/filter fields; the header
            # uses summary.mean so small-unit signals do not round to zero.
            stat.update(mean=round(mean, 6),
                        min=round(float(valid.min()), 6),
                        max=round(float(valid.max()), 6))
        else:
            stat.update(mean=None, min=None, max=None)
        out.append(stat)
    return out


def read_testpoints(name: str) -> dict:
    tp = _read_json(TESTS_DIR / name / "testpoints.json")
    if tp is None:
        meta = get_meta(name) or {}
        tp = {"version": 1, "test": name,
              "source_file": meta.get("source_file", ""),
              "fs_hz": meta.get("fs_hz"), "test_points": []}
    return tp


def write_testpoints(name: str, payload: dict):
    write_json_atomic(TESTS_DIR / name / "testpoints.json", payload)
