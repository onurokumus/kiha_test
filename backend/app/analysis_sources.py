"""Durable dataset identity and lightweight session compatibility snapshots.

Identity is separate from meta so ingestion/rebuild cannot replace it. Migration
adds one small file, only when an analysis client requests this catalog. UUIDs
travel with the complete test directory through rename/trash/restore. Revisions
are conservative local change tokens, not content hashes or immutable history.
"""
import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4
from fastapi import HTTPException

from . import store
from .locks import catalog_write, test_write

IDENTITY_FILE = 'source_identity.json'


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def read_identity(directory: Path) -> str | None:
    path = directory / IDENTITY_FILE
    if path.is_symlink() or path.is_junction():
        raise ValueError('Dataset identity is a link; automatic recovery is unavailable.')
    try:
        doc = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    if not isinstance(doc, dict) or doc.get('version') != 1 or not isinstance(doc.get('id'), str):
        raise ValueError('Dataset identity is invalid; automatic recovery is unavailable.')
    return str(UUID(doc['id']))


def _snapshot(directory: Path, status: str) -> dict:
    identity = read_identity(directory)
    if status != 'ready':
        return {'id': identity}
    for relative in ('meta.json', 'testpoints.json'):
        path = directory / relative
        if path.is_symlink() or path.is_junction():
            raise ValueError('Source metadata is a link; automatic recovery is unavailable.')
    meta = store.get_meta(directory.name)
    if not isinstance(meta, dict):
        raise ValueError('Test metadata is unavailable.')
    if identity is None:
        identity = str(uuid4())
        store.write_json_atomic(directory / IDENTITY_FILE, {'version': 1, 'id': identity})
    data = directory / 'data.parquet'
    if data.is_symlink() or data.is_junction():
        raise ValueError('Sample data is a link; automatic recovery is unavailable.')
    stat = data.stat()
    # Names, notes, components and derived caches do not change sample identity.
    revision = _hash([stat.st_size, stat.st_mtime_ns, {key: meta.get(key) for key in
        ('columns', 'time_column', 'fs_hz', 'n_rows', 't_start', 'duration_s', 'derived_variables')}])
    points = store.read_testpoints(directory.name)['test_points']
    point_refs = []
    for point in points:
        i0, i1 = store._testpoint_bounds(meta, points, point)
        point_refs.append({'id': point['id'], 'revision': _hash([
            i0, i1, point.get('start_s'), point.get('end_s')])})
    return {'id': identity, 'revision': revision,
            'columns': [c for c in meta['columns'] if c != meta.get('time_column')],
            'test_points': point_refs}


def verify_reference(name: str, expected_id: UUID | None) -> None:
    """Called under the endpoint's existing test lock. No migration on reads."""
    if expected_id is None:
        return
    try:
        identity = read_identity(store.TESTS_DIR / name)
    except (OSError, ValueError, KeyError, TypeError):
        identity = None
    if identity != str(expected_id):
        raise HTTPException(409, 'Dataset identity changed. Reload to review session recovery before analyzing this test.')


def catalog() -> dict:
    entries = []
    with catalog_write():
        root = store.TESTS_DIR.resolve()
        for info in store.list_tests():
            name = info['name']
            entry = {'name': name, 'status': info['status']}
            directory = store.TESTS_DIR / name
            try:
                if directory.is_symlink() or directory.is_junction() or directory.resolve().parent != root:
                    raise ValueError('Test directory is a link; automatic recovery is unavailable.')
                # Busy ingestion may hold its writer for minutes. Do not queue
                # a migration behind it. Existing identity alone is enough to
                # retain a pending reference; no revision is asserted meanwhile.
                if info['status'] != 'ready':
                    entry.update(id=read_identity(directory))
                else:
                    with test_write(name):
                        entry['status'] = store.get_status(name).get('status')
                        entry.update(_snapshot(directory, entry['status']))
            except (OSError, ValueError, KeyError, TypeError, OverflowError):
                # Never replace a damaged identity or bless a partial snapshot.
                entry.update(id=None, error='Source identity or metadata could not be verified. Check this test and retry recovery.')
            entries.append(entry)
    # External folder copies can duplicate UUIDs. Refuse ambiguous matches;
    # never silently choose the first directory or rewrite either copy.
    counts = {}
    for entry in entries:
        if entry.get('id'):
            counts[entry['id']] = counts.get(entry['id'], 0) + 1
    for entry in entries:
        if counts.get(entry.get('id'), 0) > 1:
            entry.update(error='Duplicate dataset identity; automatic recovery is ambiguous.')
    return {'version': 1, 'sources': entries}
