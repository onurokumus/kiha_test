"""Crash-safe, resumable multipart CSV uploads.

The browser sends server-sized chunks in a few parallel requests.  Each part
is SHA-256 verified, written into one random-access staging file, flushed, and
then represented by an atomic commit receipt.  Receipts -- not sparse-file
size -- are the source of truth for progress and completion.

The backend is intentionally single-process (see run.py/CLAUDE.md).  Existing
per-test writer locks serialize the short local commit/finalize sections while
multipart receive/spooling and browser hashing can still overlap.
"""

from __future__ import annotations

import errno
import hashlib
import json
import logging
import math
import os
import re
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Literal
from urllib.parse import parse_qs

from fastapi import (APIRouter, BackgroundTasks, Header, HTTPException, Query,
                     Request)
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from .config import (
    MAX_UPLOAD_BYTES,
    TESTS_DIR,
    UPLOAD_CHUNK_BYTES,
    UPLOAD_DISK_RESERVE_BYTES,
    UPLOAD_MULTIPART_OVERHEAD_BYTES,
    UPLOAD_SNIFF_BYTES,
    UPLOAD_STALE_AGE_S,
)
from .ingest import ingest_csv
from .locks import catalog_write, drop_test_lock, test_write
from .provenance import MAX_UPLOADER_NAME_LENGTH, normalize_uploader_name
from .status import write_status
from .store import get_status, write_json_atomic
from .test_notes import Description, MAX_DESCRIPTION_LENGTH, normalize_test_text
from . import components
from .components import ComponentIds, ComponentSets


logger = logging.getLogger("kiha.uploads")
router = APIRouter(prefix="/api/uploads", tags=["uploads"])

_TEST_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
# Deliberately broad: malformed ids/indexes still reach form parsing before
# route validation, so they need the same pre-parser byte cap as valid paths.
_CHUNK_ROUTE_RE = re.compile(r"^/api/uploads/[^/]+/chunks/[^/]+$")
_UPLOAD_DIR = ".upload"
_MANIFEST = "manifest.json"
_STAGING = "raw.csv.uploading"
_COMMITS = "commits"
_COPY_BLOCK = 1024 * 1024
_MAX_UPLOAD_CHUNKS = 100_000


class UploadInit(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    source_file: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(gt=0)
    last_modified_ms: int = Field(ge=0)
    # Validated explicitly in _init_upload. Keeping this out of Pydantic's
    # numeric constraint path avoids FastAPI trying to echo an Infinity/NaN
    # input in a JSON validation error (which is itself non-serializable).
    fs_hz: float | None = None
    time_mode: Literal["auto", "column", "generated"] = "auto"
    time_column: str | None = Field(default=None, max_length=255)
    uploader_name: str | None = Field(
        default=None, max_length=MAX_UPLOADER_NAME_LENGTH)
    description: Description = ""
    components: ComponentIds = Field(default_factory=ComponentIds)
    component_sets: ComponentSets = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_component_payload(self):
        if "component_sets" in self.model_fields_set:
            if "components" in self.model_fields_set:
                raise ValueError("Send component_sets without legacy components.")
            # Validate structural uniqueness and unset columns before a name can
            # be reserved; registry checks remain deferred for resumable uploads.
            components.validate_sets(self.component_sets, references=False)
        return self

    @field_validator("uploader_name", mode="before")
    @classmethod
    def normalize_uploader_attribution(cls, value):
        if value is None or not isinstance(value, str):
            return value
        return normalize_uploader_name(value)


class _RequestBodyTooLarge(Exception):
    pass


class UploadBodyLimitMiddleware:
    """Bound chunk request bodies before Starlette parses multipart.

    Starlette's multipart ``max_part_size`` historically did not bound file
    parts.  This pure ASGI receive wrapper validates a declared Content-Length
    and also counts the real body, covering an absent or dishonest header.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (scope.get("type") != "http"
                or scope.get("method") != "PUT"
                or not _CHUNK_ROUTE_RE.fullmatch(scope.get("path", ""))):
            await self.app(scope, receive, send)
            return

        # Existing sessions retain the server-selected size recorded in their
        # manifest even if an administrator changes the default before a
        # restart.  Read only the tiny atomically-written manifest here; all
        # body bytes remain capped before multipart parsing.
        limit = await run_in_threadpool(_chunk_request_limit, scope)
        headers = {
            key.lower(): value
            for key, value in scope.get("headers", [])
        }
        raw_length = headers.get(b"content-length")
        if raw_length is not None:
            try:
                declared = int(raw_length)
            except ValueError:
                await JSONResponse(
                    {"detail": "invalid Content-Length"},
                    status_code=400,
                )(scope, receive, send)
                return
            if declared < 0:
                await JSONResponse(
                    {"detail": "invalid Content-Length"},
                    status_code=400,
                )(scope, receive, send)
                return
            if declared > limit:
                await JSONResponse(
                    {"detail": "multipart chunk request is too large"},
                    status_code=413,
                )(scope, receive, send)
                return

        received = 0
        response_started = False

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _RequestBodyTooLarge
            return message

        async def tracked_send(message):
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except _RequestBodyTooLarge:
            if response_started:
                raise
            await JSONResponse(
                {"detail": "multipart chunk request is too large"},
                status_code=413,
            )(scope, receive, send)


def _chunk_request_limit(scope) -> int:
    fallback = UPLOAD_CHUNK_BYTES + UPLOAD_MULTIPART_OVERHEAD_BYTES
    try:
        path = scope.get("path", "")
        upload_id = path.split("/")[3]
        query = parse_qs(
            scope.get("query_string", b"").decode("ascii", errors="strict"))
        names = query.get("name", [])
        if len(names) != 1:
            return fallback
        name = names[0]
        _validate_upload_id(upload_id)
        manifest = _load_manifest(name, upload_id)
        chunk_size = manifest["chunk_size"]
        if 0 < chunk_size <= 64 * 1024**2:
            return chunk_size + UPLOAD_MULTIPART_OVERHEAD_BYTES
    except (HTTPException, UploadStateError, OSError, UnicodeError,
            IndexError):
        pass
    return fallback


class UploadStateError(ValueError):
    """On-disk upload metadata is absent, malformed, or contradictory."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _validate_name(name: str) -> None:
    if (not _TEST_NAME_RE.fullmatch(name)
            or not re.search(r"[A-Za-z0-9]", name)):
        raise HTTPException(
            400,
            "test name may only contain letters, digits, '.', '_', '-'",
        )


def _validate_upload_id(upload_id: str) -> None:
    try:
        parsed = uuid.UUID(upload_id)
    except (ValueError, AttributeError):
        raise HTTPException(404, "upload session not found")
    if str(parsed) != upload_id.lower():
        raise HTTPException(404, "upload session not found")


def _test_dir(name: str) -> Path:
    _validate_name(name)
    root = TESTS_DIR.resolve()
    directory = (TESTS_DIR / name).resolve()
    if directory.parent != root:
        raise HTTPException(400, "invalid test name")
    return directory


def _upload_dir(name: str) -> Path:
    return _test_dir(name) / _UPLOAD_DIR


def _manifest_path(name: str) -> Path:
    return _upload_dir(name) / _MANIFEST


def _commit_path(name: str, index: int) -> Path:
    return _upload_dir(name) / _COMMITS / f"{index:08d}.json"


def _read_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return value if isinstance(value, dict) else None


def _validate_manifest(value: dict | None, name: str) -> dict:
    if value is None:
        raise UploadStateError("upload manifest is missing or unreadable")
    required = {
        "version": int,
        "upload_id": str,
        "name": str,
        "source_file": str,
        "size_bytes": int,
        "last_modified_ms": int,
        "chunk_size": int,
        "total_chunks": int,
        "state": str,
    }
    for key, kind in required.items():
        if not isinstance(value.get(key), kind):
            raise UploadStateError(f"upload manifest has invalid {key}")
    try:
        uuid.UUID(value["upload_id"])
    except (ValueError, AttributeError):
        raise UploadStateError("upload manifest has invalid upload_id")
    if value["name"] != name:
        raise UploadStateError("upload manifest name does not match directory")
    if (value["size_bytes"] <= 0
            or value["chunk_size"] <= 0
            or value["chunk_size"] > 64 * 1024**2):
        raise UploadStateError("upload manifest has invalid sizes")
    expected_chunks = math.ceil(
        value["size_bytes"] / value["chunk_size"])
    if value["total_chunks"] != expected_chunks:
        raise UploadStateError("upload manifest has invalid chunk count")
    if value["state"] not in {
        "receiving", "finalizing", "ingesting", "ready", "error"
    }:
        raise UploadStateError("upload manifest has invalid state")
    time_mode = value.get("time_mode", "auto")
    time_column = value.get("time_column")
    if time_mode not in {"auto", "column", "generated"}:
        raise UploadStateError("upload manifest has invalid time_mode")
    if (time_column is not None
            and (not isinstance(time_column, str)
                 or not time_column.strip()
                 or len(time_column) > 255)):
        raise UploadStateError("upload manifest has invalid time_column")
    if time_mode in {"column", "generated"} and not time_column:
        raise UploadStateError("upload manifest is missing time_column")
    if time_mode == "auto" and time_column is not None:
        raise UploadStateError(
            "upload manifest has time_column in automatic mode")
    uploader_name = value.get("uploader_name")
    if uploader_name is not None:
        if not isinstance(uploader_name, str):
            raise UploadStateError(
                "upload manifest has invalid uploader_name")
        try:
            normalized_uploader = normalize_uploader_name(uploader_name)
        except (TypeError, ValueError):
            raise UploadStateError(
                "upload manifest has invalid uploader_name")
        if normalized_uploader != uploader_name:
            raise UploadStateError(
                "upload manifest has non-canonical uploader_name")
    description = value.get("description", "")
    try:
        if (not isinstance(description, str)
                or len(description) > MAX_DESCRIPTION_LENGTH
                or normalize_test_text(description) != description):
            raise ValueError("invalid description")
    except ValueError:
        raise UploadStateError("upload manifest has invalid description")
    try:
        components.ids(value.get("components"))
        if "component_sets" in value:
            components.validate_sets(value["component_sets"], references=False)
    except (ValueError, TypeError, HTTPException):
        raise UploadStateError("upload manifest has invalid components")
    return value


def _load_manifest(name: str, upload_id: str | None = None) -> dict:
    manifest = _validate_manifest(
        _read_json(_manifest_path(name)), name)
    if upload_id is not None and manifest["upload_id"] != upload_id:
        raise HTTPException(404, "upload session not found")
    return manifest


def _expected_chunk(manifest: dict, index: int) -> tuple[int, int]:
    if index < 0 or index >= manifest["total_chunks"]:
        raise HTTPException(404, "chunk index is outside this upload")
    offset = index * manifest["chunk_size"]
    length = min(
        manifest["chunk_size"], manifest["size_bytes"] - offset)
    return offset, length


def _read_commits(manifest: dict) -> list[dict]:
    name = manifest["name"]
    directory = _upload_dir(name) / _COMMITS
    if not directory.exists():
        return []
    commits: dict[int, dict] = {}
    for path in directory.glob("*.json"):
        value = _read_json(path)
        if value is None:
            raise UploadStateError(
                f"chunk receipt '{path.name}' is unreadable")
        index = value.get("index")
        if not isinstance(index, int):
            raise UploadStateError("chunk receipt has invalid index")
        offset, size = _expected_chunk(manifest, index)
        if (value.get("offset") != offset
                or value.get("size") != size
                or not isinstance(value.get("sha256"), str)
                or not _SHA256_RE.fullmatch(value["sha256"])):
            raise UploadStateError(
                f"chunk receipt {index} contradicts the manifest")
        if index in commits:
            raise UploadStateError(f"duplicate chunk receipt {index}")
        commits[index] = {
            "index": index,
            "size": size,
            "sha256": value["sha256"].lower(),
        }
    return [commits[index] for index in sorted(commits)]


def _session_payload(manifest: dict) -> dict:
    chunks = _read_commits(manifest)
    return {
        "upload_id": manifest["upload_id"],
        "name": manifest["name"],
        "source_file": manifest["source_file"],
        "size_bytes": manifest["size_bytes"],
        "last_modified_ms": manifest["last_modified_ms"],
        "fs_hz": manifest.get("fs_hz"),
        "time_mode": manifest.get("time_mode", "auto"),
        "time_column": manifest.get("time_column"),
        "uploader_name": manifest.get("uploader_name"),
        "description": manifest.get("description", ""),
        "components": components.ids(manifest.get("components")),
        **({"component_sets": components.sets(manifest)} if "component_sets" in manifest else {}),
        "chunk_size": manifest["chunk_size"],
        "total_chunks": manifest["total_chunks"],
        "state": manifest["state"],
        "received_bytes": sum(chunk["size"] for chunk in chunks),
        "received_chunks": len(chunks),
        "chunks": chunks,
    }


def _publish_receiving(manifest: dict, payload: dict | None = None) -> None:
    payload = payload or _session_payload(manifest)
    write_status(
        _test_dir(manifest["name"]),
        "receiving",
        upload_id=manifest["upload_id"],
        source_file=manifest["source_file"],
        total_bytes=manifest["size_bytes"],
        received_bytes=payload["received_bytes"],
        total_chunks=manifest["total_chunks"],
        received_chunks=payload["received_chunks"],
        **_uploader_status_details(manifest),
    )


def _write_manifest(manifest: dict, *, touch: bool = True) -> None:
    if touch:
        manifest["updated_at"] = _now_iso()
        manifest["updated_at_epoch"] = time.time()
    write_json_atomic(_manifest_path(manifest["name"]), manifest)


def _fsync_dir(directory: Path) -> None:
    """Best-effort directory-entry durability on POSIX.

    Windows does not allow opening a directory this way; atomic replace still
    provides the expected runtime semantics there.
    """
    if os.name == "nt":
        return
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _identity_matches(manifest: dict, request: UploadInit) -> bool:
    canonical = "component_sets" in manifest
    if canonical != ("component_sets" in request.model_fields_set):
        return False
    same_components = (components.sets(manifest) == components.normalize_sets(request.component_sets)
                       if canonical else components.ids(manifest.get("components")) == request.components.model_dump(mode="json"))
    return (
        manifest["name"] == request.name
        and manifest["source_file"] == request.source_file
        and manifest["size_bytes"] == request.size_bytes
        and manifest["last_modified_ms"] == request.last_modified_ms
        and manifest.get("fs_hz") == request.fs_hz
        and manifest.get("time_mode", "auto") == request.time_mode
        and manifest.get("time_column") == request.time_column
        and manifest.get("uploader_name") == request.uploader_name
        and manifest.get("description", "") == request.description
        and same_components
    )


def _uploader_status_details(manifest: dict) -> dict:
    uploader_name = manifest.get("uploader_name")
    details = (
        {"uploader_name": uploader_name}
        if uploader_name is not None
        else {}
    )
    if manifest.get("description"):
        details["description"] = manifest["description"]
    if manifest.get("components"):
        details["components"] = manifest["components"]
    if "component_sets" in manifest:
        details["component_sets"] = components.sets(manifest)
        details["component_sets_revision"] = 0
    return details


def _remaining_upload_reservations(exclude: str | None = None) -> int:
    reserved = 0
    if not TESTS_DIR.exists():
        return 0
    for directory in TESTS_DIR.iterdir():
        if not directory.is_dir() or directory.name == exclude:
            continue
        try:
            manifest = _validate_manifest(
                _read_json(directory / _UPLOAD_DIR / _MANIFEST),
                directory.name,
            )
            if manifest["state"] != "receiving":
                continue
            committed = sum(
                chunk["size"] for chunk in _read_commits(manifest))
            reserved += max(0, manifest["size_bytes"] - committed)
        except (UploadStateError, HTTPException, OSError):
            continue
    return reserved


def _is_stale(manifest: dict) -> bool:
    updated = manifest.get("updated_at_epoch")
    return (
        isinstance(updated, (int, float))
        and time.time() - float(updated) > UPLOAD_STALE_AGE_S
    )


def _purge_stale_sessions_locked() -> list[str]:
    """Permanently remove old *incomplete* reservations under catalog_write."""
    removed: list[str] = []
    if not TESTS_DIR.exists():
        return removed
    for directory in list(TESTS_DIR.iterdir()):
        if not directory.is_dir():
            continue
        manifest_value = _read_json(directory / _UPLOAD_DIR / _MANIFEST)
        if manifest_value is None:
            continue
        try:
            manifest = _validate_manifest(manifest_value, directory.name)
        except UploadStateError:
            continue
        if (manifest["state"] != "receiving"
                or get_status(directory.name).get("status") != "receiving"
                or not _is_stale(manifest)):
            continue
        with test_write(directory.name):
            # Re-read after waiting for any active chunk commit.
            try:
                current = _load_manifest(directory.name)
            except (UploadStateError, HTTPException):
                continue
            if (current["state"] != "receiving"
                    or not _is_stale(current)):
                continue
            shutil.rmtree(directory)
            removed.append(directory.name)
            logger.info(
                "upload '%s': purged stale incomplete session",
                directory.name,
            )
        # The directory is gone and catalog_write prevents a replacement
        # until this sweep completes. Dropping now also covers callers that
        # subsequently return early or raise.
        drop_test_lock(directory.name)
    return removed


def _init_upload(request: UploadInit) -> tuple[dict, bool]:
    _validate_name(request.name)
    if "\x00" in request.source_file:
        raise HTTPException(400, "source filename contains a NUL byte")
    if (request.fs_hz is not None
            and (not math.isfinite(request.fs_hz) or request.fs_hz <= 0)):
        raise HTTPException(
            400, "fs_hz must be a finite number greater than zero")
    raw_time_column = request.time_column or ""
    if "\x00" in raw_time_column:
        raise HTTPException(400, "time_column contains a NUL byte")
    if request.time_mode == "column" and not raw_time_column.strip():
        raise HTTPException(
            400, "time_column is required when time_mode is 'column'")
    if request.time_mode == "auto" and raw_time_column.strip():
        raise HTTPException(
            400, "time_column is only used with 'column' or 'generated' mode")
    time_column = (
        raw_time_column
        if request.time_mode == "column"
        else raw_time_column.strip()
    )
    if request.time_mode == "generated" and not time_column:
        time_column = "time_s"
    request.time_column = time_column or None
    if request.size_bytes > MAX_UPLOAD_BYTES:
        raise HTTPException(
            413,
            f"upload exceeds the {MAX_UPLOAD_BYTES / 1024**3:.0f} GB limit",
        )
    total_chunks = math.ceil(request.size_bytes / UPLOAD_CHUNK_BYTES)
    if total_chunks > _MAX_UPLOAD_CHUNKS:
        raise HTTPException(
            413,
            f"upload would require {total_chunks} chunks; increase "
            "KIHA_UPLOAD_CHUNK_BYTES",
        )

    TESTS_DIR.mkdir(parents=True, exist_ok=True)
    with catalog_write():
        _purge_stale_sessions_locked()
        directory = _test_dir(request.name)
        with test_write(request.name):
            if directory.exists():
                try:
                    manifest = _load_manifest(request.name)
                except UploadStateError:
                    raise HTTPException(
                        409, f"test '{request.name}' already exists")
                if (manifest["state"] == "receiving"
                        and get_status(request.name).get("status")
                        == "receiving"
                        and _identity_matches(manifest, request)):
                    return _session_payload(manifest), False
                raise HTTPException(
                    409, f"test '{request.name}' already exists")

            # Only new identities need registry lookup. Existing valid sessions
            # can finish after a registry backup is temporarily unavailable.
            component_sets = (components.validate_sets(request.component_sets)
                              if "component_sets" in request.model_fields_set else None)
            if component_sets is None:
                component_ids = components.validate_references(request.components)
            else:
                component_ids = component_sets[0]["components"] if component_sets else components.ids()
            reserved = _remaining_upload_reservations()
            try:
                free = shutil.disk_usage(TESTS_DIR).free
            except OSError:
                free = request.size_bytes + UPLOAD_DISK_RESERVE_BYTES
            required = (
                request.size_bytes + reserved + UPLOAD_DISK_RESERVE_BYTES)
            if free < required:
                raise HTTPException(
                    507,
                    "not enough free disk space for this upload and "
                    "active reservations",
                )

            upload_id = str(uuid.uuid4())
            upload_directory = directory / _UPLOAD_DIR
            commits = upload_directory / _COMMITS
            commits.mkdir(parents=True)
            now = _now_iso()
            manifest = {
                "version": 1,
                "upload_id": upload_id,
                "name": request.name,
                "source_file": request.source_file,
                "size_bytes": request.size_bytes,
                "last_modified_ms": request.last_modified_ms,
                "fs_hz": request.fs_hz,
                "time_mode": request.time_mode,
                "time_column": request.time_column,
                "description": request.description,
                "components": component_ids,
                "chunk_size": UPLOAD_CHUNK_BYTES,
                "total_chunks": total_chunks,
                "state": "receiving",
                "created_at": now,
                "updated_at": now,
                "updated_at_epoch": time.time(),
            }
            if component_sets is not None:
                manifest["component_sets"] = component_sets
            if request.uploader_name is not None:
                manifest["uploader_name"] = request.uploader_name
            try:
                _write_manifest(manifest, touch=False)
                payload = _session_payload(manifest)
                _publish_receiving(manifest, payload)
                _fsync_dir(upload_directory)
                _fsync_dir(directory)
            except Exception:
                shutil.rmtree(directory, ignore_errors=True)
                raise
            logger.info(
                "upload '%s': session %s reserved for %.1f MB (%d chunks)",
                request.name,
                upload_id,
                request.size_bytes / 1e6,
                manifest["total_chunks"],
            )
    return payload, True


@router.post("")
def initiate_upload(request: UploadInit):
    payload, created = _init_upload(request)
    return JSONResponse(payload, status_code=201 if created else 200)


@router.get("/{upload_id}")
def get_upload(upload_id: str, name: str = Query(...)):
    _validate_upload_id(upload_id)
    directory = _test_dir(name)
    if not directory.is_dir():
        raise HTTPException(404, "upload session not found")
    with test_write(name):
        try:
            manifest = _load_manifest(name, upload_id)
            return _session_payload(manifest)
        except UploadStateError as exc:
            raise HTTPException(409, str(exc))


def _hash_part(file: BinaryIO, expected_size: int,
               sniff_binary: bool) -> str:
    try:
        file.seek(0)
    except (OSError, AttributeError) as exc:
        raise HTTPException(400, f"could not read uploaded chunk: {exc}")
    digest = hashlib.sha256()
    total = 0
    sniff = bytearray()
    while True:
        block = file.read(_COPY_BLOCK)
        if not block:
            break
        total += len(block)
        if total > expected_size:
            raise HTTPException(
                400, f"chunk must be exactly {expected_size} bytes")
        digest.update(block)
        if sniff_binary and len(sniff) < UPLOAD_SNIFF_BYTES:
            remaining = UPLOAD_SNIFF_BYTES - len(sniff)
            sniff.extend(block[:remaining])
    if total != expected_size:
        raise HTTPException(
            400, f"chunk must be exactly {expected_size} bytes")
    if sniff_binary and b"\x00" in sniff:
        raise HTTPException(
            400, "file does not look like a CSV (binary content detected)")
    return digest.hexdigest()


def _map_storage_error(exc: OSError) -> HTTPException:
    if exc.errno in {errno.ENOSPC, getattr(errno, "EDQUOT", -1)}:
        return HTTPException(
            507,
            "server storage is full; this upload remains resumable",
        )
    return HTTPException(500, f"could not store upload chunk: {exc}")


def _commit_chunk(name: str, upload_id: str, index: int,
                  file: BinaryIO, client_sha256: str) -> dict:
    if not _SHA256_RE.fullmatch(client_sha256):
        raise HTTPException(
            400, "X-Chunk-SHA256 must be a 64-character hex digest")
    client_sha256 = client_sha256.lower()
    with test_write(name):
        try:
            manifest = _load_manifest(name, upload_id)
        except UploadStateError as exc:
            raise HTTPException(409, str(exc))
        if (manifest["state"] != "receiving"
                or get_status(name).get("status") != "receiving"):
            raise HTTPException(
                409, f"upload is already {manifest['state']}")
        offset, expected_size = _expected_chunk(manifest, index)
        actual_sha256 = _hash_part(
            file, expected_size, sniff_binary=index == 0)
        if actual_sha256 != client_sha256:
            raise HTTPException(400, "chunk SHA-256 does not match its body")

        receipt_path = _commit_path(name, index)
        existing = _read_json(receipt_path)
        if existing is not None:
            if (existing.get("index") == index
                    and existing.get("offset") == offset
                    and existing.get("size") == expected_size
                    and str(existing.get("sha256", "")).lower()
                    == actual_sha256):
                # Besides being idempotent, an exact retry repairs a prior
                # crash/failure that landed the durable receipt but not the
                # advisory manifest timestamp or status progress.
                _write_manifest(manifest)
                payload = _session_payload(manifest)
                _publish_receiving(manifest, payload)
                return payload
            raise HTTPException(
                409, f"chunk {index} was already committed differently")
        if receipt_path.exists():
            raise HTTPException(409, f"chunk {index} receipt is unreadable")

        staging = _upload_dir(name) / _STAGING
        try:
            file.seek(0)
            mode = "r+b" if staging.exists() else "w+b"
            with open(staging, mode, buffering=0) as output:
                output.seek(offset)
                remaining = expected_size
                while remaining:
                    block = file.read(min(_COPY_BLOCK, remaining))
                    if not block:
                        raise OSError(
                            errno.EIO, "chunk spool ended during commit")
                    written = output.write(block)
                    if written != len(block):
                        raise OSError(errno.EIO, "short staging-file write")
                    remaining -= written
                output.flush()
                os.fsync(output.fileno())
            write_json_atomic(receipt_path, {
                "index": index,
                "offset": offset,
                "size": expected_size,
                "sha256": actual_sha256,
            })
            _fsync_dir(receipt_path.parent)
            _write_manifest(manifest)
            payload = _session_payload(manifest)
            _publish_receiving(manifest, payload)
        except HTTPException:
            raise
        except OSError as exc:
            logger.exception(
                "upload '%s': chunk %d commit failed", name, index)
            raise _map_storage_error(exc)

        logger.info(
            "upload '%s': committed chunk %d/%d (%.1f/%.1f MB)",
            name,
            index + 1,
            manifest["total_chunks"],
            payload["received_bytes"] / 1e6,
            manifest["size_bytes"] / 1e6,
        )
        return payload


@router.put("/{upload_id}/chunks/{index}")
async def put_upload_chunk(
    request: Request,
    upload_id: str,
    index: int,
    name: str = Query(...),
    x_chunk_sha256: str = Header(..., alias="X-Chunk-SHA256"),
):
    _validate_upload_id(upload_id)
    _test_dir(name)
    try:
        limit_manifest = await run_in_threadpool(
            _load_manifest, name, upload_id)
    except UploadStateError as exc:
        raise HTTPException(409, str(exc))
    try:
        form = await request.form(
            max_files=1,
            max_fields=0,
            max_part_size=(
                limit_manifest["chunk_size"]
                + UPLOAD_MULTIPART_OVERHEAD_BYTES),
        )
    except _RequestBodyTooLarge:
        # Let the outer ASGI byte-limit middleware produce the intended 413.
        raise
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"invalid multipart chunk: {exc}")
    try:
        items = list(form.multi_items())
        if (len(items) != 1 or items[0][0] != "file"
                or not isinstance(items[0][1], UploadFile)):
            raise HTTPException(
                400, "multipart body must contain exactly one 'file' part")
        upload = items[0][1]
        return await run_in_threadpool(
            _commit_chunk,
            name,
            upload_id,
            index,
            upload.file,
            x_chunk_sha256,
        )
    finally:
        await form.close()


def _all_chunks_present(manifest: dict) -> list[dict]:
    chunks = _read_commits(manifest)
    if len(chunks) != manifest["total_chunks"]:
        missing = sorted(
            set(range(manifest["total_chunks"]))
            - {chunk["index"] for chunk in chunks})
        preview = ", ".join(str(index) for index in missing[:10])
        suffix = "..." if len(missing) > 10 else ""
        raise HTTPException(
            409, f"upload is incomplete; missing chunks {preview}{suffix}")
    return chunks


def _verify_assembled_file(path: Path, manifest: dict,
                           chunks: list[dict]) -> None:
    """Re-hash every committed range before raw.csv becomes authoritative.

    Receipts prove what was fsynced at commit time.  This final sequential read
    also catches a later truncation, sparse re-extension, manual replacement,
    or storage corruption instead of publishing a file whose bytes no longer
    match those receipts.
    """
    if not path.is_file():
        raise UploadStateError("assembled CSV data is missing")
    if path.stat().st_size != manifest["size_bytes"]:
        raise UploadStateError(
            "assembled CSV size contradicts the upload manifest")
    try:
        with open(path, "rb", buffering=0) as handle:
            for chunk in chunks:
                offset, expected_size = _expected_chunk(
                    manifest, chunk["index"])
                handle.seek(offset)
                digest = hashlib.sha256()
                remaining = expected_size
                while remaining:
                    block = handle.read(min(_COPY_BLOCK, remaining))
                    if not block:
                        raise UploadStateError(
                            f"assembled CSV is short at chunk "
                            f"{chunk['index']}")
                    digest.update(block)
                    remaining -= len(block)
                if digest.hexdigest() != chunk["sha256"]:
                    raise UploadStateError(
                        f"assembled CSV checksum failed at chunk "
                        f"{chunk['index']}")
    except UploadStateError:
        raise
    except OSError as exc:
        raise _map_storage_error(exc)


def _finalize_locked(manifest: dict) -> None:
    """Publish immutable raw.csv; caller holds test_write(name)."""
    name = manifest["name"]
    chunks = _all_chunks_present(manifest)
    upload_directory = _upload_dir(name)
    staging = upload_directory / _STAGING
    raw_path = _test_dir(name) / "raw.csv"

    manifest["state"] = "finalizing"
    _write_manifest(manifest)
    try:
        if not raw_path.exists():
            if not staging.is_file():
                raise UploadStateError(
                    "all chunk receipts exist but staging data is missing")
            with open(staging, "r+b", buffering=0) as output:
                if output.seek(0, os.SEEK_END) < manifest["size_bytes"]:
                    raise UploadStateError(
                        "staging data is shorter than the manifest")
                output.truncate(manifest["size_bytes"])
                output.flush()
                os.fsync(output.fileno())
            _verify_assembled_file(staging, manifest, chunks)
            os.replace(staging, raw_path)
            _fsync_dir(upload_directory)
            _fsync_dir(raw_path.parent)
        elif staging.exists():
            # A crash can leave both only if a manual copy was introduced.
            # Never guess which one is authoritative.
            raise UploadStateError(
                "both final and staging CSV files exist")
        elif raw_path.stat().st_size != manifest["size_bytes"]:
            raise UploadStateError(
                "final CSV size contradicts the upload manifest")
        else:
            _verify_assembled_file(raw_path, manifest, chunks)
    except OSError as exc:
        raise _map_storage_error(exc)

    manifest["state"] = "ingesting"
    manifest["assembled_at"] = _now_iso()
    _write_manifest(manifest)
    write_status(
        _test_dir(name),
        "ingesting",
        upload_id=manifest["upload_id"],
        source_file=manifest["source_file"],
        total_bytes=manifest["size_bytes"],
        received_bytes=manifest["size_bytes"],
        total_chunks=manifest["total_chunks"],
        received_chunks=manifest["total_chunks"],
        **_uploader_status_details(manifest),
    )


def ingest_completed_upload(name: str, upload_id: str) -> None:
    """Run the existing CSV ingest and mirror its result into the manifest."""
    try:
        with test_write(name):
            manifest = _load_manifest(name, upload_id)
            if manifest["state"] == "ready":
                return
            if manifest["state"] != "ingesting":
                raise UploadStateError(
                    f"cannot ingest upload in state {manifest['state']}")
            source_file = manifest["source_file"]
            fs_hz = manifest.get("fs_hz")
            time_mode = manifest.get("time_mode", "auto")
            time_column = manifest.get("time_column")
            uploader_name = manifest.get("uploader_name")
            description = manifest.get("description", "")
            component_ids = components.ids(manifest.get("components"))
            component_sets = components.sets(manifest) if "component_sets" in manifest else None
            raw_path = _test_dir(name) / "raw.csv"
        ingest_csv(
            raw_path,
            name,
            source_name=source_file,
            assume_fs=fs_hz,
            time_mode=time_mode,
            time_column=time_column,
            uploader_name=uploader_name,
            description=description,
            component_ids=component_ids if component_sets is None else None,
            component_sets=component_sets,
        )
    except Exception as exc:
        logger.exception("upload '%s': ingestion failed", name)
        detail = (
            f"ingestion failed: {exc!r}; delete this test and upload it again"
        )
        try:
            with test_write(name):
                manifest = _load_manifest(name, upload_id)
                manifest["state"] = "error"
                manifest["error"] = detail
                try:
                    _write_manifest(manifest)
                except Exception:
                    logger.exception(
                        "upload '%s': could not publish manifest error state",
                        name,
                    )
                # ingest_csv normally writes status=error before raising. If
                # it failed before/during that publication, repair the
                # lifecycle document under the same verified lock. Keeping
                # this in one critical section prevents a concurrent
                # delete/rename from moving the test and this writer from
                # recreating a ghost directory under the old name.
                try:
                    current_status = get_status(name)
                    write_status(
                        _test_dir(name),
                        "error",
                        current_status.get("error") or detail,
                        **_uploader_status_details(manifest),
                    )
                except Exception:
                    logger.exception(
                        "upload '%s': could not publish status error state",
                        name,
                    )
        except Exception:
            logger.exception(
                "upload '%s': failed session disappeared before error "
                "publication",
                name,
            )
        return

    try:
        with test_write(name):
            manifest = _load_manifest(name, upload_id)
            manifest["state"] = "ready"
            _write_manifest(manifest)
    except Exception:
        # Ingest already published ready status/meta atomically; an audit
        # manifest update failure must not make valid data unavailable.
        logger.exception(
            "upload '%s': ready, but manifest state update failed", name)


def _complete_upload(name: str, upload_id: str) -> tuple[dict, bool]:
    with test_write(name):
        try:
            manifest = _load_manifest(name, upload_id)
        except UploadStateError as exc:
            raise HTTPException(409, str(exc))
        if manifest["state"] == "ready":
            return {"name": name, "status": "ready"}, False
        if manifest["state"] == "ingesting":
            status = get_status(name).get("status")
            if status == "ready":
                # ingest_csv made the data authoritative but its wrapper did
                # not get to update the audit manifest.
                manifest["state"] = "ready"
                _write_manifest(manifest)
                return {"name": name, "status": "ready"}, False
            if status == "error":
                # Do not turn an ingest failure into an implicit retry merely
                # because the wrapper crashed before mirroring the error.
                manifest["state"] = "error"
                _write_manifest(manifest)
                raise HTTPException(
                    409, "upload ingestion failed; delete and upload again")
            if status != "ingesting":
                # _finalize_locked persists manifest=ingesting before status.
                # If that status write fails, a client retry must repair the
                # publication and actually schedule the otherwise-lost job.
                if not (_test_dir(name) / "raw.csv").is_file():
                    raise HTTPException(
                        409, "final CSV is missing before ingestion")
                _publish_ingesting(manifest)
                return {"name": name, "status": "ingesting"}, True
            return {"name": name, "status": "ingesting"}, False
        if manifest["state"] == "error":
            # Integrity failure is persisted to the manifest before status.
            # Repair a transient status-write failure so normal Delete is not
            # blocked forever by a stale "receiving" lifecycle document.
            if get_status(name).get("status") != "error":
                write_status(
                    _test_dir(name),
                    "error",
                    manifest.get("error")
                    or "upload failed; delete this test and upload it again",
                    **_uploader_status_details(manifest),
                )
            raise HTTPException(
                409, "upload failed; delete this test and upload it again")
        if manifest["state"] not in {"receiving", "finalizing"}:
            raise HTTPException(
                409, f"upload cannot complete from {manifest['state']}")
        try:
            _finalize_locked(manifest)
        except UploadStateError as exc:
            detail = (
                f"upload integrity check failed: {exc}; delete this test "
                "and upload it again"
            )
            manifest["state"] = "error"
            manifest["error"] = detail
            _write_manifest(manifest)
            write_status(
                _test_dir(name),
                "error",
                detail,
                **_uploader_status_details(manifest),
            )
            raise HTTPException(409, str(exc))
        return {"name": name, "status": "ingesting"}, True


@router.post("/{upload_id}/complete")
def complete_upload(background: BackgroundTasks, upload_id: str,
                    name: str = Query(...)):
    _validate_upload_id(upload_id)
    _test_dir(name)
    payload, schedule = _complete_upload(name, upload_id)
    if schedule:
        background.add_task(ingest_completed_upload, name, upload_id)
    return payload


@router.delete("/{upload_id}")
def cancel_upload(upload_id: str, name: str = Query(...)):
    _validate_upload_id(upload_id)
    directory = _test_dir(name)
    if not directory.is_dir():
        raise HTTPException(404, "upload session not found")
    # Keep catalog_write held until the now-unused per-test lock is removed.
    # Otherwise a concurrent init can create a replacement session/lock in the
    # gap and this request could accidentally evict that live lock.
    with catalog_write():
        with test_write(name):
            try:
                manifest = _load_manifest(name, upload_id)
            except UploadStateError as exc:
                raise HTTPException(409, str(exc))
            status = get_status(name).get("status")
            cancelable = (
                (manifest["state"] == "receiving" and status == "receiving")
                or (
                    manifest["state"] == "error"
                    and status in {"error", "receiving", "missing"}
                )
            )
            if not cancelable:
                raise HTTPException(
                    409, f"cannot cancel upload while it is {status}")
            try:
                shutil.rmtree(directory)
            except OSError as exc:
                raise _map_storage_error(exc)
        drop_test_lock(name)
    logger.info("upload '%s': canceled", name)
    return {"ok": True, "canceled": name}


def _mark_recovery_error(directory: Path, detail: str,
                         manifest: dict | None = None) -> None:
    write_status(
        directory,
        "error",
        f"upload/ingestion was interrupted by a backend restart; {detail}",
        **(_uploader_status_details(manifest) if manifest is not None else {}),
    )


def _publish_ingesting(manifest: dict) -> None:
    write_status(
        _test_dir(manifest["name"]),
        "ingesting",
        upload_id=manifest["upload_id"],
        source_file=manifest["source_file"],
        total_bytes=manifest["size_bytes"],
        received_bytes=manifest["size_bytes"],
        total_chunks=manifest["total_chunks"],
        received_chunks=manifest["total_chunks"],
        **_uploader_status_details(manifest),
    )


def recover_uploads() -> list[tuple[str, str]]:
    """Repair upload lifecycle after a process restart.

    Valid receiving sessions are intentionally untouched and remain resumable.
    Returned ``(name, upload_id)`` jobs have an immutable raw.csv and should be
    dispatched to :func:`ingest_completed_upload` by the app lifespan.
    """
    jobs: list[tuple[str, str]] = []
    if not TESTS_DIR.exists():
        return jobs

    with catalog_write():
        _purge_stale_sessions_locked()
        for directory in list(TESTS_DIR.iterdir()):
            if not directory.is_dir():
                continue
            name = directory.name
            status = get_status(name).get("status")
            upload_directory = directory / _UPLOAD_DIR
            has_upload_state = upload_directory.is_dir()
            # Ordinary ready/error/legacy directories without .upload are not
            # part of this subsystem.  A .upload directory is inspected even
            # when status.json is absent: init writes the manifest first, so a
            # power loss in that tiny window must not reserve the name forever.
            if (not has_upload_state
                    and status not in {"receiving", "ingesting"}):
                continue
            if status == "rebuilding":
                continue
            with test_write(name):
                manifest = None
                manifest_value = _read_json(
                    upload_directory / _MANIFEST)
                if manifest_value is None:
                    _mark_recovery_error(
                        directory,
                        "no resumable manifest exists; delete this test and "
                        "upload it again",
                    )
                    continue
                try:
                    manifest = _validate_manifest(manifest_value, name)
                    # Atomic JSON writers may leave hidden temp files only if
                    # the process was killed before replace. They are never
                    # authoritative.
                    for tmp in upload_directory.glob("*.tmp"):
                        try:
                            tmp.unlink()
                        except OSError:
                            pass

                    # Ingestion records status=error before its wrapper can
                    # update the audit manifest. A crash in that narrow window
                    # must not leave contradictory "ingesting" state or cause
                    # recovery to retry a failed ingest.
                    if status == "error":
                        if manifest["state"] != "error":
                            manifest["state"] = "error"
                            _write_manifest(manifest)
                        continue

                    # ingest_csv publishes ready before its wrapper updates the
                    # audit manifest. Reconcile that crash window instead of
                    # leaving GET/complete stuck at "ingesting" forever.
                    if status == "ready":
                        if (not (directory / "raw.csv").is_file()
                                or not (directory / "meta.json").is_file()):
                            raise UploadStateError(
                                "ready status is missing final data")
                        if manifest["state"] in {"ingesting", "ready"}:
                            if manifest["state"] != "ready":
                                manifest["state"] = "ready"
                                _write_manifest(manifest)
                            continue
                        raise UploadStateError(
                            "ready status contradicts manifest state")

                    if status in {"missing", "unknown", None}:
                        if manifest["state"] == "receiving":
                            _publish_receiving(
                                manifest, _session_payload(manifest))
                            continue
                        if manifest["state"] == "finalizing":
                            _finalize_locked(manifest)
                            jobs.append((name, manifest["upload_id"]))
                            continue
                        if (manifest["state"] == "ingesting"
                                and (directory / "raw.csv").is_file()):
                            _publish_ingesting(manifest)
                            jobs.append((name, manifest["upload_id"]))
                            continue
                        if (manifest["state"] == "ready"
                                and (directory / "raw.csv").is_file()
                                and (directory / "meta.json").is_file()):
                            write_status(
                                directory,
                                "ready",
                                **_uploader_status_details(manifest),
                            )
                            continue
                        raise UploadStateError(
                            "manifest cannot reconstruct missing status")

                    if status == "receiving":
                        if manifest["state"] == "receiving":
                            # Preserve the exact manifest bytes; restart is not
                            # itself upload activity and must not reset its TTL.
                            _publish_receiving(
                                manifest, _session_payload(manifest))
                            continue
                        if manifest["state"] == "finalizing":
                            _finalize_locked(manifest)
                            jobs.append((name, manifest["upload_id"]))
                            continue
                        if (manifest["state"] == "ingesting"
                                and (directory / "raw.csv").is_file()):
                            _publish_ingesting(manifest)
                            jobs.append((name, manifest["upload_id"]))
                            continue
                        if (manifest["state"] == "ready"
                                and (directory / "raw.csv").is_file()
                                and (directory / "meta.json").is_file()):
                            write_status(
                                directory,
                                "ready",
                                **_uploader_status_details(manifest),
                            )
                            continue
                        raise UploadStateError(
                            "receiving status contradicts manifest state")

                    # status == ingesting: immutable raw.csv must exist. A
                    # restart after rename but before manifest replace may
                    # leave state finalizing; rerunning finalize is safe.
                    if manifest["state"] == "finalizing":
                        _finalize_locked(manifest)
                    elif (manifest["state"] == "ready"
                          and (directory / "raw.csv").is_file()
                          and (directory / "meta.json").is_file()):
                        write_status(
                            directory,
                            "ready",
                            **_uploader_status_details(manifest),
                        )
                        continue
                    elif manifest["state"] != "ingesting":
                        raise UploadStateError(
                            "ingesting status contradicts manifest state")
                    if not (directory / "raw.csv").is_file():
                        raise UploadStateError(
                            "final CSV is missing during ingestion recovery")
                    jobs.append((name, manifest["upload_id"]))
                except (UploadStateError, HTTPException, OSError) as exc:
                    logger.exception(
                        "upload '%s': recovery found inconsistent state", name)
                    try:
                        if manifest is not None and manifest.get("name") == name:
                            manifest["state"] = "error"
                            _write_manifest(manifest)
                    except Exception:
                        logger.exception(
                            "upload '%s': could not mark manifest error", name)
                    _mark_recovery_error(
                        directory,
                        f"{exc}; delete this test and upload it again",
                        manifest,
                    )
    return jobs
