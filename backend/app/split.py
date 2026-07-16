"""Auto-split helpers: ID-column candidates + split-by-column-transitions."""

import numpy as np
import polars as pl

from .config import TESTS_DIR
from .store import get_meta


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
        if len(vals) == 0:
            continue
        if not np.allclose(vals, np.round(vals), atol=1e-9):
            continue
        n_unique = len(np.unique(vals))
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
    df = (pl.scan_parquet(TESTS_DIR / name / "data.parquet")
          .select([tcol, col]).collect())
    t = df[tcol].to_numpy()
    v = df[col].to_numpy().astype(np.float64)

    # run boundaries: value changes (NaN != NaN so NaN blocks break runs too)
    prev, curr = v[:-1], v[1:]
    changed = (curr != prev) & ~(np.isnan(curr) & np.isnan(prev))
    starts = np.concatenate([[0], np.nonzero(changed)[0] + 1])
    ends = np.concatenate([starts[1:], [len(v)]])

    tps = []
    for st, en in zip(starts, ends):
        val = v[st]
        if np.isnan(val):
            continue
        if ignore_zero and val == 0:
            continue
        if t[en - 1] - t[st] < min_len_s:
            continue
        tps.append({
            "id": len(tps) + 1,
            "name": f"TP-{len(tps) + 1:02d}",
            "label": f"{col}={val:g}",
            "start_s": round(float(t[st]), 6),
            "end_s": round(float(t[en - 1]), 6),
            "start_idx": int(st),
            "end_idx": int(en),
            "notes": "",
        })
    return tps
