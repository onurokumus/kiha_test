"""Storage defaults and explicit overrides, without accessing production data."""
import os
from pathlib import Path
import runpy
import sys
import unittest
from unittest.mock import patch


CONFIG_PATH = Path(__file__).resolve().parents[1] / "app" / "config.py"


class DataDirectoryTests(unittest.TestCase):
    def load_config(self, platform, environment=None):
        # Evaluate fresh startup bindings without reloading modules shared by
        # other tests or creating anything under the selected directory.
        with patch.dict(os.environ, environment or {}, clear=True), \
                patch.object(sys, "platform", platform):
            return runpy.run_path(str(CONFIG_PATH))

    def assert_storage_root(self, config, expected):
        self.assertEqual(config["DATA_DIR"], expected)
        self.assertEqual(config["TESTS_DIR"], expected / "tests")
        self.assertEqual(config["TRASH_DIR"], expected / "trash")

    def test_linux_uses_external_data_volume(self):
        self.assert_storage_root(self.load_config("linux"), Path("/data/ptt/data"))

    def test_desktop_development_keeps_checkout_data(self):
        for platform in ("win32", "darwin"):
            with self.subTest(platform=platform):
                config = self.load_config(platform)
                self.assert_storage_root(config, config["REPO_ROOT"] / "data")

    def test_environment_override_wins_on_every_platform(self):
        for platform in ("linux", "win32", "darwin"):
            with self.subTest(platform=platform):
                config = self.load_config(platform, {"KIHA_DATA_DIR": "/custom/ptt-data"})
                self.assert_storage_root(config, Path("/custom/ptt-data"))


if __name__ == "__main__":
    unittest.main()
