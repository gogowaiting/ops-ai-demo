"""LLM_WIKI：面向 LLM 的运维知识库（Wiki）模块。

在 RAG 引擎（rag_engine + knowledge_docs）之上提供统一检索入口：
- search(query, service=...)：向量检索优先，支持按业务元数据（service）过滤；
  llama-index 缺失或索引不可用时自动降级为关键词检索；
- 返回文本带来源标注（文档名 / 相似度 / service），可直接注入 Agent prompt；
- 惰性初始化 + 单例缓存；rebuild() 强制重建（切换向量库后端 / 文档变更后使用）。
"""

from __future__ import annotations

import logging
import re
import shutil
from functools import lru_cache
from pathlib import Path

from src.config_loader import resolve_rag_config

logger = logging.getLogger(__name__)

DEFAULT_WIKI_DIRS: tuple[str, ...] = ("knowledge_base",)


class LLMWiki:
    """运维知识 Wiki：向量检索（RAG）优先，缺依赖时降级为关键词检索。"""

    def __init__(self, knowledge_dirs: tuple[str, ...] = DEFAULT_WIKI_DIRS):
        self.knowledge_dirs = knowledge_dirs
        self._index = None  # 惰性构建的向量索引
        self._mode: str | None = None  # "rag" / "keyword"，None 表示尚未初始化

    # ---------- 初始化 ----------

    def _ensure_engine(self) -> None:
        if self._mode is not None:
            return
        try:
            from src.rag_engine import create_rag_index

            self._index = create_rag_index(self.knowledge_dirs[0])
            self._mode = "rag"
        except ImportError as exc:
            # 依赖缺失：降级为关键词检索，保持 Agent 主流程可用
            logger.warning("llama-index 未安装，LLM_WIKI 降级为关键词检索: %s", exc)
            self._index = None
            self._mode = "keyword"
        except Exception as exc:
            # 索引构建失败（如空知识库 / 向量库后端配置错误）：同样降级，不阻断主流程
            logger.warning("RAG 索引构建失败，LLM_WIKI 降级为关键词检索: %s", exc)
            self._index = None
            self._mode = "keyword"

    @property
    def mode(self) -> str:
        self._ensure_engine()
        return self._mode or "keyword"

    # ---------- 检索 ----------

    def search(self, query: str, top_k: int | None = None, service: str | None = None) -> str:
        """检索知识库，返回可直接注入 LLM prompt 的上下文（含来源标注）。

        service: 按文档 frontmatter 的 service 字段过滤，如 'order-service'。
        """
        if not query or not query.strip():
            # 边界处理：空查询直接返回提示，避免浪费一次检索
            return "Wiki 检索失败: 查询内容为空"

        self._ensure_engine()
        k = top_k or resolve_rag_config()["top_k"]
        scope = f"，service='{service}'" if service else ""
        if self._mode == "rag" and self._index is not None:
            from src.rag_engine import query_index

            response = query_index(self._index, query, k, service)
            body = f"Wiki 检索结果（RAG, top_k={k}{scope}，query='{query}'）:\n{response}"
            sources = _format_sources(response.source_nodes[:k])
            if sources:
                body += f"\n[来源] {sources}"
            return body
        return self._keyword_search(query, k, service)

    def _keyword_search(self, query: str, top_k: int, service: str | None = None) -> str:
        """降级路径：基于标题/正文的关键词匹配 + service 过滤，无外部依赖。"""
        terms = [t for t in re.split(r"[\s,，、;；]+", query) if t]
        if not terms:
            terms = [query]

        hits: list[tuple[int, str, str, str]] = []  # (score, source, text, doc_service)
        for d in self.knowledge_dirs:
            root = Path(d)
            if not root.is_dir():
                continue
            for f in sorted(root.glob("*.md")):
                try:
                    text = f.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError) as exc:
                    logger.warning("跳过无法读取的文档 %s: %s", f, exc)
                    continue
                doc_service = _doc_service(text)
                if service and doc_service and doc_service != service:
                    continue  # 指定了 service 且文档元数据不匹配
                # 简单评分：命中的关键词数量
                lowered = text.lower()
                score = sum(1 for t in terms if t.lower() in lowered)
                if score:
                    hits.append((score, f.name, text, doc_service))

        if not hits:
            scope = f"（service='{service}'）" if service else ""
            return f"Wiki 检索结果（关键词{scope}，query='{query}'）:\n未找到相关文档"

        hits.sort(key=lambda x: x[0], reverse=True)
        parts = []
        for score, name, text, doc_service in hits[:top_k]:
            tag = f", service={doc_service}" if doc_service else ""
            parts.append(f"--- {name} (匹配 {score} 个关键词{tag}) ---\n{text.strip()}")
        return f"Wiki 检索结果（关键词，query='{query}'）:\n" + "\n\n".join(parts)

    # ---------- 维护 ----------

    def rebuild(self) -> str:
        """清除向量数据与同步状态后重建，用于知识库更新 / 切换后端后刷新。"""
        cfg = resolve_rag_config()
        persist_dir = Path(cfg["persist_dir"])
        if persist_dir.is_dir():
            shutil.rmtree(persist_dir)
        qdrant_dir = Path(cfg["qdrant_path"])
        if cfg["store"] == "qdrant" and not cfg["qdrant_url"] and qdrant_dir.is_dir():
            shutil.rmtree(qdrant_dir)  # 嵌入式模式：连同本地向量库目录一起清理
        self._index = None
        self._mode = None
        self._ensure_engine()
        return f"Wiki 索引已重建（模式: {self.mode}，store: {cfg['store']}）"


def _doc_service(text: str) -> str:
    """从文档 frontmatter 提取 service 字段（关键词降级模式使用）。"""
    from src.knowledge_docs import split_frontmatter

    front, _ = split_frontmatter(text)
    return str(front.get("service", "")) if front else ""


def _format_source(node) -> str:
    """单个命中节点 -> 来源标注（文档名 / service / 相似度）。"""
    meta = getattr(node, "metadata", {}) or {}
    name = meta.get("source", "?")
    service = f", service={meta['service']}" if meta.get("service") else ""
    score = getattr(node, "score", None)
    score_text = f", score={score:.2f}" if isinstance(score, float) else ""
    return f"{name}{service}{score_text}"


def _format_sources(nodes: list) -> str:
    """检索命中节点 -> 来源列表（供 LLM 与人工核对出处）。"""
    if not nodes:
        return ""
    return "；".join(f"{i + 1}. {_format_source(n)}" for i, n in enumerate(nodes))


@lru_cache(maxsize=1)
def get_wiki() -> LLMWiki:
    """进程级单例：复用同一个 Wiki 实例与索引。"""
    return LLMWiki()
