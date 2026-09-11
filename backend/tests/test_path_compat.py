"""Exercise populated production endpoints without Python 3.12 pathlib APIs."""
from pathlib import Path
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import PropertyMock, patch

import pyarrow as pa
import pyarrow.parquet as pq
from fastapi.testclient import TestClient

from app import main, store
from app.paths import is_link_or_junction
from ._base import DataDirTestCase


class LinkCheckTests(unittest.TestCase):
    def test_posix_links_and_windows_junctions_remain_rejected(self):
        cases = [
            (SimpleNamespace(st_mode=stat.S_IFREG), False),
            (SimpleNamespace(st_mode=stat.S_IFDIR), False),
            (SimpleNamespace(st_mode=stat.S_IFLNK), True),
            (SimpleNamespace(st_mode=stat.S_IFDIR, st_reparse_tag=0xA0000003), True),
            (SimpleNamespace(st_mode=stat.S_IFDIR, st_reparse_tag=0), False),
            (SimpleNamespace(st_mode=stat.S_IFDIR, st_reparse_tag=0x8000001B), False),
        ]
        for info, linked in cases:
            with self.subTest(info=info), patch.object(Path, 'lstat', return_value=info):
                self.assertEqual(is_link_or_junction(Path('fixture')), linked)

    def test_absent_paths_are_allowed_but_io_errors_are_not_hidden(self):
        for error in (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
            with self.subTest(error=error), patch.object(Path, 'lstat', side_effect=error):
                if error in (FileNotFoundError, NotADirectoryError):
                    self.assertFalse(is_link_or_junction(Path('fixture')))
                else:
                    with self.assertRaises(error):
                        is_link_or_junction(Path('fixture'))


class PathCompatibilityTests(DataDirTestCase):
    def setUp(self):
        super().setUp()
        self.client = TestClient(main.app, raise_server_exceptions=False)
        directory = self.tests / 'alpha'
        directory.mkdir()
        pq.write_table(pa.table({'time': [0., 0.5, 1., 1.5], 'rpm': [0., 1000., 2000., 3000.]}),
                       directory / 'data.parquet')
        motor = self.client.post('/api/components', json={'kind': 'motor', 'name': 'Motor'}).json()
        store.write_json_atomic(directory / 'meta.json', dict(
            name='alpha', columns=['time', 'rpm'], time_column='time', fs_hz=2,
            n_rows=4, n_columns=2, t_start=0., duration_s=2., time_source='measured',
            components={'motor': motor['id']}, component_rpm_column='rpm',
            time_gap_ranges=[], acquisition_gap_ranges=[]))
        store.write_json_atomic(directory / 'status.json', {'status': 'ready'})
        store.write_json_atomic(directory / 'testpoints.json', {'test': 'alpha', 'test_points': [
            {'id': 1, 'start_idx': 0, 'end_idx': 4, 'start_s': 0., 'end_s': 2.}]})
        # Attribute access itself fails as on 3.11; this runs on newer CI too.
        unavailable = patch.object(Path, 'is_junction', new_callable=PropertyMock,
                                   side_effect=AttributeError('Path.is_junction is unavailable'), create=True)
        unavailable.start()
        self.addCleanup(unavailable.stop)

    def get(self, route):
        response = self.client.get('/api/' + route)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_catalog_and_guarded_reads_without_new_pathlib_api(self):
        self.assertEqual(self.get('tests')[0]['status'], 'ready')
        source = self.get('analysis-sources')['sources'][0]
        self.assertTrue(source['id'])
        self.assertEqual(source['columns'], ['rpm'])
        for suffix in ('', '/testpoints'):
            self.get(f"tests/alpha{suffix}?expected_source_id={source['id']}")
        store.write_json_atomic(self.tests / 'alpha/status.json', {'status': 'rebuilding'})
        busy = self.get('analysis-sources')['sources'][0]
        self.assertEqual(busy, {'name': 'alpha', 'status': 'rebuilding', 'id': source['id']})

    def test_component_statistics_without_new_pathlib_api(self):
        result = self.get('component-statistics')
        self.assertIsNone(result['sources'][0]['issue'])
        self.assertEqual(result['sources'][0]['summary']['running_seconds'], 1.5)

    def test_trash_lifecycle_without_new_pathlib_api_preserves_samples(self):
        data = (self.tests / 'alpha/data.parquet').read_bytes()
        deleted = self.client.delete('/api/tests/alpha')
        self.assertEqual(deleted.status_code, 200, deleted.text)
        entry_id = deleted.json()['trash_id']
        self.assertEqual(self.get('trash')['entries'][0]['id'], entry_id)
        restored = self.client.post(f'/api/trash/{entry_id}/restore', json={'name': 'restored'})
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual((self.tests / 'restored/data.parquet').read_bytes(), data)
        deleted = self.client.delete('/api/tests/restored')
        self.assertEqual(deleted.status_code, 200, deleted.text)
        purged = self.client.delete('/api/trash/' + deleted.json()['trash_id'])
        self.assertEqual(purged.status_code, 200, purged.text)
        self.assertEqual(self.get('trash')['entries'], [])

    def test_populated_legacy_trash_without_new_pathlib_api(self):
        self.trash.mkdir()
        (self.tests / 'alpha').rename(self.trash / 'alpha')
        entries = self.get('trash')['entries']
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['name'], 'alpha')
        self.assertTrue(entries[0]['restorable'])
