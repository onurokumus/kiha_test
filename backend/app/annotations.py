"""Test-level events in stored elapsed seconds. Trims never move/delete notes.

The caller holds the per-test lock. Annotation-only reads need no native slot.
The file lives with the data, so rename/trash/restore keep its identity intact.
"""

import json
import math
from typing import Annotated
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from . import store
from .test_notes import validate_test_text

MAX_ANNOTATIONS = 200
MAX_TEXT = 2000
MAX_FILE_BYTES = 8 * 1024 * 1024
def finite_seconds(value):
    # FastAPI cannot JSON-encode NaN/Infinity in its default validation echo.
    if isinstance(value, float) and not math.isfinite(value):
        raise HTTPException(422, "Annotation times must be finite numbers.")
    return value


Seconds = Annotated[float, BeforeValidator(finite_seconds), Field(ge=0, le=1e12, allow_inf_nan=False, strict=True)]
Text = Annotated[str, BeforeValidator(validate_test_text), Field(max_length=MAX_TEXT, strict=True)]


class Annotation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    start_s: Seconds
    end_s: Seconds | None = None
    text: Text

    @model_validator(mode="after")
    def check(self):
        if self.end_s is not None and self.end_s <= self.start_s:
            raise ValueError("interval end must be greater than start")
        if not self.text.strip():
            raise ValueError("annotation text must not be blank")
        return self


class AnnotationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0, strict=True)
    expected_data_bounds: tuple[Seconds, Seconds]
    annotations: list[Annotation] = Field(max_length=MAX_ANNOTATIONS)

    @model_validator(mode="after")
    def distinct_ids(self):
        if len({item.id for item in self.annotations}) != len(self.annotations):
            raise ValueError("annotation IDs must be unique")
        return self


def read_annotations(name: str) -> dict:
    directory = store.TESTS_DIR / name
    if directory.resolve().parent != store.TESTS_DIR.resolve():
        raise HTTPException(404, "test not found")
    meta = store.get_meta(name)
    if not meta:
        raise HTTPException(404, "test not found or not ready")
    try:
        start = float(meta.get("t_start", 0))
        end = start + float(meta.get("duration_s", 0))
    except (TypeError, ValueError):
        raise HTTPException(409, "test has no usable time bounds")
    if not all(math.isfinite(v) for v in (start, end)) or start < 0 or end <= start:
        raise HTTPException(409, "test has no usable time bounds")
    path = directory / "annotations.json"
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("annotation file too large")
        saved = json.loads(path.read_text(encoding="utf-8"))
        if saved.get("version") != 1 or saved.get("time_basis") != "stored_elapsed_seconds":
            raise ValueError("unsupported annotation format")
        parsed = AnnotationPatch.model_validate({
            "expected_revision": saved["revision"], "expected_data_bounds": [start, end],
            "annotations": saved["annotations"]})
        revision = parsed.expected_revision
        items = [item.model_dump(mode="json") for item in parsed.annotations]
    except FileNotFoundError:
        revision, items = 0, []
    except (ValueError, TypeError, KeyError, AttributeError, HTTPException):
        raise HTTPException(409, "Stored annotations are invalid; they were not changed. Restore a valid annotation file before editing.")
    return {"version": 1, "test": name, "time_basis": "stored_elapsed_seconds",
            "revision": revision, "data_bounds": [start, end], "annotations": items}


def replace_annotations(name: str, payload: AnnotationPatch) -> dict:
    current = read_annotations(name)
    if (payload.expected_revision != current["revision"] or
            list(payload.expected_data_bounds) != current["data_bounds"]):
        raise HTTPException(409, "Annotations or test bounds changed. Reload saved notes before saving your draft again.")
    old = {item["id"]: item for item in current["annotations"]}
    start, end = current["data_bounds"]
    items = [item.model_dump(mode="json") for item in payload.annotations]
    for item in items:
        previous = old.get(item["id"])
        unchanged_time = previous and all(previous[key] == item[key] for key in ("start_s", "end_s"))
        if not unchanged_time and not (start <= item["start_s"] <= (item["end_s"] or item["start_s"]) <= end):
            raise HTTPException(422, "New or moved annotations must be within the current test time bounds.")
    # Idempotent replay after a lost response requires a fresh GET; unchanged
    # lists do not advance revision or rewrite storage.
    if items == current["annotations"]:
        return current
    saved = {"version": 1, "time_basis": "stored_elapsed_seconds",
             "revision": current["revision"] + 1, "annotations": items}
    store.write_json_atomic(store.TESTS_DIR / name / "annotations.json", saved)
    return {**current, **saved}
