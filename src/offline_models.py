"""离线模型实现：无 API Key 时让 RAG 链路（向量化 + 问答）仍可完整运行。

- OfflineEmbedding：确定性哈希向量（词 + 中文字符 n-gram），本地计算，不依赖外部服务；
- OfflineChatLLM：抽取检索上下文作为回答，保证离线可演示、可回归测试。

仅在本模块被显式导入时才依赖 llama-index，避免拖累 Agent 主链路。
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from llama_index.core.embeddings import BaseEmbedding
from llama_index.core.llms import (
    CompletionResponse,
    CompletionResponseGen,
    CustomLLM,
    LLMMetadata,
)


class OfflineEmbedding(BaseEmbedding):
    """基于哈希的确定性向量化实现（离线可用，适合演示与小规模知识库）。"""

    embed_dim: int = 384
    model_name: str = "offline-hash-embedding"

    def _tokenize(self, text: str) -> list[str]:
        lowered = text.lower()
        words = re.findall(r"[a-z0-9]+", lowered)
        cjk = re.findall(r"[\u4e00-\u9fff]", lowered)
        # 中文以单字 + 二元组近似语义单元
        bigrams = [cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1)]
        return words + cjk + bigrams

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.embed_dim
        for token in self._tokenize(text):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            idx = int.from_bytes(digest, "big") % self.embed_dim
            vector[idx] += 1.0
        norm = sum(v * v for v in vector) ** 0.5 or 1.0
        return [v / norm for v in vector]

    def _get_query_embedding(self, query: str) -> list[float]:
        return self._embed(query)

    async def _aget_query_embedding(self, query: str) -> list[float]:
        return self._embed(query)

    def _get_text_embedding(self, text: str) -> list[float]:
        return self._embed(text)


class OfflineChatLLM(CustomLLM):
    """离线问答模型：返回检索到的知识库原文摘要，不产生幻觉内容。"""

    context_window: int = 8192
    num_output: int = 512
    model_name: str = "offline-rag-llm"

    @property
    def metadata(self) -> LLMMetadata:
        return LLMMetadata(
            context_window=self.context_window,
            num_output=self.num_output,
            model_name=self.model_name,
        )

    def complete(self, prompt: str, formatted: bool = False, **kwargs: Any) -> CompletionResponse:
        return CompletionResponse(text=self._compose(prompt))

    def stream_complete(
        self, prompt: str, formatted: bool = False, **kwargs: Any
    ) -> CompletionResponseGen:
        text = self._compose(prompt)
        yield CompletionResponse(text=text, delta=text)

    def _compose(self, prompt: str) -> str:
        parts = prompt.split("---------------------")
        context = parts[1].strip() if len(parts) >= 3 else prompt.strip()
        return (
            "[离线模式] 未配置真实 LLM，以下为知识库检索到的原文摘要：\n"
            f"{context[:600]}"
        )
