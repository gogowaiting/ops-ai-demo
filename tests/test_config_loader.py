import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import yaml  # noqa: F401
except ImportError:
    yaml = None

if yaml is not None:
    from src.config_loader import resolve_model_config, resolve_ops_config
else:
    resolve_model_config = None
    resolve_ops_config = None


@unittest.skipUnless(yaml is not None, "PyYAML is not installed")
class ResolveModelConfigTest(unittest.TestCase):
    def _write_config(self, root: Path, content: str) -> None:
        (root / "model_config.yaml").write_text(content, encoding="utf-8")

    def test_ollama_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_config(
                root,
                "llm:\n  provider: ollama\nembedding:\n  provider: ollama\n",
            )
            with patch.dict(os.environ, {}, clear=True):
                cfg = resolve_model_config(temp_dir)

        self.assertEqual(cfg["llm_provider"], "ollama")
        self.assertEqual(cfg["llm_model"], "qwen3:8b")
        self.assertEqual(cfg["llm_base_url"], "http://localhost:11434/v1")
        self.assertEqual(cfg["llm_api_key"], "ollama")
        self.assertEqual(cfg["embed_provider"], "ollama")
        self.assertEqual(cfg["embed_model"], "bge-m3")

    def test_ollama_environment_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_config(
                root,
                "llm:\n  provider: ollama\nembedding:\n  provider: ollama\n",
            )
            env = {
                "OLLAMA_BASE_URL": "http://ollama:11434",
                "OLLAMA_MODEL": "qwen2.5:14b",
                "OLLAMA_EMBED_MODEL": "nomic-embed-text",
                "OPENAI_API_KEY": "must-not-win",
            }
            with patch.dict(os.environ, env, clear=True):
                cfg = resolve_model_config(temp_dir)

        self.assertEqual(cfg["llm_base_url"], "http://ollama:11434/v1")
        self.assertEqual(cfg["embed_base_url"], "http://ollama:11434/v1")
        self.assertEqual(cfg["llm_model"], "qwen2.5:14b")
        self.assertEqual(cfg["embed_model"], "nomic-embed-text")
        self.assertEqual(cfg["llm_api_key"], "ollama")

    def test_openai_embedding_key_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_config(root, "llm:\n  provider: openai\n")
            env = {
                "OPENAI_API_KEY": "shared-key",
                "EMBED_API_KEY": "embedding-key",
            }
            with patch.dict(os.environ, env, clear=True):
                cfg = resolve_model_config(temp_dir)

        self.assertEqual(cfg["llm_api_key"], "shared-key")
        self.assertEqual(cfg["embed_api_key"], "embedding-key")

    def test_external_ops_environment_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "ops_config.yaml").write_text(
                "environment:\n  name: local\n",
                encoding="utf-8",
            )
            env = {
                "OPS_ENVIRONMENT": "staging",
                "PROMETHEUS_URL": "https://prometheus.staging.example",
                "LOKI_URL": "https://loki.staging.example",
            }
            with patch.dict(os.environ, env, clear=True):
                cfg = resolve_ops_config(temp_dir)

        self.assertEqual(cfg["environment_name"], "staging")
        self.assertEqual(
            cfg["prometheus_base_url"], "https://prometheus.staging.example"
        )
        self.assertEqual(cfg["loki_base_url"], "https://loki.staging.example")


if __name__ == "__main__":
    unittest.main()
