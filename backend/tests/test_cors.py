import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import config, main


class CorsTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    def preflight(self, origin: str):
        return self.client.options(
            "/api/health",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
            },
        )

    def test_heliweb_origins_are_allowed(self):
        for origin in ("http://heliweb", "https://heliweb"):
            with self.subTest(origin=origin):
                response = self.preflight(origin)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response.headers["access-control-allow-origin"], origin)

    def test_url_path_is_not_a_cors_origin(self):
        response = self.preflight("http://heliweb/ptt")
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("access-control-allow-origin", response.headers)

    def test_environment_override_is_trimmed_and_deduplicated(self):
        value = " https://one.local/,http://two.local,https://one.local "
        with patch.dict(os.environ, {"KIHA_CORS_ORIGINS": value}):
            self.assertEqual(
                config._cors_origins(),
                ("https://one.local", "http://two.local"),
            )


if __name__ == "__main__":
    unittest.main()
