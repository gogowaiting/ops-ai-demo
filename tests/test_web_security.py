import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.web_server import app


class WebSecurityTest(unittest.TestCase):
    def test_token_mode_requires_role(self) -> None:
        env = {
            "OPS_WEB_AUTH_MODE": "token",
            "OPS_WEB_API_TOKEN": "web-secret",
            "OPS_WEB_API_USER": "alice",
            "OPS_WEB_API_ROLES": "operator",
        }
        with patch.dict(os.environ, env, clear=True):
            client = TestClient(app)
            denied = client.get("/api/capabilities")
            self.assertEqual(denied.status_code, 401)

            authorized = client.get(
                "/api/capabilities",
                headers={"Authorization": "Bearer web-secret"},
            )
            self.assertEqual(authorized.status_code, 200)
            self.assertEqual(authorized.json()["component"]["name"], "ops-ai")

    def test_metrics_require_viewer(self) -> None:
        with patch.dict(os.environ, {"OPS_WEB_AUTH_MODE": "none"}, clear=True):
            client = TestClient(app)
            response = client.get("/metrics")
            self.assertEqual(response.status_code, 200)
            self.assertIn("opsai_http_requests_total", response.text)


if __name__ == "__main__":
    unittest.main()
