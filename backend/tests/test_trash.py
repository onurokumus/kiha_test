"""Real trash lifecycle, repeated names, failure isolation and recovery."""
import hashlib
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from app import components, main, store, trash, uploads
from app.locks import test_read as hold_read
from ._base import DataDirTestCase
from . import test_test_notes as notes_fixture


def file_hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


class TrashTests(DataDirTestCase):
    def setUp(self):
        super().setUp()
        self.client = TestClient(main.app, raise_server_exceptions=False)
        self.component = self.client.post('/api/components', json={'kind': 'motor', 'name': 'Motor 1'}).json()
        self.body = ('time,signal\n' + ''.join(f'{i / 10},{i}\n' for i in range(40))).encode()
        self.init = {'name': 'alpha', 'source_file': 'source.csv', 'size_bytes': len(self.body), 'last_modified_ms': 1,
                     'components': {'motor': self.component['id']}, 'uploader_name': 'Lab'}

    def real_test(self, name='alpha'):
        self.init['name'] = name
        notes_fixture.TestNotesTests.upload(self, 'Original description')
        meta = store.get_meta(name)
        meta.update(notes='Original findings', user_meta={'motor': 'legacy', '__proto__': 'keep'})
        store.write_json_atomic(self.tests / name / 'meta.json', meta)
        document = self.client.get(f'/api/tests/{name}/annotations').json()
        self.assertEqual(self.client.put(f'/api/tests/{name}/annotations', json={'expected_revision': 0,
            'expected_data_bounds': document['data_bounds'], 'annotations': [
                {'id': str(uuid4()), 'start_s': 1, 'end_s': 2, 'text': 'Keep interval'}]}).status_code, 200)
        self.assertEqual(self.client.put(f'/api/tests/{name}/testpoints', json={'test': name, 'test_points': [
            {'id': 3, 'name': 'Run', 'start_s': 0, 'end_s': 3}]}).status_code, 200)
        return self.tests / name

    def simple_test(self, name='alpha', value='first', status='ready'):
        path = self.tests / name; path.mkdir()
        store.write_json_atomic(path / 'meta.json', {'name': name, 'notes': value})
        store.write_json_atomic(path / 'status.json', {'status': status})
        (path / 'raw.csv').write_text(value)
        return path

    def remove(self, name='alpha'):
        response = self.client.delete(f'/api/tests/{name}')
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json(); UUID(result['trash_id'])
        return result['trash_id']

    def entries(self):
        response = self.client.get('/api/trash')
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['entries']

    def restore(self, entry_id, name=None):
        return self.client.post(f'/api/trash/{entry_id}/restore', json={} if name is None else {'name': name})

    def test_repeated_names_persist_distinct_ids_and_legacy_route_is_ambiguous(self):
        first_source = self.simple_test(); first = file_hashes(first_source)
        one = self.remove()
        second_source = self.simple_test(value='second'); second = file_hashes(second_source)
        two = self.remove()
        active_source = self.simple_test(value='active'); active = file_hashes(active_source)
        self.assertNotEqual(one, two)
        self.assertEqual({entry['id'] for entry in self.entries()}, {one, two})
        self.assertEqual(file_hashes(self.trash / one / 'data'), first)
        self.assertEqual(file_hashes(self.trash / two / 'data'), second)
        self.assertEqual(self.client.post('/api/tests/alpha/restore').status_code, 409)
        self.assertEqual(self.restore(one).status_code, 409)
        self.assertEqual(file_hashes(active_source), active)
        self.assertEqual(self.restore(one, 'first_copy').status_code, 200)
        self.assertEqual((self.tests / 'first_copy/raw.csv').read_text(), 'first')
        self.assertEqual(self.restore(two, 'second_copy').status_code, 200)
        self.assertEqual((self.tests / 'second_copy/raw.csv').read_text(), 'second')
        self.assertEqual(file_hashes(active_source), active)
        self.assertEqual(self.entries(), [])

    def test_real_restore_preserves_sources_annotations_bindings_and_upload_recovery(self):
        source = self.real_test(); before = file_hashes(source)
        metadata = store.get_meta('alpha')
        registry = components._path().read_bytes()
        entry_id = self.remove()
        self.assertEqual(file_hashes(self.trash / entry_id / 'data'), before)
        restored = self.restore(entry_id, 'restored_alpha')
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(store.get_meta('restored_alpha'), {**metadata, 'name': 'restored_alpha'})
        after = file_hashes(self.tests / 'restored_alpha')
        for relative in before:
            if relative not in ('meta.json', 'testpoints.json', '.upload/manifest.json'):
                self.assertEqual(after[relative], before[relative], relative)
        manifest = json.loads((self.tests / 'restored_alpha/.upload/manifest.json').read_text())
        self.assertEqual(manifest['name'], 'restored_alpha')
        self.assertEqual(json.loads((self.tests / 'restored_alpha/testpoints.json').read_text())['test'], 'restored_alpha')
        self.assertEqual(manifest['components']['motor'], self.component['id'])
        self.assertEqual(uploads._load_manifest('restored_alpha')['state'], 'ready')
        self.assertEqual(main._recover_interrupted_ingests(), [])
        self.assertEqual(store.get_status('restored_alpha')['status'], 'ready')
        self.assertEqual(components._path().read_bytes(), registry)

    def test_legacy_folder_migration_retains_content_time_and_stable_id(self):
        source = self.simple_test(); before = file_hashes(source)
        self.trash.mkdir(); source.rename(self.trash / 'alpha')
        old = (datetime.now(timezone.utc) - timedelta(minutes=20)).timestamp()
        os.utime(self.trash / 'alpha', (old, old))
        entries = self.entries()
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry['name'], 'alpha')
        self.assertTrue(entry['legacy_time_estimated'])
        self.assertAlmostEqual(datetime.fromisoformat(entry['deleted_at']).timestamp(), old, places=4)
        self.assertEqual(file_hashes(self.trash / entry['id'] / 'data'), before)
        self.assertEqual(self.entries()[0]['id'], entry['id'])
        self.assertEqual(self.client.post('/api/tests/alpha/restore').status_code, 200)
        self.assertEqual(file_hashes(self.tests / 'alpha'), before)

    def test_delete_batch_uses_confirmed_snapshot_and_keeps_active_and_registry(self):
        self.simple_test(); one = self.remove()
        self.simple_test(value='second'); two = self.remove()
        self.simple_test(value='new deletion'); later = self.remove()
        active = self.simple_test(value='active'); before = file_hashes(active)
        registry = components._path().read_bytes()
        response = self.client.request('DELETE', '/api/trash', json={'ids': [one, two, one]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {'deleted_ids': [one, two], 'failures': []})
        self.assertEqual({entry['id'] for entry in self.entries()}, {later})
        self.assertEqual(self.client.delete(f'/api/trash/{one}').status_code, 200)
        self.assertEqual(file_hashes(active), before)
        self.assertEqual(components._path().read_bytes(), registry)

    def test_partial_deletion_is_not_restorable_and_retry_finishes(self):
        self.simple_test(); one = self.remove()
        self.simple_test(value='second'); two = self.remove()
        original = trash.shutil.rmtree
        def fail_one(path):
            if path.parent.name == one:
                (path / 'raw.csv').unlink(missing_ok=True)
                raise PermissionError('simulated locked data')
            original(path)
        with patch.object(trash.shutil, 'rmtree', side_effect=fail_one):
            response = self.client.request('DELETE', '/api/trash', json={'ids': [one, two]})
        self.assertEqual(response.json()['deleted_ids'], [two])
        self.assertEqual(response.json()['failures'][0]['id'], one)
        entry = self.entries()[0]
        self.assertFalse(entry['restorable']); self.assertEqual(entry['state'], 'deleting')
        self.assertEqual(self.restore(one).status_code, 409)
        self.assertEqual(self.client.delete(f'/api/trash/{one}').status_code, 200)
        self.assertEqual(self.entries(), [])

    def test_failed_delete_and_restore_do_not_lose_the_dataset(self):
        source = self.simple_test(); before = file_hashes(source)
        with patch.object(Path, 'rename', side_effect=PermissionError('locked')):
            self.assertEqual(self.client.delete('/api/tests/alpha').status_code, 409)
        self.assertEqual(file_hashes(source), before)
        self.assertEqual(self.entries(), [])
        entry_id = self.remove()
        with patch.object(Path, 'rename', side_effect=PermissionError('locked')):
            self.assertEqual(self.restore(entry_id, 'beta').status_code, 409)
        self.assertEqual(file_hashes(self.trash / entry_id / 'data'), before)
        self.assertFalse((self.tests / 'beta').exists())
        self.assertEqual(self.restore(entry_id, 'beta').status_code, 200)

    def test_corrupt_metadata_is_retained_and_can_be_explicitly_deleted(self):
        source = self.simple_test(); (source / 'meta.json').write_text('{bad')
        entry_id = self.remove(); before = file_hashes(self.trash / entry_id / 'data')
        self.assertEqual(self.restore(entry_id).status_code, 409)
        self.assertEqual(file_hashes(self.trash / entry_id / 'data'), before)
        (self.trash / entry_id / 'entry.json').write_text('{bad')
        self.assertFalse(self.entries()[0]['restorable'])
        self.assertEqual(self.client.delete(f'/api/trash/{entry_id}').status_code, 200)

    def test_missing_entry_metadata_retains_uuid_and_cannot_be_restored(self):
        self.simple_test(); entry_id = self.remove()
        before = file_hashes(self.trash / entry_id / 'data')
        (self.trash / entry_id / 'entry.json').unlink()
        self.assertEqual(self.entries()[0]['id'], entry_id)
        self.assertFalse(self.entries()[0]['restorable'])
        self.assertEqual(file_hashes(self.trash / entry_id / 'data'), before)
        self.assertEqual(self.restore(entry_id).status_code, 409)
        self.assertEqual(self.client.delete(f'/api/trash/{entry_id}').status_code, 200)

    def test_metadata_write_failure_rolls_back_and_retry_preserves_all_fields(self):
        self.real_test(); entry_id = self.remove()
        folder = self.trash / entry_id / 'data'; before = file_hashes(folder)
        writer = store.write_json_atomic
        failed = False
        def fail_once(path, value):
            nonlocal failed
            if path.name == 'testpoints.json' and not failed:
                failed = True
                raise OSError('injected metadata write failure')
            writer(path, value)
        with patch.object(store, 'write_json_atomic', side_effect=fail_once):
            self.assertEqual(self.restore(entry_id, 'beta').status_code, 409)
        self.assertEqual(file_hashes(folder), before)
        self.assertFalse((self.tests / 'beta').exists())
        self.assertEqual(self.restore(entry_id, 'beta').status_code, 200)

    def test_failed_bookkeeping_write_does_not_remove_active_or_stored_data(self):
        source = self.simple_test(); before = file_hashes(source)
        with patch.object(store, 'write_json_atomic', side_effect=OSError('disk full')):
            self.assertEqual(self.client.delete('/api/tests/alpha').status_code, 409)
        self.assertEqual(file_hashes(source), before)
        entry_id = self.remove(); saved = file_hashes(self.trash / entry_id)
        with patch.object(store, 'write_json_atomic', side_effect=OSError('disk full')):
            result = self.client.request('DELETE', '/api/trash', json={'ids': [entry_id]}).json()
        self.assertEqual(result['deleted_ids'], [])
        self.assertEqual(len(result['failures']), 1)
        self.assertEqual(file_hashes(self.trash / entry_id), saved)
        self.assertTrue(self.entries()[0]['restorable'])

    def test_boundaries_busy_states_and_reserved_restore_names(self):
        self.simple_test(); entry_id = self.remove(); before = file_hashes(self.trash)
        for value in ('../outside', '.', '..', '', 'bad/name', 'CON', 'a.', 'a' * 201):
            with self.subTest(name=value):
                self.assertIn(self.restore(entry_id, value).status_code, (400, 422))
        self.assertEqual(self.client.delete('/api/trash/not-a-uuid').status_code, 422)
        self.assertEqual(self.client.request('DELETE', '/api/trash', json={'ids': []}).status_code, 422)
        self.assertEqual(self.restore(str(uuid4())).status_code, 404)
        self.assertEqual(file_hashes(self.trash), before)
        for status in ('receiving', 'ingesting', 'rebuilding'):
            with self.subTest(status=status):
                if not (self.tests / 'alpha').exists(): self.simple_test(status=status)
                store.write_json_atomic(self.tests / 'alpha/status.json', {'status': status})
                self.assertEqual(self.client.delete('/api/tests/alpha').status_code, 409)

    def test_concurrent_restore_and_delete_have_one_complete_outcome(self):
        self.simple_test(); entry_id = self.remove()
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(self.restore, entry_id, name) for name in ('beta', 'gamma')]
            self.assertEqual(sorted(job.result().status_code for job in jobs), [200, 404])
        winner = 'beta' if (self.tests / 'beta').exists() else 'gamma'
        self.assertEqual((self.tests / winner / 'raw.csv').read_text(), 'first')
        entry_id = self.remove(winner)
        with ThreadPoolExecutor(max_workers=2) as pool:
            restoring = pool.submit(self.restore, entry_id, 'winner')
            deleting = pool.submit(self.client.delete, f'/api/trash/{entry_id}')
            self.assertIn(restoring.result().status_code, (200, 404))
            self.assertEqual(deleting.result().status_code, 200)
        self.assertEqual(self.entries(), [])

    def test_restore_waits_for_destination_readers(self):
        self.simple_test(); entry_id = self.remove()
        started = threading.Event()
        def work():
            started.set(); return self.restore(entry_id, 'beta')
        with ThreadPoolExecutor(max_workers=1) as pool:
            with hold_read('beta'):
                future = pool.submit(work); self.assertTrue(started.wait(1))
                self.assertFalse(future.done())
            self.assertEqual(future.result(timeout=3).status_code, 200)

    def test_existing_expiry_policy_uses_deletion_time_not_payload_mtime(self):
        self.simple_test(); entry_id = self.remove()
        path = self.trash / entry_id / 'entry.json'
        record = json.loads(path.read_text())
        record['deleted_at'] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        store.write_json_atomic(path, record)
        # Listing is not a deletion. The old policy expires on next test delete.
        self.assertEqual(len(self.entries()), 1)
        self.simple_test(value='new')
        with patch.object(main, 'TRASH_MAX_AGE_S', 3600): self.remove()
        self.assertNotIn(entry_id, [entry['id'] for entry in self.entries()])
        survivor = self.entries()[0]
        record = json.loads((self.trash / survivor['id'] / 'entry.json').read_text())
        record['deleted_at'] = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        store.write_json_atomic(self.trash / survivor['id'] / 'entry.json', record)
        self.simple_test(value='last')
        with patch.object(main, 'TRASH_MAX_AGE_S', None): self.remove()
        self.assertIn(survivor['id'], [entry['id'] for entry in self.entries()])
