"""Typed component identities. One atomic registry; associations live with tests.

Registry operations never acquire catalog/test locks. Callers may validate under
test_write, so keep that direction (test -> component registry) one-way. There is
no component deletion/renaming yet: UUID references outlive test folder names.
"""
import json
import unicodedata
from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, TypeAdapter

from . import store
from .locks import ReaderWriterLock
from .test_notes import validate_test_text

KINDS = ("propeller", "motor", "esc")
Kind = Literal["propeller", "motor", "esc"]
MAX_PER_KIND = 1000
_lock = ReaderWriterLock()


def normalize_name(value):
    value = validate_test_text(value)
    if not isinstance(value, str):
        return value
    value = unicodedata.normalize("NFC", value).strip()
    if any(c in value for c in "\n\t"):
        raise ValueError("component names must be a single line without tabs")
    return value


Name = Annotated[str, BeforeValidator(normalize_name), Field(min_length=1, max_length=120, strict=True)]


class ComponentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Kind
    name: Name


class Component(ComponentCreate):
    id: UUID
    created_at: str


class ComponentIds(BaseModel):
    model_config = ConfigDict(extra="forbid")
    propeller: UUID | None = None
    motor: UUID | None = None
    esc: UUID | None = None


Column = Annotated[str, Field(min_length=1, strict=True)]
MAX_SETS = 16
COLUMN_BINDINGS = ("rpm_column", "motor_temperature_column", "power_column")


class ComponentSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID | Literal["legacy"]
    name: Name
    components: ComponentIds = Field(default_factory=ComponentIds)
    rpm_column: Column | None = None
    motor_temperature_column: Column | None = None
    motor_temperature_unit: Literal["C", "F", "K"] = "C"
    power_column: Column | None = None
    power_unit: Literal["W", "kW"] = "W"


ComponentSets = Annotated[list[ComponentSet], Field(max_length=MAX_SETS)]
_sets_adapter = TypeAdapter(ComponentSets)


def normalize_sets(value) -> list[dict]:
    """Normalize persisted structure without requiring a live registry."""
    return _sets_adapter.dump_python(_sets_adapter.validate_python(value), mode="json")


def sets(meta: dict) -> list[dict]:
    """Canonical read view; explicit empty sets must never revive legacy IDs."""
    if "component_sets" in meta:
        return normalize_sets(meta["component_sets"])
    return normalize_sets([{"id": "legacy", "name": "Set 1",
        "components": ids(meta.get("components")),
        "rpm_column": meta.get("component_rpm_column")}])


def validate_sets(value, meta: dict | None = None, *, references: bool = True) -> list[dict]:
    proposed = normalize_sets(value)
    if len({item["id"] for item in proposed}) != len(proposed):
        raise HTTPException(422, "Each component set must have a unique ID.")
    if len({item["name"].casefold() for item in proposed}) != len(proposed):
        raise HTTPException(422, "Each component set must have a unique name.")
    assigned = [component_id for item in proposed
                for component_id in item["components"].values() if component_id]
    if len(set(assigned)) != len(assigned):
        raise HTTPException(422, "A component can only belong to one set in a test.")
    for item in proposed:
        if references:
            validate_references(ComponentIds.model_validate(item["components"]))
        for field in COLUMN_BINDINGS:
            column = item[field]
            if column is None:
                continue
            if meta is None:
                raise HTTPException(422, "Select component columns after the upload finishes.")
            if column == meta.get("time_column") or column not in meta.get("columns", []):
                raise HTTPException(422, f"Select an existing non-time column for {field}.")
    return proposed


def sync_projection(meta: dict) -> None:
    """Keep older read clients' first-set projection and guards coherent."""
    current = sets(meta)
    first = current[0] if current else None
    next_ids = first["components"] if first else ids()
    next_rpm = first["rpm_column"] if first else None
    try:
        previous_ids = ids(meta.get("components"))
    except (ValueError, TypeError, AttributeError):
        previous_ids = None  # A valid explicit save can repair a damaged projection.
    if previous_ids != next_ids:
        meta["components_revision"] = meta.get("components_revision", 0) + 1
    if meta.get("component_rpm_column") != next_rpm:
        meta["component_rpm_revision"] = meta.get("component_rpm_revision", 0) + 1
    meta["components"] = next_ids
    meta["component_rpm_column"] = next_rpm


def apply_sets(meta: dict, value, expected_revision: int | None) -> None:
    revision = meta.get("component_sets_revision", 0)
    if expected_revision is None or expected_revision != revision:
        raise HTTPException(409, "Component sets changed. Reload saved metadata before saving your draft again.")
    proposed = validate_sets(value, meta)
    try:
        current = sets(meta)
    except (ValueError, TypeError, AttributeError):
        current = None  # Preserve bad saved data on reads; replace only on an explicit guarded save.
    if "component_sets" not in meta or proposed != current:
        meta["component_sets"] = proposed
        meta["component_sets_revision"] = revision + 1
    sync_projection(meta)


def remap_columns(meta: dict, rename: dict, columns: list[str], time_column: str) -> None:
    """Apply a data edit to every binding, preserving legacy-only documents."""
    if "component_sets" not in meta:
        previous = meta.get("component_rpm_column")
        updated = rename.get(previous, previous)
        if updated not in columns or updated == time_column:
            updated = None
        if updated != previous:
            meta["component_rpm_column"] = updated
            meta["component_rpm_revision"] = meta.get("component_rpm_revision", 0) + 1
            meta["component_sets_revision"] = meta.get("component_sets_revision", 0) + 1
        return
    current = sets(meta)
    proposed = normalize_sets(current)
    for item in proposed:
        for field in COLUMN_BINDINGS:
            column = rename.get(item[field], item[field])
            item[field] = column if column in columns and column != time_column else None
    if proposed != current:
        meta["component_sets"] = proposed
        meta["component_sets_revision"] = meta.get("component_sets_revision", 0) + 1
    sync_projection(meta)


def ids(value=None) -> dict:
    return ComponentIds.model_validate({} if value is None else value).model_dump(mode="json")


def _path():
    # Dynamic TESTS_DIR binding also keeps DataDirTestCase fully isolated.
    return store.TESTS_DIR.parent / "components.json"


def _load() -> dict:
    try:
        path = _path()
        if path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError("registry too large")
        saved = json.loads(path.read_text(encoding="utf-8"))
        if saved["version"] != 1 or not isinstance(saved["components"], list) or len(saved["components"]) > 3 * MAX_PER_KIND:
            raise ValueError("invalid format")
        items = [Component.model_validate(item).model_dump(mode="json") for item in saved["components"]]
        if len({item["id"] for item in items}) != len(items):
            raise ValueError("duplicate UUID")
        if len({(item["kind"], item["name"].casefold()) for item in items}) != len(items):
            raise ValueError("duplicate name")
        if any(sum(item["kind"] == kind for item in items) > MAX_PER_KIND for kind in KINDS):
            raise ValueError("too many components")
        return {"version": 1, "components": sorted(items, key=lambda c: (c["kind"], c["name"].casefold()))}
    except FileNotFoundError:
        return {"version": 1, "components": []}
    except (ValueError, TypeError, KeyError, AttributeError, HTTPException):
        raise HTTPException(409, "The component registry is invalid. Restore a valid components.json before editing components.") from None


def list_components() -> dict:
    with _lock.read():
        return _load()


def create_component(payload: ComponentCreate) -> dict:
    with _lock.write():
        document = _load()
        # Safe replay after a lost response and concurrent same-name creation.
        prior = next((c for c in document["components"] if c["kind"] == payload.kind
                      and c["name"].casefold() == payload.name.casefold()), None)
        if prior:
            return prior
        if sum(c["kind"] == payload.kind for c in document["components"]) >= MAX_PER_KIND:
            raise HTTPException(409, f"Limit: {MAX_PER_KIND} components of each type.")
        item = {**payload.model_dump(), "id": str(uuid4()),
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        document["components"].append(item)
        store.write_json_atomic(_path(), document)
        return item


def validate_references(value: ComponentIds) -> dict:
    references = value.model_dump(mode="json")
    if not any(references.values()):
        return references
    by_id = {c["id"]: c for c in list_components()["components"]}
    for kind, component_id in references.items():
        if component_id is not None and (component_id not in by_id or by_id[component_id]["kind"] != kind):
            raise HTTPException(422, f"Select an existing {kind} component or leave it unassigned.")
    return references


def apply_assignments(meta: dict, value: ComponentIds, expected_revision: int | None) -> None:
    current = ids(meta.get("components"))
    revision = meta.get("components_revision", 0)
    if expected_revision is None or expected_revision != revision:
        raise HTTPException(409, "Component associations changed. Reload saved metadata before saving your draft again.")
    proposed = validate_references(value)
    if proposed != current:
        meta["components"] = proposed
        # Updated in the SAME atomic meta write. Component totals read current
        # assignments every request; only numerical source summaries are cached.
        meta["components_revision"] = revision + 1
        # Canonical drafts can be opened before a legacy client writes. Their
        # guard must advance too, even before the first component-set migration.
        meta["component_sets_revision"] = meta.get("component_sets_revision", 0) + 1
