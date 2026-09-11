"""FastAPI app: upload/ingest, windowed data serving, test points CRUD.

Run:  backend\\.venv\\Scripts\\python.exe backend\\run.py   (port 8000)

Use run.py, NOT `uvicorn app.main:app` directly: run.py installs the
SelectorEventLoop policy that avoids the Windows/py3.14 polars crash and wires
logging.basicConfig so kiha.* log lines are emitted. Launching uvicorn straight
skips both (see CLAUDE.md).
"""

import asyncio
import json
import logging
import re
import time
import uuid
from collections import Counter
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import (BackgroundTasks, FastAPI, HTTPException, Query, Request,
                     UploadFile)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from . import dsp, edit, export_progress, formula, image_export, plot_export, recipes, spectrum_export, split, store, uploads, waterfall_export, xy_export
from .config import (CORS_ORIGINS, DATA_DIR, POINT_BUDGET_CAP, TESTS_DIR,
                     TRASH_DIR, TRASH_MAX_AGE_S)
from .locks import (catalog_read, catalog_write, data_read, drop_test_lock,
                    test_read, test_write, tests_write, with_test_read)
from .status import BUSY_STATUSES, INGEST_LIKE, write_status
from .test_notes import Description, Notes
from . import annotations
from . import components
from . import component_stats
from .components import ComponentIds
from . import trash
from . import analysis_sources


logger = logging.getLogger("kiha.api")


def _reject_if_busy(name: str) -> None:
    status = store.get_status(name).get("status")
    if status in BUSY_STATUSES:
        raise HTTPException(
            409, f"'{name}' is busy ({status}); retry once it is ready")


def _reject_duplicate_ids(payload: "TestPointsFile") -> None:
    """Reject a test-point list with repeated ids before it is persisted.

    Nothing else enforces uniqueness (bug 4.4): read_testpoint_trace silently
    picks the first match, exports resolve one id, and the frontend selection
    key `${test}:${tpId}` would collide — so two points sharing an id are a
    latent data corruption, not a valid file."""
    counts = Counter(tp.id for tp in payload.test_points)
    dupes = sorted(i for i, n in counts.items() if n > 1)
    if dupes:
        raise HTTPException(400, f"duplicate test-point ids: {dupes}")


def _recover_interrupted_ingests() -> list[tuple[str, str]]:
    """Repair resumable uploads and make interrupted rebuilds manageable."""
    jobs = uploads.recover_uploads()
    if not TESTS_DIR.exists():
        return jobs
    with catalog_write():
        for test_dir in TESTS_DIR.iterdir():
            if not test_dir.is_dir():
                continue
            status = store.get_status(test_dir.name).get("status")
            if status == "rebuilding":
                write_status(
                    test_dir,
                    "error",
                    "an edit/rebuild was interrupted by a backend restart "
                    "and the on-disk data may be inconsistent; delete this "
                    "test and upload it again",
                )
    return jobs


@asynccontextmanager
async def lifespan(_app: FastAPI):
    recovery_tasks: set[asyncio.Task] = set()
    for name, upload_id in _recover_interrupted_ingests():
        task = asyncio.create_task(run_in_threadpool(
            uploads.ingest_completed_upload, name, upload_id))
        recovery_tasks.add(task)
        task.add_done_callback(recovery_tasks.discard)
    try:
        yield
    finally:
        for task in recovery_tasks:
            task.cancel()


app = FastAPI(title="kiha time-series plotter", lifespan=lifespan)

app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(uploads.UploadBodyLimitMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "X-Export-Rows", "X-Export-Sources",
                    "X-Export-Plots"],
)

app.include_router(uploads.router)
app.include_router(plot_export.router)
app.include_router(spectrum_export.router)
app.include_router(waterfall_export.router)
app.include_router(xy_export.router)
app.include_router(image_export.router)
app.include_router(export_progress.router)


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Attach a short correlation id and prevent stale API proxy caches."""
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    """Log internals server-side without dumping them into the UI."""
    request_id = getattr(request.state, "request_id", uuid.uuid4().hex)
    logger.exception("Unhandled API error [%s] %s %s",
                     request_id, request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "The server could not finish this request. Please retry.",
            "request_id": request_id,
        },
        headers={"X-Request-ID": request_id, "Cache-Control": "no-store"},
    )


@app.get("/api/health")
def api_health():
    return {"ok": True}


# ---------- models ----------

class TestPoint(BaseModel):
    id: int
    name: str
    label: str = ""
    start_s: float
    end_s: float | None = None
    start_idx: int | None = None
    end_idx: int | None = None
    notes: str = ""


class TestPointsFile(BaseModel):
    version: int = 1
    test: str
    source_file: str = ""
    fs_hz: float | None = None
    test_points: list[TestPoint] = Field(default_factory=list)


class AppSettingsDefaults(BaseModel):
    """Validated shape of the browser settings that may be shared globally."""

    model_config = ConfigDict(extra="forbid")

    scatterX: str = Field(default="", max_length=512)
    scatterY: str = Field(default="", max_length=512)
    datasheetZone: str = Field(default="", max_length=512)
    datasheetVisible: bool = True
    gridColumns: list[str] = Field(
        default_factory=lambda: [""] * 9, min_length=9, max_length=9)
    xyYCols: list[str] = Field(
        default_factory=lambda: [""] * 9, min_length=9, max_length=9)
    xyXCols: list[str] = Field(
        default_factory=lambda: [""] * 9, min_length=9, max_length=9)
    defaultViewMode: Literal["tp", "full", "spectrum", "xy"] = "tp"
    specMode: Literal["fft", "welch", "waterfall"] = "fft"
    specLogY: bool = False
    clustering: bool = True
    uploadFsHz: str = Field(default="", max_length=64)


class AppSettingsDefaultsResponse(BaseModel):
    settings: AppSettingsDefaults | None


def _app_settings_defaults_path() -> Path:
    """Keep the shared UI defaults beside (not inside) the test catalog."""
    return DATA_DIR / "default-settings.json"


@app.get("/api/settings/defaults", response_model=AppSettingsDefaultsResponse)
def api_get_settings_defaults():
    path = _app_settings_defaults_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"settings": None}
    except json.JSONDecodeError:
        logger.warning("ignoring invalid shared settings file: %s", path)
        return {"settings": None}

    try:
        settings = AppSettingsDefaults.model_validate(raw)
    except ValueError:
        logger.warning("ignoring invalid shared settings document: %s", path)
        return {"settings": None}
    return {"settings": settings}


@app.put("/api/settings/defaults", response_model=AppSettingsDefaultsResponse)
def api_put_settings_defaults(payload: AppSettingsDefaults):
    settings = payload.model_dump()
    store.write_json_atomic(_app_settings_defaults_path(), settings)
    return {"settings": settings}


# ---------- tests ----------

@app.get("/api/analysis-sources")
def api_analysis_sources():
    return analysis_sources.catalog()

@app.get("/api/tests")
def api_list_tests():
    with catalog_read():
        return store.list_tests()


@app.get("/api/tests/{name}")
@with_test_read
def api_get_meta(name: str, expected_source_id: uuid.UUID | None = None):
    analysis_sources.verify_reference(name, expected_source_id)
    meta = store.get_meta(name)
    if meta is None:
        raise HTTPException(404, f"test '{name}' not found or not ready")
    return meta


@app.get("/api/tests/{name}/status")
def api_get_status(name: str):
    return store.get_status(name)


def _purge_trash():
    if TRASH_MAX_AGE_S is None:
        return
    now = time.time()
    for entry in trash.list_entries(TRASH_DIR):
        try:
            if entry.get('deleted_at') and now - datetime.fromisoformat(entry['deleted_at']).timestamp() > TRASH_MAX_AGE_S:
                trash.delete_permanently(TRASH_DIR, entry['id'])
        except (OSError, HTTPException, ValueError):
            logger.warning("Could not expire trash entry %s", entry['id'])


@app.get("/api/trash")
def api_list_trash():
    with catalog_write():
        return {"entries": trash.list_entries(TRASH_DIR), "retention_seconds": TRASH_MAX_AGE_S}


class TrashRestore(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, max_length=200)


@app.post("/api/trash/{entry_id}/restore")
def api_restore_trash(entry_id: uuid.UUID, payload: TrashRestore):
    with catalog_write():
        _, entry = trash.get_entry(TRASH_DIR, str(entry_id))
        name = payload.name if payload.name is not None else entry['name']
        trash.validate_name(name)
        with test_write(name):
            return trash.restore(TRASH_DIR, TESTS_DIR, str(entry_id), name)


class TrashDeleteBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[uuid.UUID] = Field(min_length=1, max_length=10000)


@app.delete("/api/trash")
def api_delete_trash_batch(payload: TrashDeleteBatch):
    with catalog_write():
        return trash.delete_batch(TRASH_DIR, [str(value) for value in payload.ids])


@app.delete("/api/trash/{entry_id}")
def api_delete_trash(entry_id: uuid.UUID):
    with catalog_write():
        result = trash.delete_batch(TRASH_DIR, [str(entry_id)])
        if result['failures']:
            raise HTTPException(409, result['failures'][0]['error'])
        return result


@app.delete("/api/tests/{name}")
def api_delete_test(name: str):
    """Soft delete: move to trash so the client can offer undo."""
    # Do not wait for a potentially long upload/ingestion only to delete
    # its result (the status pre-check also avoids blocking for minutes on
    # the per-test lock the receiving/ingesting job holds).
    status = store.get_status(name).get("status")
    if status in BUSY_STATUSES:
        raise HTTPException(409, f"'{name}' is still {status}")

    # Keep the catalog locked until the removed name's lock is forgotten. An
    # upload init must not recreate that name in the gap and then lose its new
    # lock when this delete drops the old registry entry.
    with catalog_write():
        with test_write(name):
            tests_root = TESTS_DIR.resolve()
            test_dir = TESTS_DIR / name
            if test_dir.is_symlink() or test_dir.is_junction() or test_dir.resolve().parent != tests_root or not test_dir.is_dir():
                raise HTTPException(404, f"test '{name}' not found")
            status = store.get_status(name).get("status")
            if status in BUSY_STATUSES:
                raise HTTPException(409, f"'{name}' is still {status}")
            _purge_trash()
            try:
                entry = trash.move_to_trash(TRASH_DIR, test_dir, name)
            except OSError:
                logger.warning("Could not move test %s to trash", name, exc_info=True)
                raise HTTPException(
                    409, f"Could not move '{name}' to trash. Check disk access and close any external file viewers, then retry.") from None
        # The name is gone from tests/; restore/init creates a fresh lock only
        # after this catalog critical section exits.
        drop_test_lock(name)
    store._size_cache.pop(name, None)
    return {"ok": True, "deleted": name, "restorable": True, "trash_id": entry['id']}


@app.post("/api/tests/{name}/restore")
def api_restore_test(name: str):
    """Legacy name route: only an unambiguous trash copy may be restored."""
    with catalog_write():
        matches = [entry for entry in trash.list_entries(TRASH_DIR) if entry['name'] == name]
        if not matches:
            raise HTTPException(404, f"no restorable copy of '{name}'")
        if len(matches) != 1:
            raise HTTPException(409, "Several deleted tests have this name. Restore the intended entry by its trash ID.")
        trash.validate_name(name)
        with test_write(name):
            return trash.restore(TRASH_DIR, TESTS_DIR, matches[0]['id'], name)


TEST_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


@app.post("/api/tests/{name}/rename")
def api_rename_test(name: str, new_name: str = Query(...)):
    if (not TEST_NAME_RE.fullmatch(new_name)
            or not re.search(r"[A-Za-z0-9]", new_name)):
        raise HTTPException(
            400, "test name may only contain letters, digits, '.', '_', '-'")
    if new_name == name:
        with catalog_read(), test_read(name):
            tests_root = TESTS_DIR.resolve()
            src = (TESTS_DIR / name).resolve()
            if src.parent != tests_root or not src.is_dir():
                raise HTTPException(404, f"test '{name}' not found")
            return {"ok": True, "name": name}
    status = store.get_status(name).get("status")
    if status in INGEST_LIKE:
        raise HTTPException(409, f"'{name}' is still {status}")

    with catalog_write():
        with tests_write(name, new_name):
            tests_root = TESTS_DIR.resolve()
            src = (TESTS_DIR / name).resolve()
            if src.parent != tests_root or not src.is_dir():
                raise HTTPException(404, f"test '{name}' not found")
            status = store.get_status(name).get("status")
            if status in INGEST_LIKE:
                raise HTTPException(409, f"'{name}' is still {status}")
            dst = TESTS_DIR / new_name
            if dst.exists():
                raise HTTPException(409, f"test '{new_name}' already exists")

            # Rewrite metadata before moving the directory. Completed
            # resumable uploads retain an audit manifest, and restart recovery
            # validates its name against the containing test directory.
            documents: list[tuple[Path, dict, dict]] = []
            for relative, key in (
                (Path("meta.json"), "name"),
                (Path("testpoints.json"), "test"),
                (Path(".upload") / "manifest.json", "name"),
            ):
                p = src / relative
                try:
                    original = json.loads(p.read_text(encoding="utf-8"))
                except (FileNotFoundError, json.JSONDecodeError):
                    continue
                updated = dict(original)
                updated[key] = new_name
                documents.append((p, original, updated))

            try:
                for path, _, updated in documents:
                    store.write_json_atomic(path, updated)
                src.rename(dst)
            except OSError as e:
                for path, original, _ in documents:
                    try:
                        store.write_json_atomic(path, original)
                    except OSError:
                        pass
                raise HTTPException(
                    409, f"could not rename '{name}' (files in use?): {e}")
        # Keep catalog_write held through the registry update so a concurrent
        # init cannot recreate the old name and have its fresh lock removed.
        drop_test_lock(name)
    return {"ok": True, "name": new_name}


# ---------- editing ----------

@app.get("/api/tests/{name}/annotations")
def api_get_annotations(name: str):
    _reject_if_busy(name)
    with test_read(name):
        _reject_if_busy(name)
        return annotations.read_annotations(name)


@app.put("/api/tests/{name}/annotations")
def api_put_annotations(name: str, payload: annotations.AnnotationPatch):
    _reject_if_busy(name)
    with test_write(name):
        _reject_if_busy(name)
        return annotations.replace_annotations(name, payload)

@app.get("/api/components")
def api_components():
    return components.list_components()


@app.post("/api/components")
def api_create_component(payload: components.ComponentCreate):
    return components.create_component(payload)


@app.get("/api/component-statistics")
def api_component_statistics():
    return component_stats.statistics()


class UserMetaPatch(BaseModel):
    user_meta: dict[str, str] = Field(default_factory=dict)
    description: Description = ""
    notes: Notes = ""
    components: ComponentIds = Field(default_factory=ComponentIds)
    expected_components_revision: int | None = Field(default=None, ge=0, strict=True)
    component_rpm_column: str | None = Field(default=None, min_length=1, strict=True)
    expected_component_rpm_revision: int | None = Field(default=None, ge=0, strict=True)


class FormulaSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    expression: str = Field(
        min_length=1, max_length=formula.MAX_EXPRESSION_LENGTH)
    replace: bool = False


class FormulaPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    formulas: list[FormulaSpec] = Field(
        min_length=1, max_length=formula.MAX_FORMULAS)
    sample_size: int = Field(default=64, ge=1, le=256)


class FormulaRecipePut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(
        default="", max_length=recipes.MAX_DESCRIPTION_LENGTH)
    formulas: list[FormulaSpec] = Field(
        min_length=1, max_length=formula.MAX_FORMULAS)


class EditOps(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rename: dict[str, str] = Field(default_factory=dict)
    drop: list[str] = Field(default_factory=list)
    trim_t0: float | None = None
    trim_t1: float | None = None
    nan_policy: str | None = None
    formulas: list[FormulaSpec] = Field(
        default_factory=list, max_length=formula.MAX_FORMULAS)


@app.patch("/api/tests/{name}/meta")
def api_patch_meta(name: str, payload: UserMetaPatch):
    """Patch supplied text fields; user_meta, when supplied, remains a replacement.
    Omitted fields and all scientific/provenance metadata are preserved.
    """
    _reject_if_busy(name)  # don't park on the lock during a rebuild/upload
    with test_write(name):
        if store.get_status(name).get("status") in BUSY_STATUSES:
            raise HTTPException(409, f"'{name}' became busy; retry")
        meta_path = TESTS_DIR / name / "meta.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            raise HTTPException(404, f"test '{name}' not found")
        if "components" in payload.model_fields_set:
            components.apply_assignments(meta, payload.components, payload.expected_components_revision)
        if "component_rpm_column" in payload.model_fields_set:
            component_stats.apply_rpm(meta, payload.component_rpm_column, payload.expected_component_rpm_revision)
        for key in ("user_meta", "description", "notes"):
            if key in payload.model_fields_set:
                meta[key] = getattr(payload, key)
        store.write_json_atomic(meta_path, meta)
        return meta


@app.post("/api/tests/{name}/formulas/preview")
def api_preview_formulas(name: str, payload: FormulaPreviewRequest):
    """Synchronously validate formulas and evaluate representative rows."""
    _reject_if_busy(name)
    with data_read(name):
        meta = store.get_meta(name)
        if meta is None:
            raise HTTPException(
                404, f"test '{name}' not found or not ready")
        if store.get_status(name).get("status") != "ready":
            raise HTTPException(409, f"test '{name}' is not ready")
        try:
            specs = formula.expand_formula_dependents(
                payload.formulas, meta.get("derived_variables"))
            return formula.preview_formulas(
                TESTS_DIR / name / "data.parquet",
                meta,
                specs,
                payload.sample_size,
            )
        except formula.FormulaError as exc:
            raise HTTPException(400, str(exc)) from None


@app.post("/api/tests/{name}/edit")
def api_edit(name: str, ops: EditOps, background: BackgroundTasks):
    """Schedule a destructive rebuild.

    Supports column rename/drop, trim, NaN policy, or a standalone ordered
    formula batch. Validates against current meta, then runs like an ingest
    (status 'rebuilding' -> 'ready'/'error').
    """
    meta = store.get_meta(name)
    if meta is None:
        raise HTTPException(404, f"test '{name}' not found")
    if store.get_status(name).get("status") != "ready":
        raise HTTPException(409, f"test '{name}' is not ready")

    tcol = meta["time_column"]
    columns = set(meta["columns"])
    has_legacy_op = bool(
        ops.rename or ops.drop or ops.nan_policy
        or ops.trim_t0 is not None or ops.trim_t1 is not None)
    if ops.formulas and has_legacy_op:
        raise HTTPException(
            400, "formulas cannot be combined with rename, drop, trim, or "
                 "NaN-policy operations in one edit")
    has_op = bool(ops.rename or ops.drop or ops.nan_policy
                  or ops.trim_t0 is not None or ops.trim_t1 is not None
                  or ops.formulas)
    if not has_op:
        raise HTTPException(400, "no edit operations given")

    if ops.formulas:
        try:
            specs = formula.expand_formula_dependents(
                ops.formulas, meta.get("derived_variables"))
            formula.compile_formula_batch(
                specs, meta["columns"], tcol)
        except formula.FormulaError as exc:
            raise HTTPException(400, str(exc)) from None

    unknown = [c for c in list(ops.rename) + ops.drop if c not in columns]
    if unknown:
        raise HTTPException(400, f"unknown columns: {unknown}")
    if tcol in ops.drop or tcol in ops.rename:
        raise HTTPException(400, "the time column cannot be dropped or renamed")
    targets = list(ops.rename.values())
    remaining = (columns - set(ops.drop) - set(ops.rename)) | set(targets)
    if len(targets) != len(set(targets)) or len(remaining) != \
            len(columns) - len(ops.drop):
        raise HTTPException(400, "rename would produce duplicate column names")
    for new_name in targets:
        if not new_name or not re.fullmatch(r"[A-Za-z0-9_.\-]+", new_name):
            raise HTTPException(
                400, f"invalid column name '{new_name}': use letters, "
                     "digits, '_', '.', '-'")
    if len(columns) - len(ops.drop) < 2:
        raise HTTPException(400, "cannot drop every data column")

    if ops.trim_t0 is not None or ops.trim_t1 is not None:
        t_start = meta.get("t_start") or 0.0
        t_end = t_start + meta["duration_s"]
        lo = t_start if ops.trim_t0 is None else ops.trim_t0
        hi = t_end if ops.trim_t1 is None else ops.trim_t1
        if (not (t_start - 1e-9 <= lo < hi <= t_end + 1e-9)
                or hi - lo < 1.0):
            raise HTTPException(
                400, f"trim range must satisfy {t_start:g} <= t0 < t1 <= "
                     f"{t_end:g} and keep at least 1 s of data")

    if ops.nan_policy is not None and ops.nan_policy not in edit.NAN_POLICIES:
        raise HTTPException(
            400, f"nan_policy must be one of {edit.NAN_POLICIES} "
                 "('drop rows' would break the uniform sample rate)")

    with test_write(name):
        # Re-check under the lock: two /edit requests racing through the
        # validation above must not both schedule — the second would run ops
        # validated against the schema the first is about to change.
        if store.get_status(name).get("status") != "ready":
            raise HTTPException(409, f"test '{name}' is not ready")
        write_status(TESTS_DIR / name, "rebuilding")
    background.add_task(edit.rebuild_test, name, ops.model_dump())
    return {"name": name, "status": "rebuilding"}


# ---------- global formula recipes ----------

@app.get("/api/formula-recipes")
def api_list_formula_recipes():
    try:
        return recipes.list_recipes()
    except recipes.RecipeError as exc:
        raise HTTPException(500, str(exc)) from None


@app.get("/api/formula-recipes/{recipe_name}")
def api_get_formula_recipe(recipe_name: str):
    try:
        recipe = recipes.get_recipe(recipe_name)
    except recipes.RecipeError as exc:
        raise HTTPException(400, str(exc)) from None
    if recipe is None:
        raise HTTPException(
            404, f"formula recipe '{recipe_name}' not found")
    return recipe


@app.put("/api/formula-recipes/{recipe_name}")
def api_put_formula_recipe(
    recipe_name: str,
    payload: FormulaRecipePut,
):
    try:
        return recipes.put_recipe(
            recipe_name, payload.description, payload.formulas)
    except recipes.RecipeError as exc:
        raise HTTPException(400, str(exc)) from None


@app.delete("/api/formula-recipes/{recipe_name}")
def api_delete_formula_recipe(recipe_name: str):
    try:
        deleted = recipes.delete_recipe(recipe_name)
    except recipes.RecipeError as exc:
        raise HTTPException(400, str(exc)) from None
    if not deleted:
        raise HTTPException(
            404, f"formula recipe '{recipe_name}' not found")
    return {"ok": True, "name": recipes.validate_recipe_name(recipe_name)}


# ---------- data windows ----------

@app.get("/api/tests/{name}/data")
@with_test_read
def api_data(name: str,
             cols: str = Query(..., description="comma-separated column names"),
             t0: float | None = None, t1: float | None = None,
             px: int = 1500,
             display: Literal["auto", "line", "envelope"] = "auto"):
    meta = store.get_meta(name)
    if meta is None:
        raise HTTPException(404, f"test '{name}' not found or not ready")
    col_list = _data_columns(meta, cols)
    return store.read_window(name, col_list, t0, t1, px, display)


# ---------- csv export / raw download ----------

def _data_columns(meta: dict, cols: str) -> list[str]:
    """Validated, deduped data-column selection for /data and /filter.

    Drops the time column (always returned separately as ``t``): requesting it
    via ``cols`` would otherwise make a duplicate polars select in raw mode, or
    look for a non-existent ``{tcol}__min`` pyramid column in envelope mode —
    both 500s.  /xy dedupes the same way in store.read_xy."""
    col_list = [c.strip() for c in cols.split(",") if c.strip()]
    unknown = [c for c in col_list if c not in meta["columns"]]
    if unknown:
        raise HTTPException(400, f"unknown columns: {unknown}")
    col_list = [c for c in dict.fromkeys(col_list) if c != meta["time_column"]]
    if not col_list:
        raise HTTPException(400, "no data columns requested")
    return col_list


def _export_columns(meta: dict, cols: str) -> list[str]:
    """Validated export column selection: time column always first, deduped
    (so `cols` naming the time column cannot produce a duplicate select)."""
    col_list = [c.strip() for c in cols.split(",") if c.strip()]
    unknown = [c for c in col_list if c not in meta["columns"]]
    if unknown:
        raise HTTPException(400, f"unknown columns: {unknown}")
    return list(dict.fromkeys(
        [meta["time_column"], *(col_list or meta["columns"])]))


def _csv_response(name: str, columns: list[str], i0: int, i1: int,
                  filename: str) -> StreamingResponse:
    """Stream rows [i0, i1) as a CSV download.

    The generator acquires the read locks itself: a StreamingResponse body
    runs after the endpoint returns, so a @with_test_read lock would already
    be released while the parquet file is still being read (and a rebuild
    could swap data.parquet mid-download)."""
    def stream():
        with data_read(name):
            yield from store.stream_csv(name, columns, i0, i1)

    return StreamingResponse(
        stream(), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/api/tests/{name}/export")
def api_export(name: str, cols: str = "",
               t0: float | None = None, t1: float | None = None):
    """Full-resolution CSV of the test, or of a [t0, t1] window of it.
    `cols` empty = every column."""
    meta = store.get_meta(name)
    if meta is None:
        raise HTTPException(404, f"test '{name}' not found or not ready")
    columns = _export_columns(meta, cols)
    i0, i1 = store.window_bounds(meta, t0, t1)
    suffix = "" if t0 is None and t1 is None else f"_rows{i0}-{i1}"
    return _csv_response(name, columns, i0, i1, f"{name}{suffix}.csv")


@app.get("/api/tests/{name}/testpoints/{tp_id}/export")
def api_export_testpoint(name: str, tp_id: int, cols: str = "",
                         start_idx: int | None = None,
                         end_idx: int | None = None):
    """Full-resolution TP CSV with generated test_point_id on every row.

    No indices: use the saved TP. Both indices: explicit half-open draft rows,
    without reading or writing saved definitions (also supports new TPs).
    """
    if (start_idx is None) != (end_idx is None):
        raise HTTPException(400, "draft export requires both start_idx and end_idx")
    draft = start_idx is not None

    def resolve():
        _reject_if_busy(name)
        meta = store.get_meta(name)
        if meta is None:
            raise HTTPException(404, f"test '{name}' not found or not ready")
        if store.get_status(name).get("status") == "error":
            raise HTTPException(409, "test data is unavailable; download the original CSV")
        columns = _export_columns(meta, cols)
        try:
            if draft:
                # Same clamping/empty-range policy as saved points. The editor
                # supplies its Save-converted row indices, including open ends.
                i0, i1 = store._testpoint_bounds(meta, [], {
                    "id": tp_id, "start_idx": start_idx, "end_idx": end_idx})
            else:
                i0, i1 = store.testpoint_range(name, tp_id)
        except KeyError:
            raise HTTPException(404, f"test point {tp_id} not found in test '{name}'")
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return columns, i0, i1, store.tp_export_source_alias(meta["columns"])

    # Preflight errors before sending attachment headers. Resolve again under
    # the body lock so a save/rebuild between response creation and streaming
    # cannot pair stale TP bounds/schema with newly written samples.
    _reject_if_busy(name)
    with test_read(name):
        resolve()

    def stream():
        with data_read(name):
            columns, i0, i1, alias = resolve()
            yield from store.stream_csv(name, columns, i0, i1, tp_id, alias)

    suffix = "_draft" if draft else ""
    return StreamingResponse(stream(), media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="{name}_tp{tp_id}{suffix}.csv"'})


@app.get("/api/tests/{name}/raw")
def api_download_raw(name: str):
    """The original uploaded CSV (raw.csv), kept for provenance."""
    tests_root = TESTS_DIR.resolve()
    test_dir = (TESTS_DIR / name).resolve()
    if test_dir.parent != tests_root or not test_dir.is_dir():
        raise HTTPException(404, f"test '{name}' not found")
    raw = test_dir / "raw.csv"
    if not raw.is_file():
        raise HTTPException(404, f"no raw.csv stored for '{name}'")
    filename = (store.get_meta(name) or {}).get("source_file") or f"{name}.csv"
    return FileResponse(raw, media_type="text/csv", filename=filename)


# ---------- xy + tp stats ----------

@app.get("/api/tests/{name}/xy")
@with_test_read
def api_xy(name: str, x: str = Query(...), y: str | None = None,
           t0: float | None = None, t1: float | None = None,
           max_pts: int = Query(3000, ge=4, le=20000), tp_id: int | None = None,
           y_col: str | None = None):
    meta = store.get_meta(name)
    if meta is None:
        raise HTTPException(404, f"test '{name}' not found or not ready")
    if y_col is not None and y is not None:
        raise HTTPException(400, "use y_col for one exact variable or y for the legacy column list")
    y_cols = [y_col] if y_col is not None else [c.strip() for c in (y or '').split(",") if c.strip()]
    unknown = [c for c in [x] + y_cols if c not in meta["columns"]]
    if unknown:
        raise HTTPException(400, f"unknown columns: {unknown}")
    if not y_cols:
        raise HTTPException(400, "no y columns requested")
    try:
        return store.read_xy(name, x, y_cols, t0, t1, max_pts, tp_id=tp_id)
    except KeyError:
        raise HTTPException(404, f"test point {tp_id} not found in '{name}'")
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/tests/{name}/tp_stats")
@with_test_read
def api_tp_stats(name: str, col: str = Query(...)):
    meta = store.get_meta(name)
    if meta is None:
        raise HTTPException(404, f"test '{name}' not found or not ready")
    if col not in meta["columns"]:
        raise HTTPException(400, f"unknown column: {col}")
    return store.tp_stats(name, col)


@app.post("/api/tests/{name}/tp_stats/rebuild")
@with_test_read
def api_rebuild_tp_stats(name: str):
    """Force a fresh recompute of the cached test-point averages.

    Non-destructive: the sidecar already self-invalidates on any data/TP
    change, so this normally reproduces the same numbers — it exists as a
    manual override, and it swaps the result in atomically so the previous
    averages keep serving until it finishes."""
    if store.get_meta(name) is None:
        raise HTTPException(404, f"test '{name}' not found or not ready")
    return {"name": name, "columns_recomputed": store.rebuild_tp_stats(name)}


# ---------- signal processing ----------

@app.get("/api/tests/{name}/filter")
@with_test_read
def api_filter(name: str,
               cols: str = Query(..., description="comma-separated column names"),
               kind: str = Query(..., alias="type"),
               t0: float | None = None, t1: float | None = None,
               px: int = 1500, order: int = 4,
               f1: float | None = None, f2: float | None = None,
               window_s: float | None = None,
               max_spike_s: float = dsp.DEFAULT_MAX_SPIKE_S,
               threshold: float = dsp.DEFAULT_DESPIKE_THRESHOLD,
               abs_floor: float = dsp.DEFAULT_DESPIKE_ABS_FLOOR,
               replacement: Literal["linear", "median"] = "linear",
               display: Literal["auto", "line", "envelope"] = "auto",
               tp_id: int | None = None):
    meta = store.get_meta(name)
    if meta is None:
        raise HTTPException(404, f"test '{name}' not found or not ready")
    col_list = _data_columns(meta, cols)
    try:
        return dsp.filtered_window(
            name, col_list, kind, t0, t1, px,
            order=order, f1=f1, f2=f2, window_s=window_s,
            display=display, max_spike_s=max_spike_s,
            threshold=threshold, abs_floor=abs_floor,
            replacement=replacement, tp_id=tp_id)
    except KeyError:
        raise HTTPException(404, f"test point {tp_id} not found")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/tests/{name}/spectrum")
@with_test_read
def api_spectrum(name: str, col: str = Query(...),
                 mode: Literal["fft", "welch"] = "fft",
                 t0: float | None = None, t1: float | None = None,
                 nperseg: int = 4096,
                 rpm_col: str | None = None,
                 tp_id: int | None = None):
    meta = store.get_meta(name)
    if meta is None:
        raise HTTPException(404, f"test '{name}' not found or not ready")
    if col not in meta["columns"]:
        raise HTTPException(400, f"unknown column: {col}")
    if rpm_col is not None and rpm_col not in meta["columns"]:
        raise HTTPException(400, f"unknown RPM column: {rpm_col}")
    try:
        return dsp.spectrum(
            name, col, mode, t0, t1, nperseg, rpm_col=rpm_col, tp_id=tp_id)
    except KeyError:
        raise HTTPException(404, f"test point {tp_id} not found")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/tests/{name}/waterfall")
@with_test_read
def api_waterfall(name: str, col: str = Query(...),
                  t0: float | None = None, t1: float | None = None,
                  tp_id: int | None = None, nperseg: int = 1024, overlap: int = 50):
    from .waterfall import calculate
    try:
        return calculate(name, col, t0, t1, tp_id=tp_id, nperseg=nperseg, overlap=overlap)
    except FileNotFoundError:
        raise HTTPException(404, f"test '{name}' not found")
    except KeyError:
        raise HTTPException(404, f"test point {tp_id} not found")
    except ValueError as exc:
        raise HTTPException(400, str(exc))


# ---------- split ----------

class AutoSplitPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    columns: list[Annotated[str, Field(min_length=1)]] = Field(
        min_length=1, max_length=split.MAX_SPLIT_COLUMNS)
    ignore_zero: Annotated[bool, Field(strict=True)] = True
    min_len_s: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 1.0


@app.post("/api/tests/{name}/split/preview")
def api_autosplit_preview(name: str, payload: AutoSplitPreviewRequest):
    # A rebuild may hold its writer for minutes. Fail promptly before waiting,
    # then recheck under the native-read gate to close the readiness race.
    _reject_if_busy(name)
    with data_read(name):
        _reject_if_busy(name)
        if store.get_meta(name) is None:
            raise HTTPException(404, f"test '{name}' not found or not ready")
        try:
            return split.preview_autosplit(
                name, payload.columns, payload.ignore_zero, payload.min_len_s)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc


@app.get("/api/tests/{name}/split/candidates")
@with_test_read
def api_split_candidates(name: str):
    if store.get_meta(name) is None:
        raise HTTPException(404, f"test '{name}' not found or not ready")
    return split.id_candidates(name)


@app.post("/api/tests/{name}/split/auto")
@with_test_read
def api_autosplit(name: str, col: str = Query(...),
                  ignore_zero: bool = True,
                  min_len_s: Annotated[
                      float, Query(ge=0, allow_inf_nan=False)] = 1.0):
    meta = store.get_meta(name)
    if meta is None:
        raise HTTPException(404, f"test '{name}' not found or not ready")
    if col not in meta["columns"]:
        raise HTTPException(400, f"unknown column: {col}")
    return split.autosplit(name, col, ignore_zero, min_len_s)


# ---------- test points ----------

@app.get("/api/tests/{name}/testpoints")
@with_test_read
def api_get_testpoints(name: str, expected_source_id: uuid.UUID | None = None):
    analysis_sources.verify_reference(name, expected_source_id)
    if store.get_meta(name) is None:
        raise HTTPException(404, f"test '{name}' not found or not ready")
    return store.read_testpoints(name)


@app.get("/api/tests/{name}/testpoints/{tp_id}/data")
@with_test_read
def api_get_testpoint_data(
        name: str, tp_id: int,
        cols: str = Query(..., description="comma-separated column names"),
        max_points: int = Query(3000, ge=4, le=POINT_BUDGET_CAP)):
    """Serve one test point on a relative time axis with a hard point cap."""
    meta = store.get_meta(name)
    if meta is None:
        raise HTTPException(404, f"test '{name}' not found or not ready")
    col_list = list(dict.fromkeys(
        col.strip() for col in cols.split(",") if col.strip()))
    unknown = [col for col in col_list if col not in meta["columns"]]
    if unknown:
        raise HTTPException(400, f"unknown columns: {unknown}")
    if not col_list:
        raise HTTPException(400, "no columns requested")
    try:
        return store.read_testpoint_trace(
            name, tp_id, col_list, max_points)
    except KeyError:
        raise HTTPException(
            404, f"test point {tp_id} not found in test '{name}'")
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.put("/api/tests/{name}/testpoints")
def api_put_testpoints(name: str, payload: TestPointsFile):
    _reject_duplicate_ids(payload)
    # Pre-check status before the lock so a rebuild in progress 409s instead of
    # parking this request on test_write for the whole rebuild (bug 1.12).
    _reject_if_busy(name)
    with test_write(name):
        if store.get_status(name).get("status") in BUSY_STATUSES:
            raise HTTPException(409, f"'{name}' became busy; retry")
        if store.get_meta(name) is None:
            raise HTTPException(404, f"test '{name}' not found or not ready")
        store.write_testpoints(name, payload.model_dump())
        return {"ok": True, "n": len(payload.test_points)}


@app.post("/api/tests/{name}/testpoints/upload")
def api_upload_testpoints(name: str, file: UploadFile):
    try:
        payload = TestPointsFile(**json.loads(file.file.read()))
    except Exception as e:
        raise HTTPException(400, f"invalid testpoints file: {e}")
    _reject_duplicate_ids(payload)
    _reject_if_busy(name)  # same rebuild-safety pre-check as PUT above
    with test_write(name):
        if store.get_status(name).get("status") in BUSY_STATUSES:
            raise HTTPException(409, f"'{name}' became busy; retry")
        if store.get_meta(name) is None:
            raise HTTPException(404, f"test '{name}' not found or not ready")
        store.write_testpoints(name, payload.model_dump())
        return {"ok": True, "n": len(payload.test_points)}
