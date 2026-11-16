import os
import unittest
from unittest.mock import patch

from src.security import (
    Principal,
    Role,
    require_role,
    resolve_web_principal,
)


class FakeRequest:
    def __init__(self, headers: dict[str, str]):
        self.headers = headers


class SecurityTest(unittest.TestCase):
    def test_token_principal(self) -> None:
        request = FakeRequest({"Authorization": "Bearer web-secret"})
        with patch.dict(
            os.environ,
            {
                "OPS_WEB_AUTH_MODE": "token",
                "OPS_WEB_API_TOKEN": "web-secret",
                "OPS_WEB_API_USER": "alice",
                "OPS_WEB_API_ROLES": "operator",
            },
            clear=True,
        ):
            principal = resolve_web_principal(request)

        self.assertEqual(principal.identity, "alice")
        self.assertEqual(principal.role, Role.OPERATOR)
        self.assertEqual(principal.source, "web_token")

    def test_token_rejects_mismatch(self) -> None:
        request = FakeRequest({"Authorization": "Bearer bad"})
        with patch.dict(
            os.environ,
            {
                "OPS_WEB_AUTH_MODE": "token",
                "OPS_WEB_API_TOKEN": "web-secret",
            },
            clear=True,
        ):
            with self.assertRaises(Exception):
                resolve_web_principal(request)

    def test_trusted_header_principal(self) -> None:
        request = FakeRequest({"X-User": "alice", "X-Roles": "viewer,admin"})
        with patch.dict(
            os.environ,
            {"OPS_WEB_AUTH_MODE": "trusted_header"},
            clear=True,
        ):
            principal = resolve_web_principal(request)

        self.assertEqual(principal.identity, "alice")
        self.assertEqual(principal.role, Role.ADMIN)
        self.assertEqual(principal.source, "trusted_header")

    def test_missing_role_is_denied(self) -> None:
        principal = Principal("bob", Role.VIEWER, "trusted_header", "trusted_header")
        with self.assertRaises(Exception):
            require_role(principal, Role.OPERATOR)


if __name__ == "__main__":
    unittest.main()
