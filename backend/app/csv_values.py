"""Lossless generated values shared by CSV export paths."""

from decimal import Decimal

import pyarrow as pa


def test_point_id_scalar(value: int | None) -> pa.Scalar:
    """Write integer IDs without CSV quotes or floating-point conversion."""
    if value is None or -(2**63) <= value < 2**63:
        return pa.scalar(value, type=pa.int64())
    digits = len(str(abs(value)))
    if digits <= 76:
        return pa.scalar(Decimal(value), type=pa.decimal256(digits, 0))
    # Preserve extreme legacy IDs beyond Arrow's numeric capacity as exact
    # text (quoted by Arrow), rather than truncating or rejecting the export.
    return pa.scalar(str(value))
