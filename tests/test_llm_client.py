import importlib.util
import unittest
from unittest.mock import patch


REQUIRED_MODULES = ("langchain_core", "langchain_openai", "llama_index", "yaml")
HAS_DEPENDENCIES = all(importlib.util.find_spec(name) for name in REQUIRED_MODULES)


@unittest.skipUnless(HAS_DEPENDENCIES, "model dependencies are not installed")
class OllamaModelFactoryTest(unittest.TestCase):
    def setUp(self) -> None:
        import src.llm_client as llm_client

        self.client = llm_client
        self.client._build_chat_model.cache_clear()
        self._original_config = self.client._config
        self.client._config = {
            "llm_provider": "ollama",
            "llm_model": "qwen3:8b",
            "llm_base_url": "http://localhost:11434/v1",
            "llm_api_key": "ollama",
            "llm_temperature": 0.0,
            "llm_timeout": 120,
            "llm_max_retries": 2,
            "embed_provider": "ollama",
            "embed_model": "bge-m3",
            "embed_base_url": "http://localhost:11434/v1",
            "embed_api_key": "ollama",
            "offline_fallback_model": "offline-agent",
        }

    def tearDown(self) -> None:
        self.client._build_chat_model.cache_clear()
        self.client._config = self._original_config

    def test_chat_model_uses_openai_compatible_ollama_endpoint(self) -> None:
        with patch("langchain_openai.ChatOpenAI") as chat_model:
            self.client.get_chat_model()

        chat_model.assert_called_once_with(
            model="qwen3:8b",
            api_key="ollama",
            temperature=0.0,
            timeout=120,
            max_retries=2,
            base_url="http://localhost:11434/v1",
        )

    def test_rag_models_use_ollama_endpoint(self) -> None:
        with (
            patch("llama_index.llms.openai.OpenAI") as rag_llm,
            patch("llama_index.embeddings.openai.OpenAIEmbedding") as embedding,
        ):
            self.client.get_llm(allow_offline=False)
            self.client.get_embedding(allow_offline=False)

        rag_llm.assert_called_once_with(
            model="qwen3:8b",
            api_key="ollama",
            api_base="http://localhost:11434/v1",
        )
        embedding.assert_called_once_with(
            model_name="bge-m3",
            api_key="ollama",
            api_base="http://localhost:11434/v1",
        )


if __name__ == "__main__":
    unittest.main()
