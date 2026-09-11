"""Auto-split helpers: ID candidates and native-sample value-change proposals."""

import numpy as np
import polars as pl

from .config import TESTS_DIR
from .store import get_meta


MAX_SPLIT_COLUMNS = 9
MAX_SPLIT_POINTS = 1000


def id_candidates(name: str, max_unique: int = 500) -> list[dict]:
    """Columns that look like test-point ID columns:
    integer-valued, low cardinality, at least 2 distinct values."""
    meta = get_meta(name)
    if meta is None:
        raise FileNotFoundError(name)
    tcol = meta["time_column"]
    stride = max(1, meta["n_rows"] // 20000)
    df = (pl.scan_parquet(TESTS_DIR / name / "data.parquet")
          .gather_every(stride).collect())

    out = []
    for c in meta["columns"]:
        if c == tcol:
            continue
        vals = df[c].drop_nulls().to_numpy()
        # Ingest keeps only numeric data columns, but stay defensive: a single
        # column that cannot be rounded/compared must skip, never 500 the whole
        # candidate list.
        try:
            # Acquisition gaps are missing IDs, not evidence against an ID
            # column. Relative tolerance would also classify large fractional
            # measurements as integers, so use only absolute tolerance.
            vals = vals[np.isfinite(vals)]
            if len(vals) == 0:
                continue
            if not np.allclose(vals, np.round(vals), atol=1e-9, rtol=0):
                continue
            n_unique = len(np.unique(vals))
        except (TypeError, ValueError):
            continue
        if 2 <= n_unique <= max_unique:
            out.append({"col": c, "n_unique": int(n_unique)})
    out.sort(key=lambda x: x["n_unique"])
    return out


def autosplit(name: str, col: str, ignore_zero: bool = True,
              min_len_s: float = 1.0) -> list[dict]:
    """Test points from constant-value runs of `col`. Does NOT save —
    returns a proposal for the user to review."""
    meta = get_meta(name)
    if meta is None:
        raise FileNotFoundError(name)
    tcol = meta["time_column"]
    fs = float(meta["fs_hz"])
    if not np.isfinite(min_len_s) or min_len_s < 0:
        raise ValueError("min_len_s must be a finite value >= 0")
    df = (pl.scan_parquet(TESTS_DIR / name / "data.parquet")
          .select(list(dict.fromkeys([tcol, col]))).collect())
    t = df[tcol].to_numpy()
    v = df[col].to_numpy().astype(np.float64)
    if not len(v):
        return []

    # Consecutive NaNs form one missing-ID run; valid runs either side stay
    # separate. Missing and infinite values never produce a proposed point.
    prev, curr = v[:-1], v[1:]
    changed = (curr != prev) & ~(np.isnan(curr) & np.isnan(prev))
    starts = np.concatenate([[0], np.nonzero(changed)[0] + 1])
    ends = np.concatenate([starts[1:], [len(v)]])

    tps = []
    for st, en in zip(starts, ends):
        val = v[st]
        if not np.isfinite(val):
            continue
        if ignore_zero and val == 0:
            continue
        # N samples occupy N/fs seconds in a half-open point. Measuring from
        # the first to last sample rejects runs exactly at the requested
        # minimum, and timestamp subtraction adds rounding errors at late runs.
        if (en - st) / fs < min_len_s:
            continue
        # end_idx is EXCLUSIVE (range is [st, en)); end_s must be the matching
        # exclusive-boundary TIME, i.e. the next run's first sample (or one
        # sample step past the last sample for the final run). Otherwise the
        # frontend's save round-trip round((end_s - t_start)*fs) maps back to
        # en-1 and every saved test point loses its last sample (bug 1.10).
        end_s = float(t[en]) if en < len(t) else float(t[en - 1]) + 1.0 / fs
        tps.append({
            "id": len(tps) + 1,
            "name": f"TP-{len(tps) + 1:02d}",
            "label": f"{col}={val:g}",
            "start_s": round(float(t[st]), 6),
            "end_s": round(end_s, 6),
            "start_idx": int(st),
            "end_idx": int(en),
            "notes": "",
        })
    return tps


def preview_autosplit(name: str, columns: list[str], ignore_zero: bool = True,
                      min_len_s: float = 1.0) -> dict:
    """Preview intervals where ALL selected values stay constant together.

    Read only selected native columns. Missing values break runs; valid tuples
    on either side never join. Exclusion sample counts are disjoint: missing
    first, then zero, then isolated samples (no unchanged adjacent tuple).
    A short run counts only after those exclusions. No writes,
    downsampling, tolerance, value rounding, or silent proposal truncation.
    The caller holds data_read so metadata and samples belong to one read.
    """
    if not 1 <= len(columns) <= MAX_SPLIT_COLUMNS:
        raise ValueError(f"select between 1 and {MAX_SPLIT_COLUMNS} variables")
    if any(not isinstance(col, str) or not col.strip() for col in columns):
        raise ValueError("variable names must be nonempty strings")
    if len(set(columns)) != len(columns):
        raise ValueError("select each variable only once")
    if not np.isfinite(min_len_s) or min_len_s < 0:
        raise ValueError("min_len_s must be a finite value >= 0")
    meta = get_meta(name)
    if meta is None:
        raise FileNotFoundError(name)
    tcol = meta["time_column"]
    unknown = [col for col in columns if col not in meta["columns"]]
    if unknown:
        raise ValueError(f"unknown variables: {unknown}")
    if tcol in columns:
        raise ValueError("choose signal or ID variables; the time column cannot drive auto-split")
    try:
        fs = float(meta["fs_hz"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("test sample rate is unavailable; reload or re-import this test") from None
    if not np.isfinite(fs) or fs <= 0:
        raise ValueError("test sample rate must be finite and greater than zero")

    df = (pl.scan_parquet(TESTS_DIR / name / "data.parquet")
          .select([tcol, *columns]).collect())
    try:
        t = df[tcol].to_numpy().astype(np.float64, copy=False)
        values = [df[col].to_numpy().astype(np.float64, copy=False)
                  for col in columns]
    except (TypeError, ValueError):
        raise ValueError("auto-split requires numeric variables and numeric timestamps") from None
    n_rows = len(t)
    if not np.isfinite(t).all() or np.any(t[1:] < t[:-1]):
        raise ValueError("test timestamps must be finite and ordered; re-import this test")
    proposal = {
        "method": "value_changes", "columns": list(columns),
        "ignore_zero": ignore_zero, "min_len_s": min_len_s,
        "sample_count": n_rows, "fs_hz": fs, "test_points": [],
        "excluded": {"missing_samples": 0, "zero_samples": 0,
                     "isolated_samples": 0, "short_runs": 0},
    }
    if n_rows == 0:
        return proposal

    changed = np.zeros(n_rows - 1, dtype=bool)
    finite = np.ones(n_rows, dtype=bool)
    zero = np.zeros(n_rows, dtype=bool)
    for value in values:
        finite &= np.isfinite(value)
        if ignore_zero:
            zero |= value == 0
        prev, curr = value[:-1], value[1:]
        changed |= (curr != prev) & ~(np.isnan(curr) & np.isnan(prev))
    starts = np.concatenate([[0], np.flatnonzero(changed) + 1])
    ends = np.concatenate([starts[1:], [n_rows]])
    eligible = finite[starts] & ~zero[starts]
    # A single sample cannot establish constancy over time. Without this
    # condition, a ramp becomes one TP per sample whenever min_len_s <= 1/fs,
    # even if another selected variable stays flat for the entire test.
    constant = ends - starts >= 2
    # Duration belongs to the half-open sample interval, independent of
    # timestamp subtraction errors and coarse source-clock quantization.
    long_enough = (ends - starts) / fs >= min_len_s
    kept = np.flatnonzero(eligible & constant & long_enough)
    if len(kept) > MAX_SPLIT_POINTS:
        raise ValueError(
            f"auto-split found more than {MAX_SPLIT_POINTS:,} test points; "
            "increase the minimum duration or choose stable ID/state variables")
    proposal["excluded"] = {
        "missing_samples": int(np.count_nonzero(~finite)),
        "zero_samples": int(np.count_nonzero(finite & zero)),
        "isolated_samples": int(np.count_nonzero(eligible & ~constant)),
        "short_runs": int(np.count_nonzero(eligible & constant & ~long_enough)),
    }
    for run_index in kept:
        st, en = int(starts[run_index]), int(ends[run_index])
        start_s = float(t[st])
        end_s = float(t[en]) if en < n_rows else float(t[-1]) + 1.0 / fs
        if not np.isfinite(end_s) or end_s <= start_s:
            raise ValueError(
                "source-clock resolution cannot represent these test-point boundaries; "
                "increase the minimum duration or re-import with a generated time axis")
        labels = []
        for col, value in zip(columns, values):
            val = float(value[st])
            # repr keeps distinct finite Float64 states distinguishable, while
            # integer IDs remain readable (1 instead of 1.0).
            display = str(int(val)) if val.is_integer() else repr(val)
            labels.append(f"{col}={display}")
        point_id = len(proposal["test_points"]) + 1
        proposal["test_points"].append({
            "id": point_id, "name": f"TP-{point_id:02d}",
            "label": ", ".join(labels), "start_s": start_s, "end_s": end_s,
            "start_idx": st, "end_idx": en, "notes": "",
        })
    return proposal
