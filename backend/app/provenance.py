"""Normalization and validation for self-reported upload provenance."""

from __future__ import annotations

import unicodedata


MAX_UPLOADER_NAME_LENGTH = 80


def normalize_uploader_name(value: str) -> str:
    """Return the canonical display value for an uploader attribution.

    Uploader names are labels, not account identities.  NFC preserves the
    user's chosen Unicode spelling while making canonically equivalent input
    stable for resumable-upload identity comparisons.
    """
    if not isinstance(value, str):
        raise TypeError("uploader_name must be a string")
    normalized = unicodedata.normalize("NFC", value)
    if any(unicodedata.category(char) == "Cc" for char in normalized):
        raise ValueError("uploader_name must not contain control characters")
    normalized = normalized.strip()
    if not normalized:
        raise ValueError("uploader_name must not be blank")
    if len(normalized) > MAX_UPLOADER_NAME_LENGTH:
        raise ValueError(
            f"uploader_name must be at most {MAX_UPLOADER_NAME_LENGTH} "
            "characters"
        )
    return normalized
