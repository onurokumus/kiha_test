"""Bounded import diagnostics and a metadata-only upload-list summary.

Source findings are immutable, including after trims/fills. Current signal
counts and gap ranges come from the existing rebuild metadata. Never scan raw
CSV/Parquet during catalog polling or invent source checks for older imports.
"""

import numpy as np

EXAMPLE_LIMIT = 8


def inspect_source_time(values: np.ndarray, column: str) -> dict:
    """Count adjacent finite equal/backward steps before time normalization.

    Rows are one-based parsed data records (header excluded), NOT file lines
    or indices in the gap-expanded/edited Parquet. Counts cover the entire
    column; only examples are capped. Invalid neighbors are not bridged.
    """
    finite = np.isfinite(values)
    pairs = finite[:-1] & finite[1:]

    def steps(mask):
        indices = np.flatnonzero(mask)
        return {
            "count": int(indices.size),
            "examples": [
                {"row": int(i + 2), "previous_s": float(values[i]),
                 "time_s": float(values[i + 1])}
                for i in indices[:EXAMPLE_LIMIT]
            ],
        }

    invalid = np.flatnonzero(~finite)
    return {
        "version": 1,
        "checked": True,
        "column": column,
        "n_rows": len(values),
        "duplicate_steps": steps(pairs & (values[1:] == values[:-1])),
        "backward_steps": steps(pairs & (values[1:] < values[:-1])),
        "invalid_timestamps": {
            "count": int(invalid.size),
            "example_rows": (invalid[:EXAMPLE_LIMIT] + 1).tolist(),
        },
        # Filled from _measured_timing only if that existing validation passes.
        "gap_count": None,
        "gap_examples": [],
    }


def quality_summary(meta: dict) -> dict | None:
    """Small catalog payload; full details use the existing metadata endpoint."""
    if not meta:
        return None
    source = meta.get("source_time_quality") or {}
    source_known = source.get("version") == 1 and source.get("checked") is True
    warnings = []
    for field in ("duplicate_steps", "backward_steps", "invalid_timestamps"):
        if source_known and (source.get(field) or {}).get("count", 0) > 0:
            warnings.append(field)
    if (meta.get("time_gap_count") or 0) > 0 or (source.get("gap_count") or 0) > 0:
        warnings.append("time_gaps")
    if any((meta.get("nan_counts") or {}).values()):
        warnings.append("missing_values")
    if any((meta.get("inf_counts") or {}).values()):
        warnings.append("infinite_values")
    if meta.get("time_source") == "generated":
        warnings.append("generated_time")
    if meta.get("jitter_warning"):
        warnings.append("timing_jitter")
    if meta.get("skipped_columns"):
        warnings.append("skipped_columns")
    partial = (
        not source_known or source.get("gap_count") is None
        or meta.get("time_gap_count") is None
        or not isinstance(meta.get("nan_counts"), dict)
        or not isinstance(meta.get("inf_counts"), dict)
    )
    return {"warnings": warnings, "partial": partial}
