import tempfile
import unittest
from pathlib import Path

from src.deployment_check import parse_env_file, validate_env


class DeploymentCheckTest(unittest.TestCase):
    def test_parses_comments_quotes_and_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / ".env"
            path.write_text(
                "# comment\nexport A='hello world'\nB=2\nA=override\n",
                encoding="utf-8",
            )
            values, warnings = parse_env_file(path)

        self.assertEqual(values, {"A": "override", "B": "2"})
        self.assertEqual(len(warnings), 1)
        self.assertIn("重复定义 A", warnings[0])

    def test_valid_production_config(self) -> None:
        result = validate_env(
            {
                "OPS_ENVIRONMENT": "production",
                "OPS_TOOLS_MODE": "real",
                "PROMETHEUS_URL": "https://prometheus.example.com",
                "LOKI_URL": "https://loki.example.com",
                "OPS_WEB_AUTH_MODE": "token",
                "OPS_WEB_API_TOKEN": "secret",
                "OPS_WEB_API_USER": "alice",
                "OPS_WEB_API_ROLES": "operator",
                "ALERT_WEBHOOK_TOKEN": "alert-secret",
                "K8S_NAMESPACE": "default",
                "LLM_PROVIDER": "ollama",
                "EMBED_PROVIDER": "ollama",
                "OLLAMA_BASE_URL": "http://ollama:11434/v1",
                "OLLAMA_MODEL": "qwen3:8b",
                "OLLAMA_EMBED_MODEL": "bge-m3",
            }
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.errors, [])

    def test_real_production_requires_urls_alert_token_and_web_token(self) -> None:
        result = validate_env(
            {
                "OPS_ENVIRONMENT": "production",
                "OPS_TOOLS_MODE": "real",
                "OPS_WEB_AUTH_MODE": "token",
            }
        )

        error_messages = "\n".join(result.errors)
        warning_messages = "\n".join(result.warnings)
        self.assertFalse(result.ok)
        self.assertIn("PROMETHEUS_URL", error_messages)
        self.assertIn("LOKI_URL", error_messages)
        self.assertIn("OPS_WEB_API_TOKEN", error_messages)
        self.assertIn("ALERT_WEBHOOK_TOKEN", warning_messages)

    def test_rejects_none_auth_in_production(self) -> None:
        result = validate_env(
            {
                "OPS_ENVIRONMENT": "production",
                "OPS_WEB_AUTH_MODE": "none",
            }
        )
        self.assertFalse(result.ok)
        self.assertIn("OPS_WEB_AUTH_MODE=none", "\n".join(result.errors))

    def test_postgres_backend_requires_database_url(self) -> None:
        result = validate_env(
            {
                "OPS_DB_BACKEND": "postgres",
                "OPS_TOOLS_MODE": "mock",
                "OPS_WEB_AUTH_MODE": "none",
            }
        )

        self.assertFalse(result.ok)
        self.assertIn("OPS_DB_URL", "\n".join(result.errors))

    def test_postgres_backend_rejects_invalid_url(self) -> None:
        result = validate_env(
            {
                "OPS_DB_BACKEND": "postgres",
                "OPS_DB_URL": "mysql://localhost/db",
                "OPS_TOOLS_MODE": "mock",
                "OPS_WEB_AUTH_MODE": "none",
            }
        )

        self.assertFalse(result.ok)
        self.assertIn("postgresql://", "\n".join(result.errors))


if __name__ == "__main__":
    unittest.main()
