import unittest
import os
from unittest.mock import patch

from src.database import DatabaseConfig


class DatabaseConfigTest(unittest.TestCase):
    def test_defaults_to_sqlite_path(self) -> None:
        with patch.dict(
            os.environ,
            {"OPS_DB_BACKEND": "sqlite", "APPROVAL_AUDIT_DB": "audit.sqlite"},
            clear=True,
        ):
            config = DatabaseConfig.resolve("default.sqlite", "APPROVAL_AUDIT_DB")

        self.assertEqual(config.backend, "sqlite")
        self.assertEqual(config.path, "audit.sqlite")
        self.assertEqual(config.placeholder, "?")

    def test_sqlite_url_uses_shared_file(self) -> None:
        with patch.dict(
            os.environ,
            {"OPS_DB_BACKEND": "sqlite", "OPS_DB_URL": "sqlite:///data/shared.sqlite"},
            clear=True,
        ):
            config = DatabaseConfig.resolve("default.sqlite", "APPROVAL_AUDIT_DB")

        self.assertEqual(config.backend, "sqlite")
        self.assertEqual(config.path, "data/shared.sqlite")

    def test_postgres_requires_url(self) -> None:
        with patch.dict(os.environ, {"OPS_DB_BACKEND": "postgres"}, clear=True):
            with self.assertRaises(RuntimeError):
                DatabaseConfig.resolve("default.sqlite", "APPROVAL_AUDIT_DB")
if __name__ == "__main__":
    unittest.main()
