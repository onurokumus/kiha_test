"""Read side: test registry, windowed range reads, pyramid level selection."""

import json
import math
import os
import tempfile
import time
from pathlib import Path

import numpy as np
import polars as pl
import pyarrow.parquet as pq

from .config import MAX_POINTS_RAW, POINT_BUDGET_CAP, PYRAMID_LEVELS, TESTS_DIR


def list_tests() -> list[dict]:
    out = []
    if not TESTS_DIR.exists():
        return out
    for d in sorted(TESTS_DIR.iterdir()):
        if not d.is_dir():
            continue
        status = _read_json(d / "status.json") or {"status": "unknown"}
        meta = _read_json(d / "meta.json") or {}
        out.append({"name": d.name, "status": status.get("status"),
                    "error": status.get("error"),
                    "n_rows": meta.get("n_rows"), "fs_hz": meta.get("fs_hz"),
                    "duration_s": meta.get("duration_s"),
                    "n_columns": meta.get("n_columns")})
    return out


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


def _nan_to_none(arr: np.ndarray) -> list:
    return [None if math.isnan(v) else round(float(v), 6) for v in arr]


def read_window(name: str, cols: list[str], t0: float | None,
                t1: float | None, px: int) -> dict:
    """Serve a plot window: raw when zoomed in, min/max envelope otherwise."""
    meta = get_meta(name)
    if meta is None:
        raise FileNotFoundError(name)
    fs = meta["fs_hz"]
    n_rows = meta["n_rows"]
    tcol = meta["time_column"]
    t_start = meta.get("t_start") or 0.0
    duration = n_rows / fs

    lo = t_start if t0 is None else max(t0, t_start)
    hi = t_start + duration if t1 is None else min(t1, t_start + duration)
    i0 = max(0, int((lo - t_start) * fs))
    i1 = min(n_rows, int(math.ceil((hi - t_start) * fs)) + 1)
    n_raw = max(0, i1 - i0)

    budget = min(max(2 * px, 1000), POINT_BUDGET_CAP)
    test_dir = TESTS_DIR / name

    if n_raw <= max(MAX_POINTS_RAW, budget):
        df = (pl.scan_parquet(test_dir / "data.parquet")
              .slice(i0, n_raw).select([tcol] + cols).collect())
        return {
            "mode": "raw", "level": 1, "n_raw": n_raw, "i0": i0, "i1": i1,
            "t": _nan_to_none(df[tcol].to_numpy()),
            "series": {c: _nan_to_none(df[c].to_numpy().astype(np.float64))
                       for c in cols},
        }

    # pick the FINEST level whose point count fits the cap (best envelope detail)
    level = PYRAMID_LEVELS[-1]
    for f in PYRAMID_LEVELS:
        if n_raw / f <= POINT_BUDGET_CAP:
            level = f
            break
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

    # if even the coarsest level exceeds the cap, MERGE adjacent buckets
    # (min of mins / max of maxes) — striding would drop spikes
    merge = max(1, math.ceil(len(t) / POINT_BUDGET_CAP))
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

    return {
        "mode": "envelope", "level": level * merge, "n_raw": n_raw,
        "i0": i0, "i1": i1,
        "t": _nan_to_none(t),
        "series": {c: {"min": _nan_to_none(mn), "max": _nan_to_none(mx)}
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
    parquet = pq.ParquetFile(path)
    row_start = 0
    for row_group in range(parquet.num_row_groups):
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


def _json_numbers(values: np.ndarray) -> list[float | None]:
    return [round(float(value), 6) if math.isfinite(value) else None
            for value in values]


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
            col: {"t": _json_numbers(relative_t),
                  "y": _json_numbers(np.concatenate(value_parts[col]))}
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

            interior_start = max(rel_start, 1)
            interior_end = min(rel_end, n_raw - 1)
            if interior_end <= interior_start:
                continue
            first_bucket = max(
                0, int(np.searchsorted(edges, interior_start,
                                       side="right") - 1))
            last_bucket = min(
                bucket_count - 1,
                int(np.searchsorted(edges, interior_end - 1,
                                    side="right") - 1))
            for bucket in range(first_bucket, last_bucket + 1):
                seg0 = max(interior_start, int(edges[bucket]))
                seg1 = min(interior_end, int(edges[bucket + 1]))
                if seg1 <= seg0:
                    continue
                local0, local1 = seg0 - rel_start, seg1 - rel_start
                if not math.isfinite(bucket_t[bucket]):
                    bucket_t[bucket] = float(t[local0])
                for col in cols:
                    segment = values[col][local0:local1]
                    finite_offsets = np.flatnonzero(np.isfinite(segment))
                    if not len(finite_offsets):
                        continue
                    finite_values = segment[finite_offsets]
                    lo_offset = int(finite_offsets[np.argmin(finite_values)])
                    hi_offset = int(finite_offsets[np.argmax(finite_values)])
                    lo_value = float(segment[lo_offset])
                    hi_value = float(segment[hi_offset])
                    lo_index = seg0 + lo_offset
                    hi_index = seg0 + hi_offset
                    if (min_index[col][bucket] < 0
                            or lo_value < min_value[col][bucket]):
                        min_value[col][bucket] = lo_value
                        min_index[col][bucket] = lo_index
                        min_t[col][bucket] = float(t[local0 + lo_offset])
                    if (max_index[col][bucket] < 0
                            or hi_value > max_value[col][bucket]):
                        max_value[col][bucket] = hi_value
                        max_index[col][bucket] = hi_index
                        max_t[col][bucket] = float(t[local0 + hi_offset])

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
            series[col] = {"t": _json_numbers(relative_t),
                           "y": _json_numbers(
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
        "series": series,
    }


def read_xy(name: str, x_col: str, y_cols: list[str], t0: float | None,
            t1: float | None, max_pts: int = 3000) -> dict:
    """Variable-vs-variable data over a time range, stride-decimated.
    NaN pairs dropped per series."""
    meta = get_meta(name)
    if meta is None:
        raise FileNotFoundError(name)
    fs = meta["fs_hz"]
    n_rows = meta["n_rows"]
    t_start = meta.get("t_start") or 0.0
    duration = n_rows / fs

    lo = t_start if t0 is None else max(t0, t_start)
    hi = t_start + duration if t1 is None else min(t1, t_start + duration)
    i0 = max(0, int((lo - t_start) * fs))
    i1 = min(n_rows, int(math.ceil((hi - t_start) * fs)) + 1)
    n_raw = max(0, i1 - i0)
    stride = max(1, math.ceil(n_raw / max_pts))

    # dedupe: y may include x itself (e.g. an XY grid cell whose column
    # equals the shared x axis) and polars rejects duplicate selects
    df = (pl.scan_parquet(TESTS_DIR / name / "data.parquet")
          .slice(i0, n_raw).gather_every(stride)
          .select(list(dict.fromkeys([x_col] + y_cols))).collect())
    xv = df[x_col].to_numpy().astype(np.float64)

    series = {}
    for y in y_cols:
        yv = df[y].to_numpy().astype(np.float64)
        m = np.isfinite(xv) & np.isfinite(yv)
        series[y] = {
            "x": [round(float(v), 6) for v in xv[m]],
            "y": [round(float(v), 6) for v in yv[m]],
        }
    return {"stride": stride, "n_raw": n_raw, "series": series}


def tp_stats(name: str, col: str) -> list[dict]:
    """Per-test-point mean/min/max of one column (NaN-aware)."""
    meta = get_meta(name)
    if meta is None:
        raise FileNotFoundError(name)
    fs = meta["fs_hz"]
    n_rows = meta["n_rows"]
    t_start = meta.get("t_start") or 0.0
    tps = sorted(read_testpoints(name)["test_points"],
                 key=lambda tp: tp["start_s"])

    v = (pl.scan_parquet(TESTS_DIR / name / "data.parquet")
         .select([col]).collect())[col].to_numpy().astype(np.float64)

    out = []
    for i, tp in enumerate(tps):
        si = tp.get("start_idx")
        if si is None:
            si = int(round((tp["start_s"] - t_start) * fs))
        ei = tp.get("end_idx")
        if ei is None:
            if tp.get("end_s") is not None:
                ei = int(round((tp["end_s"] - t_start) * fs))
            elif i + 1 < len(tps):
                ei = int(round((tps[i + 1]["start_s"] - t_start) * fs))
            else:
                ei = n_rows
        sl = v[max(0, si):min(ei, n_rows)]
        valid = sl[np.isfinite(sl)]
        stat = {"id": tp["id"], "name": tp["name"], "label": tp["label"],
                "n": int(len(sl)), "n_valid": int(len(valid))}
        if len(valid):
            stat.update(mean=round(float(valid.mean()), 6),
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
