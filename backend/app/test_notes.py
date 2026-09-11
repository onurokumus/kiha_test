"""Bounded plain text for test descriptions and findings (not plot annotations)."""

import re
from typing import Annotated

from fastapi import HTTPException
from pydantic import BeforeValidator, Field

MAX_DESCRIPTION_LENGTH = 1000
MAX_NOTES_LENGTH = 20_000
_INVALID_TEXT = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\ud800-\udfff]")


def normalize_test_text(value):
    if not isinstance(value, str):
        return value  # Let Pydantic reject non-string JSON without coercion.
    if _INVALID_TEXT.search(value):
        raise ValueError("test text must not contain control characters or invalid Unicode")
    return value.replace("\r\n", "\n").replace("\r", "\n")


def validate_test_text(value):
    # FastAPI's default validation response echoes the bad input. An escaped
    # lone surrogate is valid JSON syntax but cannot be UTF-8 encoded in that
    # response. Reject it with static detail before normal field validation.
    if isinstance(value, str) and re.search(r"[\ud800-\udfff]", value):
        raise HTTPException(422, "test text contains invalid Unicode")
    return normalize_test_text(value)


Description = Annotated[str, BeforeValidator(validate_test_text),
                        Field(max_length=MAX_DESCRIPTION_LENGTH, strict=True)]
Notes = Annotated[str, BeforeValidator(validate_test_text),
                  Field(max_length=MAX_NOTES_LENGTH, strict=True)]
