"""FastAPI app: upload/ingest, windowed data serving, test points CRUD.

Run:  uvicorn app.main:app --reload --port 8000   (from backend/)
"""

import json
import logging
import os
import re
import shutil
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import (BackgroundTasks, FastAPI, HTTPException, Query, Request,
                     UploadFile)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.requests import ClientDisconnect

from . import dsp, edit, split, store
from .config import (POINT_BUDGET_CAP, TESTS_DIR, TRASH_DIR,
                     TRASH_MAX_AGE_S)
from .ingest import _write_status, ingest_csv
from .locks import (catalog_read, catalog_write, test_read, test_write,
                    tests_write, with_test_read, with_test_write)


logger = logging.getLogger("kiha.api")


# Statuses of an upload that has not yet become a ready test.  'receiving'
# means the request body is still streaming in; 'ingesting' means the
# background CSV->parquet job runs.  Both must block delete/rename.
INGEST_LIKE = ("receiving", "ingesting")


def _recover_interrupted_ingests() -> None:
    """Make tests left by a process crash manageable again on restart."""
    if not TESTS_DIR.exists():
        return
    with catalog_write():
        for test_dir in TESTS_DIR.iterdir():
            if not test_dir.is_dir():
                continue
            if store.get_status(test_dir.name).get("status") in INGEST_LIKE:
                _write_status(
                    test_dir,
                    "error",
                    "upload/ingestion was interrupted by a backend restart; "
                    "delete this test and upload it again",
                )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _recover_interrupted_ingests()
    yield


app = FastAPI(title="kiha time-series plotter", lifespan=lifespan)

app.add_middleware(GZipMiddleware, minimum_size=1000)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173",
                   "http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


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


# ---------- tests ----------

@app.get("/api/tests")
def api_list_tests():
    with catalog_read():
        return store.list_tests()


@app.get("/api/tests/{name}")
@with_test_read
def api_get_meta(name: str):
    meta = store.get_meta(name)
    if meta is None:
        raise HTTPException(404, f"test '{name}' not found or not ready")
    return meta


@app.get("/api/tests/{name}/status")
def api_get_status(name: str):
    return store.get_status(name)


def _purge_trash():
    if not TRASH_DIR.exists():
        return
    now = time.time()
    for d in TRASH_DIR.iterdir():
        try:
            if now - d.stat().st_mtime > TRASH_MAX_AGE_S:
                shutil.rmtree(d) if d.is_dir() else d.unlink()
        except OSError:
            pass


@app.delete("/api/tests/{name}")
def api_delete_test(name: str):
    """Soft delete: move to trash so the client can offer undo."""
    # Do not wait for a potentially long upload/ingestion only to delete
    # its result (the status pre-check also avoids blocking for minutes on
    # the per-test lock the receiving/ingesting job holds).
    status = store.get_status(name).get("status")
    if status in INGEST_LIKE:
        raise HTTPException(409, f"'{name}' is still {status}")

    with catalog_write(), test_write(name):
        tests_root = TESTS_DIR.resolve()
        test_dir = (TESTS_DIR / name).resolve()
        if test_dir.parent != tests_root or not test_dir.is_dir():
            raise HTTPException(404, f"test '{name}' not found")
        status = store.get_status(name).get("status")
        if status in INGEST_LIKE:
            raise HTTPException(409, f"'{name}' is still {status}")
        TRASH_DIR.mkdir(parents=True, exist_ok=True)
        _purge_trash()
        dst = TRASH_DIR / name
        try:
            if dst.exists():
                shutil.rmtree(dst)
            test_dir.rename(dst)
            os.utime(dst)  # move keeps the old mtime; reset the purge clock
        except OSError as e:
            raise HTTPException(
                409, f"could not delete '{name}' (files in use?): {e}")
        if not dst.is_dir():
            raise HTTPException(500, f"delete of '{name}' did not complete")
    return {"ok": True, "deleted": name, "restorable": True}


@app.post("/api/tests/{name}/restore")
def api_restore_test(name: str):
    with catalog_write(), test_write(name):
        trash_root = TRASH_DIR.resolve()
        src = (TRASH_DIR / name).resolve() if TRASH_DIR.exists() else None
        if src is None or src.parent != trash_root or not src.is_dir():
            raise HTTPException(404, f"no restorable copy of '{name}'")
        dst = TESTS_DIR / name
        if dst.exists():
            raise HTTPException(409, f"test '{name}' already exists")
        try:
            src.rename(dst)
        except OSError as e:
            raise HTTPException(409, f"could not restore '{name}': {e}")
        if not dst.is_dir():
            raise HTTPException(500, f"restore of '{name}' did not complete")
    return {"ok": True, "restored": name}


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

    with catalog_write(), tests_write(name, new_name):
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

        # Rewrite metadata first while readers are excluded.  If either a JSON
        # write or the directory move fails, restore the original documents so
        # the operation is transactional from the API's point of view.
        documents: list[tuple[Path, dict, dict]] = []
        for fname, key in (("meta.json", "name"),
                           ("testpoints.json", "test")):
            p = src / fname
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
    return {"ok": True, "name": new_name}


def _discard_partial_upload(name: str) -> None:
    """Remove the leftovers of a failed transfer so a retry does not 409."""
    test_dir = TESTS_DIR / name
    with catalog_write(), test_write(name):
        try:
            shutil.rmtree(test_dir)
        except OSError:
            # Cannot remove (files in use?) — leave an inspectable error
            # state instead of a test stuck at 'receiving'.
            _write_status(test_dir, "error",
                          "upload did not complete; delete this test and "
                          "upload it again")


@app.post("/api/tests/upload")
async def api_upload(request: Request, background: BackgroundTasks,
                     name: str = Query(default=""),
                     source: str = Query(default="")):
    """Upload a test CSV as the RAW request body (not multipart).

    ?name= is required (multipart carried the filename; a raw body cannot).
    ?source= is the optional original file name, recorded as
    meta.source_file for the upload history (the body lands in raw.csv,
    so the client's file name would otherwise be lost).
    Raw-body streaming is deliberate: with UploadFile, starlette spools the
    whole multipart body to a temp file BEFORE the endpoint runs, so a
    multi-GB transfer produced minutes of dead air — no status.json, no log
    line, an extra full disk copy.  Here the handler starts with the headers:
    status 'receiving' is visible to /api/tests immediately and bytes go
    straight to raw.csv.  Lifecycle: receiving -> ingesting -> ready|error.
    """
    test_name = name
    if (not TEST_NAME_RE.fullmatch(test_name)
            or not re.search(r"[A-Za-z0-9]", test_name)):
        raise HTTPException(
            400, "test name may only contain letters, digits, '.', '_', '-'")
    test_dir = TESTS_DIR / test_name
    with catalog_write():
        if test_dir.exists():
            raise HTTPException(409, f"test '{test_name}' already exists")
        test_dir.mkdir(parents=True)
        # Publish the state before receiving so delete/rename cannot race
        # the (possibly minutes-long) body transfer.
        _write_status(test_dir, "receiving")
    raw_path = test_dir / "raw.csv"
    expected = int(request.headers.get("content-length") or 0)
    logger.info("upload '%s': receiving %s", test_name,
                f"{expected / 1e6:,.1f} MB" if expected else "(unknown size)")
    t0 = time.time()
    received = 0
    try:
        # Holding test_write across the transfer is safe: mutating endpoints
        # 409 on the 'receiving' status before ever touching this lock.
        with test_write(test_name), open(raw_path, "wb") as out:
            async for chunk in request.stream():
                out.write(chunk)
                received += len(chunk)
    except ClientDisconnect:
        logger.warning("upload '%s': client disconnected after %.1f of "
                       "%.1f MB — discarding", test_name, received / 1e6,
                       expected / 1e6)
        _discard_partial_upload(test_name)
        raise HTTPException(400, "client disconnected during upload")
    except OSError as e:
        logger.exception("upload '%s': could not store body", test_name)
        _discard_partial_upload(test_name)
        raise HTTPException(500, f"could not store upload: {e}")
    if expected and received != expected:
        logger.warning("upload '%s': truncated body (%d of %d bytes) — "
                       "discarding", test_name, received, expected)
        _discard_partial_upload(test_name)
        raise HTTPException(400, "upload was truncated; please retry")
    logger.info("upload '%s': %.1f MB stored in %.1f s, ingest scheduled",
                test_name, received / 1e6, time.time() - t0)
    _write_status(test_dir, "ingesting")
    background.add_task(ingest_csv, raw_path, test_name,
                        source_name=source[:255] or None)
    return {"name": test_name, "status": "ingesting"}


# ---------- editing ----------

class UserMetaPatch(BaseModel):
    user_meta: dict[str, str] = Field(default_factory=dict)


class EditOps(BaseModel):
    rename: dict[str, str] = Field(default_factory=dict)
    drop: list[str] = Field(default_factory=list)
    trim_t0: float | None = None
    trim_t1: float | None = None
    nan_policy: str | None = None


@app.patch("/api/tests/{name}/meta")
def api_patch_meta(name: str, payload: UserMetaPatch):
    """Replace the free-form user_meta block (prop/motor/ESC descriptors...).
    Nothing else in meta.json is writable from the API."""
    with test_write(name):
        meta_path = TESTS_DIR / name / "meta.json"
        try:
            meta = json.loads(meta_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            raise HTTPException(404, f"test '{name}' not found")
        meta["user_meta"] = payload.user_meta
        store.write_json_atomic(meta_path, meta)
        return meta


@app.post("/api/tests/{name}/edit")
def api_edit(name: str, ops: EditOps, background: BackgroundTasks):
    """Schedule a destructive rebuild: column rename/drop, trim, NaN policy.
    Validates against current meta, then runs like an ingest (status
    'rebuilding' -> 'ready'/'error')."""
    meta = store.get_meta(name)
    if meta is None:
        raise HTTPException(404, f"test '{name}' not found")
    if store.get_status(name).get("status") != "ready":
        raise HTTPException(409, f"test '{name}' is not ready")

    tcol = meta["time_column"]
    columns = set(meta["columns"])
    has_op = bool(ops.rename or ops.drop or ops.nan_policy
                  or ops.trim_t0 is not None or ops.trim_t1 is not None)
    if not has_op:
        raise HTTPException(400, "no edit operations given")

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

    t_start = meta.get("t_start") or 0.0
    t_end = t_start + meta["duration_s"]
    lo = t_start if ops.trim_t0 is None else ops.trim_t0
    hi = t_end if ops.trim_t1 is None else ops.trim_t1
    if not (t_start - 1e-9 <= lo < hi <= t_end + 1e-9) or hi - lo < 1.0:
        raise HTTPException(
            400, f"trim range must satisfy {t_start:g} <= t0 < t1 <= "
                 f"{t_end:g} and keep at least 1 s of data")

    if ops.nan_policy is not None and ops.nan_policy not in edit.NAN_POLICIES:
        raise HTTPException(
            400, f"nan_policy must be one of {edit.NAN_POLICIES} "
                 "('drop rows' would break the uniform sample rate)")

    with test_write(name):
        _write_status(TESTS_DIR / name, "rebuilding")
    background.add_task(edit.rebuild_test, name, ops.model_dump())
    return {"name": name, "status": "rebuilding"}


# ---------- data windows ----------

@app.get("/api/tests/{name}/data")
@with_test_read
def api_data(name: str,
             cols: str = Query(..., description="comma-separated column names"),
             t0: float | None = None, t1: float | None = None,
             px: int = 1500):
    meta = store.get_meta(name)
    if meta is None:
        raise HTTPException(404, f"test '{name}' not found or not ready")
    col_list = [c.strip() for c in cols.split(",") if c.strip()]
    unknown = [c for c in col_list if c not in meta["columns"]]
    if unknown:
        raise HTTPException(400, f"unknown columns: {unknown}")
    if not col_list:
        raise HTTPException(400, "no columns requested")
    return store.read_window(name, col_list, t0, t1, px)


# ---------- xy + tp stats ----------

@app.get("/api/tests/{name}/xy")
@with_test_read
def api_xy(name: str, x: str = Query(...), y: str = Query(...),
           t0: float | None = None, t1: float | None = None,
           max_pts: int = 3000):
    meta = store.get_meta(name)
    if meta is None:
        raise HTTPException(404, f"test '{name}' not found or not ready")
    y_cols = [c.strip() for c in y.split(",") if c.strip()]
    unknown = [c for c in [x] + y_cols if c not in meta["columns"]]
    if unknown:
        raise HTTPException(400, f"unknown columns: {unknown}")
    if not y_cols:
        raise HTTPException(400, "no y columns requested")
    return store.read_xy(name, x, y_cols, t0, t1, min(max_pts, 20000))


@app.get("/api/tests/{name}/tp_stats")
@with_test_read
def api_tp_stats(name: str, col: str = Query(...)):
    meta = store.get_meta(name)
    if meta is None:
        raise HTTPException(404, f"test '{name}' not found or not ready")
    if col not in meta["columns"]:
        raise HTTPException(400, f"unknown column: {col}")
    return store.tp_stats(name, col)


# ---------- signal processing ----------

@app.get("/api/tests/{name}/filter")
@with_test_read
def api_filter(name: str,
               cols: str = Query(..., description="comma-separated column names"),
               kind: str = Query(..., alias="type"),
               t0: float | None = None, t1: float | None = None,
               px: int = 1500, order: int = 4,
               f1: float | None = None, f2: float | None = None,
               window_s: float | None = None):
    meta = store.get_meta(name)
    if meta is None:
        raise HTTPException(404, f"test '{name}' not found or not ready")
    col_list = [c.strip() for c in cols.split(",") if c.strip()]
    unknown = [c for c in col_list if c not in meta["columns"]]
    if unknown:
        raise HTTPException(400, f"unknown columns: {unknown}")
    if not col_list:
        raise HTTPException(400, "no columns requested")
    try:
        return dsp.filtered_window(name, col_list, kind, t0, t1, px,
                                   order, f1, f2, window_s)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/tests/{name}/spectrum")
@with_test_read
def api_spectrum(name: str, col: str = Query(...),
                 mode: Literal["fft", "welch"] = "fft",
                 t0: float | None = None, t1: float | None = None,
                 nperseg: int = 4096):
    meta = store.get_meta(name)
    if meta is None:
        raise HTTPException(404, f"test '{name}' not found or not ready")
    if col not in meta["columns"]:
        raise HTTPException(400, f"unknown column: {col}")
    try:
        return dsp.spectrum(name, col, mode, t0, t1, nperseg)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ---------- split ----------

@app.get("/api/tests/{name}/split/candidates")
@with_test_read
def api_split_candidates(name: str):
    if store.get_meta(name) is None:
        raise HTTPException(404, f"test '{name}' not found or not ready")
    return split.id_candidates(name)


@app.post("/api/tests/{name}/split/auto")
@with_test_read
def api_autosplit(name: str, col: str = Query(...),
                  ignore_zero: bool = True, min_len_s: float = 1.0):
    meta = store.get_meta(name)
    if meta is None:
        raise HTTPException(404, f"test '{name}' not found or not ready")
    if col not in meta["columns"]:
        raise HTTPException(400, f"unknown column: {col}")
    return split.autosplit(name, col, ignore_zero, min_len_s)


# ---------- test points ----------

@app.get("/api/tests/{name}/testpoints")
@with_test_read
def api_get_testpoints(name: str):
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
@with_test_write
def api_put_testpoints(name: str, payload: TestPointsFile):
    if store.get_meta(name) is None:
        raise HTTPException(404, f"test '{name}' not found or not ready")
    store.write_testpoints(name, payload.model_dump())
    return {"ok": True, "n": len(payload.test_points)}


@app.post("/api/tests/{name}/testpoints/upload")
@with_test_write
def api_upload_testpoints(name: str, file: UploadFile):
    if store.get_meta(name) is None:
        raise HTTPException(404, f"test '{name}' not found or not ready")
    import json
    try:
        payload = TestPointsFile(**json.loads(file.file.read()))
    except Exception as e:
        raise HTTPException(400, f"invalid testpoints file: {e}")
    store.write_testpoints(name, payload.model_dump())
    return {"ok": True, "n": len(payload.test_points)}
