"""Session identities and conservative compatibility tokens across lifecycle."""
import hashlib
import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from uuid import UUID

from fastapi.testclient import TestClient
from app import analysis_sources, main, store
from ._base import DataDirTestCase
from . import test_test_notes as notes_fixture


class AnalysisSourceTests(DataDirTestCase):
    def setUp(self):
        super().setUp()
        self.client = TestClient(main.app, raise_server_exceptions=False)
        self.body = ('time,signal\n' + ''.join(f'{i / 10},{i}\n' for i in range(40))).encode()
        self.init = {'name': 'alpha', 'source_file': 'source.csv', 'size_bytes': len(self.body), 'last_modified_ms': 1}
        notes_fixture.TestNotesTests.upload(self, 'Keep description')
        self.assertEqual(self.client.put('/api/tests/alpha/testpoints', json={'test': 'alpha', 'test_points': [
            {'id': 3, 'start_s': 0, 'end_s': 3, 'name': 'Run'}]}).status_code, 200)

    def catalog(self):
        response = self.client.get('/api/analysis-sources')
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['sources']

    def test_migration_is_additive_atomic_idempotent_and_shared(self):
        directory = self.tests / 'alpha'
        before = {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in directory.rglob('*') if p.is_file()}
        with ThreadPoolExecutor(max_workers=6) as pool:
            catalogs = list(pool.map(lambda _: self.catalog(), range(12)))
        first = catalogs[0][0]
        UUID(first['id'])
        self.assertTrue(all(catalog == [first] for catalog in catalogs))
        self.assertEqual(first['columns'], ['signal'])
        self.assertEqual(first['test_points'][0]['id'], 3)
        self.assertEqual({name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
                          for name in before}, before)
        self.assertEqual(json.loads((directory / analysis_sources.IDENTITY_FILE).read_text()), {'version': 1, 'id': first['id']})

    def test_rename_trash_restore_and_same_name_replacement(self):
        original = self.catalog()[0]
        self.assertEqual(self.client.post('/api/tests/alpha/rename?new_name=renamed').status_code, 200)
        renamed = self.catalog()[0]
        self.assertEqual(renamed, {**original, 'name': 'renamed'})
        entry = self.client.delete('/api/tests/renamed').json()['trash_id']
        self.assertEqual(self.catalog(), [])
        notes_fixture.TestNotesTests.upload(self, 'Second copy with identical data')
        self.assertEqual(self.client.post('/api/tests/alpha/rename?new_name=renamed').status_code, 200)
        replacement = self.catalog()[0]
        self.assertNotEqual(replacement['id'], original['id'])
        self.assertEqual(self.client.post(f'/api/trash/{entry}/restore', json={'name': 'restored'}).status_code, 200)
        restored = next(s for s in self.catalog() if s['name'] == 'restored')
        self.assertEqual(restored, {**original, 'name': 'restored'})
        self.assertNotEqual(entry, restored['id'])

    def test_notes_and_components_do_not_change_sample_revision(self):
        before = self.catalog()[0]
        self.assertEqual(self.client.patch('/api/tests/alpha/meta', json={'notes': 'Changed findings'}).status_code, 200)
        self.assertEqual(self.catalog()[0], before)
        meta = store.get_meta('alpha')
        meta.update(components={'motor': 'hardware-id'}, component_revision=5)
        store.write_json_atomic(self.tests / 'alpha/meta.json', meta)
        self.assertEqual(self.catalog()[0], before)

    def test_point_bounds_change_separately_from_sample_revision(self):
        before = self.catalog()[0]
        self.assertEqual(self.client.put('/api/tests/alpha/testpoints', json={'test': 'alpha', 'test_points': [
            {'id': 3, 'start_s': 0, 'end_s': 2, 'name': 'Run'}]}).status_code, 200)
        after = self.catalog()[0]
        self.assertEqual(after['id'], before['id'])
        self.assertEqual(after['revision'], before['revision'])
        self.assertNotEqual(after['test_points'], before['test_points'])

    def test_rebuild_preserves_identity_changes_revision(self):
        before = self.catalog()[0]
        response = self.client.post('/api/tests/alpha/edit', json={'rename': {'signal': 'force'}})
        self.assertEqual(response.status_code, 200, response.text)
        after = self.catalog()[0]
        self.assertEqual(after['id'], before['id'])
        self.assertNotEqual(after['revision'], before['revision'])
        self.assertEqual(after['columns'], ['force'])

    def test_damaged_duplicate_and_failed_identity_never_silently_replaced(self):
        path = self.tests / 'alpha' / analysis_sources.IDENTITY_FILE
        path.write_text('invalid')
        self.assertIsNone(self.catalog()[0]['id'])
        self.assertEqual(path.read_text(), 'invalid')
        path.write_text('{"version":1,"id":42}')
        self.assertIsNone(self.catalog()[0]['id'])
        self.assertEqual(json.loads(path.read_text())['id'], 42)
        path.unlink()
        with patch.object(store, 'write_json_atomic', side_effect=PermissionError('Read only')):
            self.assertIsNone(self.catalog()[0]['id'])
        self.assertFalse(path.exists())
        original = self.catalog()[0]
        shutil.copytree(self.tests / 'alpha', self.tests / 'copy')
        copied_meta = store.get_meta('copy'); copied_meta['name'] = 'copy'
        store.write_json_atomic(self.tests / 'copy/meta.json', copied_meta)
        result = self.catalog()
        self.assertEqual([s['id'] for s in result], [original['id']] * 2)
        self.assertTrue(all('Duplicate' in s['error'] for s in result))

    def test_busy_sources_do_not_migrate_or_take_writer_lock(self):
        directory = self.tests / 'alpha'
        store.write_json_atomic(directory / 'status.json', {'status': 'rebuilding'})
        with patch.object(analysis_sources, 'test_write', side_effect=AssertionError('must not block')):
            self.assertEqual(self.catalog(), [{'name': 'alpha', 'status': 'rebuilding', 'id': None}])
        self.assertFalse((directory / analysis_sources.IDENTITY_FILE).exists())

    def test_metadata_hydration_rejects_replaced_or_damaged_source(self):
        source_id = self.catalog()[0]['id']
        for suffix in ('', '/testpoints'):
            self.assertEqual(self.client.get(f'/api/tests/alpha{suffix}?expected_source_id={source_id}').status_code, 200)
        path = self.tests / 'alpha' / analysis_sources.IDENTITY_FILE
        path.write_text('broken identity')
        for suffix in ('', '/testpoints'):
            result = self.client.get(f'/api/tests/alpha{suffix}?expected_source_id={source_id}')
            self.assertEqual(result.status_code, 409, result.text)
        # Existing API clients remain compatible; a guarded read never migrates.
        self.assertEqual(self.client.get('/api/tests/alpha').status_code, 200)
        self.assertEqual(path.read_text(), 'broken identity')
