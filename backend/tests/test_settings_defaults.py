import json
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main
from ._base import DataDirTestCase


def settings_payload(**overrides):
    payload = {
        "scatterX": "RPM",
        "scatterY": "Thrust",
        "datasheetZone": "reference-values",
        "datasheetVisible": True,
        "gridColumns": [f"signal_{i}" for i in range(9)],
        "xyYCols": ["" for _ in range(9)],
        "xyXCols": ["RPM" for _ in range(9)],
        "defaultViewMode": "spectrum",
        "specMode": "welch",
        "specLogY": True,
        "clustering": False,
        "uploadFsHz": "4096",
    }
    payload.update(overrides)
    return payload


class SettingsDefaultsTests(DataDirTestCase):
    def setUp(self):
        super().setUp()
        data_dir_patcher = patch.object(main, "DATA_DIR", self.root)
        data_dir_patcher.start()
        self.addCleanup(data_dir_patcher.stop)
        self.client = TestClient(main.app)

    def test_missing_defaults_are_reported_as_null(self):
        response = self.client.get("/api/settings/defaults")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"settings": None})

    def test_put_atomically_persists_and_get_returns_defaults(self):
        payload = settings_payload()

        put = self.client.put("/api/settings/defaults", json=payload)
        get = self.client.get("/api/settings/defaults")

        self.assertEqual(put.status_code, 200)
        self.assertEqual(put.json(), {"settings": payload})
        self.assertEqual(get.status_code, 200)
        self.assertEqual(get.json(), {"settings": payload})
        self.assertEqual(
            json.loads((self.root / "default-settings.json").read_text()),
            payload,
        )
        self.assertEqual(list(self.root.glob(".default-settings.json.*.tmp")), [])

    def test_invalid_payload_does_not_replace_existing_defaults(self):
        payload = settings_payload()
        self.assertEqual(
            self.client.put("/api/settings/defaults", json=payload).status_code,
            200,
        )

        invalid = settings_payload(gridColumns=["only one"])
        rejected = self.client.put("/api/settings/defaults", json=invalid)

        self.assertEqual(rejected.status_code, 422)
        self.assertEqual(
            self.client.get("/api/settings/defaults").json(),
            {"settings": payload},
        )

    def test_corrupt_file_falls_back_without_breaking_page_load(self):
        (self.root / "default-settings.json").write_text("not json")

        response = self.client.get("/api/settings/defaults")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"settings": None})
