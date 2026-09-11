"""Trash entry identities are independent of active test folder names.

All operations require catalog_write (including legacy-folder migration). No
native read slot is acquired. Active source/destination test locks belong to the
caller. Each entry wraps the untouched test directory in <UUID>/data; entry.json
is outside the data so lifecycle bookkeeping cannot change scientific files.
"""
import json
import logging
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import HTTPException

from . import store
from .paths import is_link_or_junction

ENTRY_FILE = "entry.json"
NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
logger = logging.getLogger("kiha.trash")


def validate_name(name: str) -> None:
    if (not isinstance(name, str) or not 1 <= len(name) <= 200
            or not NAME_RE.fullmatch(name) or not re.search(r"[A-Za-z0-9]", name)
            or name.endswith('.') or re.fullmatch(r"(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", name, re.I)):
        raise HTTPException(400, "Use 1–200 letters, digits, '.', '_' or '-' for the test name; no reserved names or trailing dots.")


def _child(root: Path, name: str) -> Path:
    path = root / name
    if is_link_or_junction(path) or path.resolve().parent != root.resolve():
        raise HTTPException(409, "Unsafe trash path; no files were changed.")
    return path


def entry_path(root: Path, entry_id: str) -> Path:
    try:
        canonical = str(UUID(entry_id))
    except (ValueError, AttributeError):
        raise HTTPException(400, "Invalid trash ID") from None
    return _child(root, canonical)


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def move_to_trash(root: Path, source: Path, name: str, *, legacy=False) -> dict:
    # Callers have checked the source against the active/legacy root, including
    # resolved containment. Never overwrite an existing entry, even on UUID clash.
    root.mkdir(parents=True, exist_ok=True)
    entry_id = str(uuid4())
    folder = _child(root, entry_id)
    deleted_at = (datetime.fromtimestamp(source.stat().st_mtime, timezone.utc)
                  if legacy else datetime.now(timezone.utc)).isoformat()
    record = {"version": 1, "id": entry_id, "name": name,
              "deleted_at": deleted_at, "legacy_time_estimated": legacy,
              "state": "stored"}
    folder.mkdir()
    try:
        store.write_json_atomic(folder / ENTRY_FILE, record)
        source.rename(folder / "data")
    except OSError:
        # Only remove our bookkeeping if the atomic source move never happened.
        if not (folder / "data").exists():
            try:
                (folder / ENTRY_FILE).unlink(missing_ok=True)
                folder.rmdir()
            except OSError:
                pass
        raise
    return record


def migrate_legacy(root: Path) -> None:
    if not root.exists():
        return
    for path in list(root.iterdir()):
        if not path.is_dir():
            continue
        _child(root, path.name)
        # New wrappers always publish the entry before moving data. A leftover
        # empty wrapper after a crash is visible as unavailable, never auto-erased.
        try:
            is_uuid = str(UUID(path.name)) == path.name
        except ValueError:
            is_uuid = False
        if (path / ENTRY_FILE).exists() or (is_uuid and ((path / 'data').is_dir() or not any(path.iterdir()))):
            continue
        move_to_trash(root, path, path.name, legacy=True)


def get_entry(root: Path, entry_id: str) -> tuple[Path, dict]:
    folder = entry_path(root, entry_id)
    if not folder.is_dir():
        raise HTTPException(404, "This trash entry no longer exists. Reload the trash list.")
    record = _read(folder / ENTRY_FILE)
    if (record.get("version") != 1 or record.get("id") != folder.name
            or not isinstance(record.get("name"), str)
            or record.get("state") not in ("stored", "deleting")
            or not (isinstance(record.get("deleted_at"), str)
                    or record.get("state") == "deleting" and record.get("deleted_at") is None)):
        record = {"id": folder.name, "name": folder.name, "deleted_at": None,
                  "state": "unavailable", "error": "Trash metadata is invalid; restore is unavailable."}
    _child(folder, "data")
    return folder, record


def list_entries(root: Path) -> list[dict]:
    migrate_legacy(root)
    if not root.exists():
        return []
    items = []
    for folder in root.iterdir():
        if not folder.is_dir():
            continue
        folder, record = get_entry(root, folder.name)
        data = folder / "data"
        if record["state"] == "stored" and not data.exists():
            continue  # Empty prepare/committed-restore bookkeeping, no dataset.
        meta, status = _read(data / "meta.json"), _read(data / "status.json")
        items.append({**record, "restorable": record["state"] == "stored" and data.is_dir(),
                      "status": status.get("status", "unknown"),
                      "description": meta.get("description", status.get("description", "")),
                      "source_file": meta.get("source_file", status.get("source_file")),
                      "uploader_name": meta.get("uploader_name", status.get("uploader_name")),
                      "components": meta.get("components", status.get("components")),
                      "duration_s": meta.get("duration_s")})
    return sorted(items, key=lambda item: (item.get("deleted_at") or "", item["id"]), reverse=True)


def restore(root: Path, tests_root: Path, entry_id: str, name: str) -> dict:
    validate_name(name)
    folder, record = get_entry(root, entry_id)
    source = _child(folder, "data")
    if record["state"] != "stored" or not source.is_dir():
        raise HTTPException(409, "This entry cannot be restored. A permanent deletion may be incomplete.")
    destination = _child(tests_root, name)
    if destination.exists():
        raise HTTPException(409, f"Test '{name}' already exists. Enter a different restore name; neither test was changed.")
    # Reuse the name-bearing document contract from normal rename. Stage all
    # parsing first; malformed documents must never be replaced with empty JSON.
    documents = []
    for relative, key in (("meta.json", "name"), ("testpoints.json", "test"),
                          (".upload/manifest.json", "name")):
        path = source / relative
        if is_link_or_junction(path) or is_link_or_junction(path.parent) or not path.resolve().is_relative_to(source.resolve()):
            raise HTTPException(409, "Cannot restore linked metadata. The trash copy is retained.")
        try:
            original = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        except ValueError:
            raise HTTPException(409, f"Cannot restore: {relative} is invalid. The trash copy is retained.") from None
        if not isinstance(original, dict):
            raise HTTPException(409, f"Cannot restore: {relative} must be a JSON object.")
        if original.get(key) != name:
            documents.append((path, original, {**original, key: name}))
    try:
        for path, _, updated in documents:
            store.write_json_atomic(path, updated)
        source.rename(destination)
    except OSError:
        logger.warning("Restore failed for trash entry %s", entry_id, exc_info=True)
        for path, original, _ in documents:
            try:
                store.write_json_atomic(path, original)
            except OSError:
                pass  # Retry parses and rewrites every name-bearing document.
        raise HTTPException(409, "Restore failed; the trash copy is retained. Check available disk space and close any external file viewers, then retry.") from None
    # The directory rename is the commit. Failure to remove empty bookkeeping
    # must not turn a successful restore into a misleading failure/retry.
    try:
        (folder / ENTRY_FILE).unlink()
        folder.rmdir()
    except OSError:
        pass
    store._size_cache.pop(name, None)
    return {"ok": True, "restored": name, "trash_id": entry_id}


def delete_permanently(root: Path, entry_id: str) -> None:
    folder, record = get_entry(root, entry_id)
    record = {**record, "version": 1, "state": "deleting"}
    # Durable first: a partially removed dataset must never be offered to restore.
    store.write_json_atomic(folder / ENTRY_FILE, record)
    try:
        for child in list(folder.iterdir()):
            if child.name == ENTRY_FILE:
                continue
            target = _child(folder, child.name)  # Verify before recursive deletion.
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        (folder / ENTRY_FILE).unlink()
        folder.rmdir()
    except OSError:
        logger.warning("Permanent deletion incomplete for trash entry %s", entry_id, exc_info=True)
        if folder.exists():
            store.write_json_atomic(folder / ENTRY_FILE, record)
        raise HTTPException(409, "Permanent deletion incomplete. Some files could not be removed; close any external file viewers and retry deletion.") from None


def delete_batch(root: Path, entry_ids: list[str]) -> dict:
    # Operate on the confirmed snapshot only: tests deleted by another client
    # while the confirmation is open must not be swept into Delete all.
    deleted, failures = [], []
    for entry_id in dict.fromkeys(entry_ids):
        try:
            delete_permanently(root, entry_id)
            deleted.append(entry_id)
        except HTTPException as error:
            if error.status_code == 404:
                deleted.append(entry_id)  # A replay after a lost response is safe.
            else:
                failures.append({"id": entry_id, "error": str(error.detail)})
        except OSError:
            logger.warning("Could not delete trash entry %s", entry_id, exc_info=True)
            failures.append({"id": entry_id, "error": "Could not finish deleting this entry. Check disk access and retry."})
    return {"deleted_ids": deleted, "failures": failures}
