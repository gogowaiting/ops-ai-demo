import re
import unittest
from pathlib import Path


class RequirementsTest(unittest.TestCase):
    def test_direct_dependencies_are_declared(self) -> None:
        requirements = Path("requirements.txt").read_text(encoding="utf-8")
        declared = {
            match.group(0).lower()
            for line in requirements.splitlines()
            if (match := re.match(r"[A-Za-z0-9_.-]+", line))
        }
        required = {
            "fastapi",
            "langchain-core",
            "langchain-openai",
            "langgraph",
            "langgraph-checkpoint",
            "langgraph-checkpoint-sqlite",
            "llama-index",
            "llama-index-core",
            "llama-index-embeddings-openai",
            "llama-index-llms-openai",
            "llama-index-vector-stores-qdrant",
            "pydantic",
            "pyyaml",
            "qdrant-client",
            "requests",
            "typing-extensions",
            "uvicorn",
        }

        self.assertEqual(required - declared, set())


if __name__ == "__main__":
    unittest.main()
