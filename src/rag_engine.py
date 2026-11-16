"""RAG 引擎：可配置向量库后端 + 文档切分 + 增量同步。

存储后端（config/ops_config.yaml → rag.store，环境变量 RAG_STORE 覆盖）：
- local：llama_index 内置 SimpleVectorStore，JSON 持久化到 persist_dir（默认，零外部依赖）；
- qdrant：Qdrant 向量库。qdrant.url 为空时走嵌入式本地模式（qdrant.path 目录持久化，
  无需部署服务）；填写 url 则连接远程 Qdrant（生产多副本场景）。

增量同步（persist_dir/doc_state.json 记录每个文档的 SHA256）：
- 文档无变化          -> 直接复用已有向量数据，零向量计算；
- 文档新增/变更/删除  -> 按新文档集合重建（local 清目录重建；qdrant 重建集合）。
  逐条 upsert 的增量更新待接入（当前规模全量重建为秒级，可靠性优先）。
"""

import json
import logging
import shutil
from pathlib import Path
from typing import cast

from llama_index.core import Settings, StorageContext, VectorStoreIndex
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.vector_stores import (
    FilterOperator,
    MetadataFilter,
    MetadataFilters,
)
from llama_index.core.vector_stores.simple import SimpleVectorStore

from src.config_loader import resolve_rag_config
from src.knowledge_docs import load_documents
from src.llm_client import get_embedding, get_llm

logger = logging.getLogger(__name__)

STATE_FILE = "doc_state.json"  # 增量同步状态：{文档名: 内容 SHA256}


def build_service_filters(service: str | None) -> MetadataFilters | None:
    """按业务元数据过滤（service 精确匹配）；SimpleVectorStore 与 Qdrant 均支持。"""
    if not service:
        return None
    return MetadataFilters(
        filters=[MetadataFilter(key="service", value=service, operator=FilterOperator.EQ)]
    )


def _build_vector_store(cfg: dict):
    store = cfg["store"]
    if store == "qdrant":
        try:
            from llama_index.vector_stores.qdrant import QdrantVectorStore
            from qdrant_client import QdrantClient
        except ImportError as exc:
            raise RuntimeError(
                "rag.store=qdrant 需要安装: pip install qdrant-client llama-index-vector-stores-qdrant"
            ) from exc

        if cfg["qdrant_url"]:
            client = QdrantClient(
                url=cfg["qdrant_url"], api_key=cfg["qdrant_api_key"] or None
            )
            logger.info("Qdrant 远程模式: %s", cfg["qdrant_url"])
        else:
            client = QdrantClient(path=cfg["qdrant_path"])
            logger.info("Qdrant 嵌入式本地模式: %s", cfg["qdrant_path"])
        return QdrantVectorStore(client=client, collection_name=cfg["collection"])

    if store != "local":
        raise ValueError(f"未知向量库后端: {store}（可选 local / qdrant）")
    return SimpleVectorStore()


def _configure_settings(cfg: dict) -> None:
    """统一配置全局模型组件与切分参数，加载与构建两条路径都必须生效。"""
    Settings.llm = get_llm()
    Settings.embed_model = get_embedding()
    Settings.node_parser = SentenceSplitter(
        chunk_size=cfg["chunk_size"], chunk_overlap=cfg["chunk_overlap"]
    )


# ---------- 增量同步状态 ----------

def _load_state(persist_dir: Path) -> dict[str, str]:
    state_file = persist_dir / STATE_FILE
    if not state_file.exists():
        return {}
    try:
        return json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("同步状态文件损坏，视为需要重建: %s", exc)
        return {}


def _save_state(persist_dir: Path, state: dict[str, str]) -> None:
    persist_dir.mkdir(parents=True, exist_ok=True)
    (persist_dir / STATE_FILE).write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _needs_rebuild(docs: list, state: dict[str, str]) -> tuple[bool, str]:
    """对比文档 hash 与上次同步状态，判断是否需要重建及原因。"""
    current = {d.metadata["source"]: d.metadata["doc_hash"] for d in docs}
    if not state:
        return True, "首次构建"
    if set(current) != set(state):
        added = sorted(set(current) - set(state)) or "-"
        removed = sorted(set(state) - set(current)) or "-"
        return True, f"文档集合变化（新增 {added} / 删除 {removed}）"
    changed = [name for name, h in current.items() if state.get(name) != h]
    if changed:
        return True, f"文档内容变更: {changed}"
    return False, ""


# ---------- 构建 / 加载 ----------

def _build_index(knowledge_dir: str, docs: list, vector_store) -> VectorStoreIndex:
    if not docs:
        # 边界处理：空知识库无法构建有效索引，快速失败并给出明确提示
        raise ValueError(f"知识库目录中未找到任何 Markdown 文档: {knowledge_dir}")
    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    return VectorStoreIndex.from_documents(docs, storage_context=storage_context)


def create_rag_index(knowledge_dir: str) -> VectorStoreIndex:
    """构建向量索引：优先复用已有向量数据，文档变更时按 hash 增量重建。"""
    cfg = resolve_rag_config()
    _configure_settings(cfg)
    persist_dir = Path(cfg["persist_dir"])

    docs = load_documents(knowledge_dir)
    state = _load_state(persist_dir)
    rebuild, reason = _needs_rebuild(docs, state)

    vector_store = _build_vector_store(cfg)
    index: VectorStoreIndex | None = None
    if not rebuild:
        try:
            if cfg["store"] == "local":
                from llama_index.core import load_index_from_storage

                index = cast(
                    VectorStoreIndex,
                    load_index_from_storage(
                        StorageContext.from_defaults(persist_dir=str(persist_dir))
                    ),
                )
            else:
                index = cast(
                    VectorStoreIndex,
                    VectorStoreIndex.from_vector_store(vector_store),
                )
            logger.info("已复用现有向量数据（store=%s）", cfg["store"])
        except Exception as exc:  # noqa: BLE001 - 加载失败兜底重建，避免链路不可用
            logger.warning("向量数据加载失败，将重建: %s", exc)
            rebuild = True

    if rebuild:
        logger.info("重建向量索引: %s", reason)
        if cfg["store"] == "local" and persist_dir.exists():
            shutil.rmtree(persist_dir)  # 清理旧 JSON，防止陈旧 docstore 残留
        if cfg["store"] == "qdrant":
            client = vector_store.client
            if client is not None and hasattr(client, "delete_collection"):
                try:
                    client.delete_collection(cfg["collection"])
                except Exception as exc:  # noqa: BLE001 - 集合不存在时忽略
                    logger.debug("清理旧 Qdrant 集合: %s", exc)
        index = _build_index(knowledge_dir, docs, vector_store)
        if cfg["store"] == "local":
            index.storage_context.persist(persist_dir=str(persist_dir))
        _save_state(persist_dir, {d.metadata["source"]: d.metadata["doc_hash"] for d in docs})

    assert index is not None  # 复用或重建两条路径必有其一成功
    return index


def query_index(index: VectorStoreIndex, query: str, top_k: int, service: str | None = None):
    """带元数据过滤的向量检索（service 精确匹配）。"""
    engine = index.as_query_engine(similarity_top_k=top_k, filters=build_service_filters(service))
    return engine.query(query)


def create_rag_engine(knowledge_dir: str):
    """兼容入口：返回默认 QueryEngine（无过滤）。"""
    cfg = resolve_rag_config()
    index = create_rag_index(knowledge_dir)
    return index.as_query_engine(similarity_top_k=cfg["top_k"])
