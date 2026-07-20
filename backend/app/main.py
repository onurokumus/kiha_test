"""FastAPI app: upload/ingest, windowed data serving, test points CRUD.

Run:  backend\\.venv\\Scripts\\python.exe backend\\run.py   (port 8000)

Use run.py, NOT `uvicorn app.main:app` directly: run.py installs the
SelectorEventLoop policy that avoids the Windows/py3.14 polars crash and wires
logging.basicConfig so kiha.* log lines are emitted. Launching uvicorn straight
skips both (see CLAUDE.md).
"""

import json
import logging
import os
import re
import shutil
import time
import uuid
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import (BackgroundTasks, FastAPI, HTTPException, Query, Request,
                     UploadFile)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from . import dsp, edit, split, store
from .config import (MAX_UPLOAD_BYTES, POINT_BUDGET_CAP, TESTS_DIR, TRASH_DIR,
                     TRASH_MAX_AGE_S, UPLOAD_SNIFF_BYTES)
from .ingest import ingest_csv
from .locks import (catalog_read, catalog_write, data_read, drop_test_lock,
                    test_read, test_write, tests_write, with_test_read)
from .status import BUSY_STATUSES, INGEST_LIKE, write_status


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


def _recover_interrupted_ingests() -> None:
    """Make tests left by a process crash manageable again on restart."""
    if not TESTS_DIR.exists():
        return
    with catalog_write():
        for test_dir in TESTS_DIR.iterdir():
            if not test_dir.is_dir():
                continue
            status = store.get_status(test_dir.name).get("status")
            if status in INGEST_LIKE:
                write_status(
                    test_dir,
                    "error",
                    "upload/ingestion was interrupted by a backend restart; "
                    "delete this test and upload it again",
                )
            elif status == "rebuilding":
                write_status(
                    test_dir,
                    "error",
                    "an edit/rebuild was interrupted by a backend restart "
                    "and the on-disk data may be inconsistent; delete this "
                    "test and upload it again",
                )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _recover_interrupted_ingests()
    yield


app = FastAPI(title="kiha time-series plotter", lifespan=lifespan)

app.add_middleware(GZipMiddleware, minimum_size=1000)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
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
    # The name is gone from tests/ — forget its RW lock (bug 4.8). A restore
    # lazily recreates one.
    drop_test_lock(name)
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
    # The old name no longer exists — forget its RW lock (bug 4.8); the new
    # name lazily gets its own on first use.
    drop_test_lock(name)
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
            write_status(test_dir, "error",
                          "upload did not complete; delete this test and "
                          "upload it again")


def _reserve_upload_dir(test_name: str) -> None:
    """Create the test dir and publish 'receiving' under catalog_write.

    Called via run_in_threadpool from the async upload endpoint: acquiring the
    catalog lock can block for as long as another writer holds it (a delete
    purging a multi-GB trash entry runs an rmtree inside its lock), and doing
    that ON the event loop would freeze every client. In a worker thread the
    wait is harmless. Publishing 'receiving' before returning still lets
    delete/rename see the reservation immediately (bug 1.5)."""
    test_dir = TESTS_DIR / test_name
    with catalog_write():
        if test_dir.exists():
            raise HTTPException(409, f"test '{test_name}' already exists")
        test_dir.mkdir(parents=True)
        # Publish the state before receiving so delete/rename cannot race
        # the (possibly minutes-long) body transfer.
        write_status(test_dir, "receiving")


@app.post("/api/tests/upload")
async def api_upload(request: Request, background: BackgroundTasks,
                     name: str = Query(default=""),
                     source: str = Query(default=""),
                     fs: float | None = Query(default=None, gt=0)):
    """Upload a test CSV as the RAW request body (not multipart).

    ?name= is required (multipart carried the filename; a raw body cannot).
    ?source= is the optional original file name, recorded as
    meta.source_file for the upload history (the body lands in raw.csv,
    so the client's file name would otherwise be lost).
    ?fs= is the sample rate to assume ONLY if the file's time column turns
    out to be unusable (a uniform axis is then generated); it does not
    override a good, measurable time column.
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
    # Every synchronous, potentially-blocking step (catalog-lock acquisition,
    # per-chunk disk writes, status writes whose atomic-replace retries can
    # sleep on Windows, discard cleanup) runs in a worker thread. This handler
    # is `async` only so it can consume request.stream(); nothing here may block
    # the event loop, or one slow filesystem op would freeze all clients (1.5).
    #
    # The transfer no longer holds test_write across the stream (that would be a
    # threading lock held for minutes over `await` points). The 'receiving'
    # status published atomically under catalog_write below IS the guard: every
    # mutating endpoint 409s on it before touching the lock (delete/rename via
    # INGEST_LIKE, edit/patch/testpoints via _reject_if_busy), a duplicate name
    # 409s on the dir already existing, and ingest is only scheduled once the
    # transfer completes — so nothing else writes this test dir while it streams.
    # Reject an over-cap upload from its declared size BEFORE reserving the dir
    # or writing a byte (a mistaken multi-GB non-CSV must not fill the volume).
    # content-length can be absent/wrong, so the stream is also capped below.
    expected = int(request.headers.get("content-length") or 0)
    if expected and expected > MAX_UPLOAD_BYTES:
        raise HTTPException(
            413, f"upload is {expected / 1024**3:.1f} GB; the limit is "
                 f"{MAX_UPLOAD_BYTES / 1024**3:.0f} GB")

    await run_in_threadpool(_reserve_upload_dir, test_name)
    raw_path = test_dir / "raw.csv"
    logger.info("upload '%s': receiving %s", test_name,
                f"{expected / 1e6:,.1f} MB" if expected else "(unknown size)")
    t0 = time.time()
    received = 0
    first_chunk = True
    out = await run_in_threadpool(open, raw_path, "wb")

    async def _reject(status: int, detail: str):
        await run_in_threadpool(out.close)
        await run_in_threadpool(_discard_partial_upload, test_name)
        raise HTTPException(status, detail)

    try:
        async for chunk in request.stream():
            if first_chunk:
                first_chunk = False
                if b"\x00" in chunk[:UPLOAD_SNIFF_BYTES]:
                    # binary content (NUL byte) — a text CSV never contains one
                    await _reject(400, "file does not look like a CSV "
                                       "(binary content detected)")
            await run_in_threadpool(out.write, chunk)
            received += len(chunk)
            if received > MAX_UPLOAD_BYTES:
                await _reject(
                    413, f"upload exceeds the {MAX_UPLOAD_BYTES / 1024**3:.0f} "
                         "GB limit")
    except ClientDisconnect:
        logger.warning("upload '%s': client disconnected after %.1f of "
                       "%.1f MB — discarding", test_name, received / 1e6,
                       expected / 1e6)
        await run_in_threadpool(out.close)
        await run_in_threadpool(_discard_partial_upload, test_name)
        raise HTTPException(400, "client disconnected during upload")
    except OSError as e:
        logger.exception("upload '%s': could not store body", test_name)
        await run_in_threadpool(out.close)
        await run_in_threadpool(_discard_partial_upload, test_name)
        raise HTTPException(500, f"could not store upload: {e}")
    else:
        await run_in_threadpool(out.close)
    if expected and received != expected:
        logger.warning("upload '%s': truncated body (%d of %d bytes) — "
                       "discarding", test_name, received, expected)
        await run_in_threadpool(_discard_partial_upload, test_name)
        raise HTTPException(400, "upload was truncated; please retry")
    logger.info("upload '%s': %.1f MB stored in %.1f s, ingest scheduled",
                test_name, received / 1e6, time.time() - t0)
    await run_in_threadpool(write_status, test_dir, "ingesting")
    background.add_task(ingest_csv, raw_path, test_name,
                        source_name=source[:255] or None, assume_fs=fs)
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
    _reject_if_busy(name)  # don't park on the lock during a rebuild/upload
    with test_write(name):
        if store.get_status(name).get("status") in BUSY_STATUSES:
            raise HTTPException(409, f"'{name}' became busy; retry")
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
        # Re-check under the lock: two /edit requests racing through the
        # validation above must not both schedule — the second would run ops
        # validated against the schema the first is about to change.
        if store.get_status(name).get("status") != "ready":
            raise HTTPException(409, f"test '{name}' is not ready")
        write_status(TESTS_DIR / name, "rebuilding")
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
    col_list = _data_columns(meta, cols)
    return store.read_window(name, col_list, t0, t1, px)


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
def api_export_testpoint(name: str, tp_id: int, cols: str = ""):
    """Exact-boundary CSV of one saved test point."""
    meta = store.get_meta(name)
    if meta is None:
        raise HTTPException(404, f"test '{name}' not found or not ready")
    columns = _export_columns(meta, cols)
    try:
        i0, i1 = store.testpoint_range(name, tp_id)
    except KeyError:
        raise HTTPException(
            404, f"test point {tp_id} not found in test '{name}'")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return _csv_response(name, columns, i0, i1, f"{name}_tp{tp_id}.csv")


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
def api_xy(name: str, x: str = Query(...), y: str = Query(...),
           t0: float | None = None, t1: float | None = None,
           max_pts: int = Query(3000, ge=4, le=20000)):
    meta = store.get_meta(name)
    if meta is None:
        raise HTTPException(404, f"test '{name}' not found or not ready")
    y_cols = [c.strip() for c in y.split(",") if c.strip()]
    unknown = [c for c in [x] + y_cols if c not in meta["columns"]]
    if unknown:
        raise HTTPException(400, f"unknown columns: {unknown}")
    if not y_cols:
        raise HTTPException(400, "no y columns requested")
    return store.read_xy(name, x, y_cols, t0, t1, max_pts)


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
               window_s: float | None = None):
    meta = store.get_meta(name)
    if meta is None:
        raise HTTPException(404, f"test '{name}' not found or not ready")
    col_list = _data_columns(meta, cols)
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
