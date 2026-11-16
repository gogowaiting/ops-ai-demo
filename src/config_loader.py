import os
from pathlib import Path
from typing import Any

import yaml


DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434/v1"
DEFAULT_OLLAMA_API_KEY = "ollama"
DEFAULT_OLLAMA_CHAT_MODEL = "qwen3:8b"
DEFAULT_OLLAMA_EMBED_MODEL = "bge-m3"


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    return yaml.safe_load(text) or {}


def load_model_config(config_dir: str = "config") -> dict[str, Any]:
    base = Path(config_dir)
    cfg = _load_yaml(base / "model_config.yaml")

    llm = cfg.get("llm", {})
    embedding = cfg.get("embedding", {})
    offline = cfg.get("offline", {})

    return {
        "llm_provider": str(llm.get("provider", "openai") or "openai").strip().lower(),
        "llm_model": str(llm.get("model", "") or ""),
        "llm_base_url": str(llm.get("base_url", "") or ""),
        "llm_api_key": str(llm.get("api_key", "") or ""),
        "llm_temperature": float(llm.get("temperature", 0.0)),
        "llm_timeout": int(llm.get("timeout", 60)),
        "llm_max_retries": int(llm.get("max_retries", 2)),
        "embed_provider": str(embedding.get("provider", "openai") or "openai").strip().lower(),
        "embed_model": str(embedding.get("model", "") or ""),
        "embed_base_url": str(embedding.get("base_url", "") or ""),
        "embed_api_key": str(embedding.get("api_key", "") or ""),
        "offline_enabled": bool(offline.get("enabled", True)),
        "offline_fallback_model": str(offline.get("fallback_model", "offline-agent")),
    }


def _env_or(value: str, env_key: str) -> str:
    return os.getenv(env_key) or value


def _env_first_or(value: str, *env_keys: str) -> str:
    for env_key in env_keys:
        current = os.getenv(env_key)
        if current:
            return current
    return value


def _normalize_ollama_base_url(value: str) -> str:
    base_url = value.rstrip("/")
    return base_url if base_url.endswith("/v1") else f"{base_url}/v1"


def load_ops_config(config_dir: str = "config") -> dict[str, Any]:
    """加载运维组件统一配置（Prometheus / Loki / Kubernetes / 工具模式）。"""
    cfg = _load_yaml(Path(config_dir) / "ops_config.yaml")

    tools = cfg.get("tools", {})
    prometheus = cfg.get("prometheus", {})
    loki = cfg.get("loki", {})
    k8s = cfg.get("kubernetes", {})

    return {
        "tools_mode": str(tools.get("mode", "auto")).lower(),
        "prometheus_base_url": prometheus.get("base_url", ""),
        "prometheus_timeout": float(prometheus.get("timeout", 10)),
        "loki_base_url": loki.get("base_url", ""),
        "loki_timeout": float(loki.get("timeout", 15)),
        "loki_limit": int(loki.get("limit", 50)),
        "loki_lookback_minutes": int(loki.get("lookback_minutes", 10)),
        "k8s_kubeconfig": k8s.get("kubeconfig", ""),
        "k8s_context": k8s.get("context", ""),
        "k8s_namespace": k8s.get("namespace", "default"),
        "k8s_timeout": float(k8s.get("timeout", 15)),
    }


def resolve_ops_config(config_dir: str = "config") -> dict[str, Any]:
    """加载运维组件配置并用环境变量覆盖（部署环境优先）。"""
    cfg = load_ops_config(config_dir)

    cfg["tools_mode"] = _env_or(cfg["tools_mode"], "OPS_TOOLS_MODE")
    cfg["prometheus_base_url"] = _env_or(cfg["prometheus_base_url"], "PROMETHEUS_URL")
    cfg["loki_base_url"] = _env_or(cfg["loki_base_url"], "LOKI_URL")
    cfg["k8s_kubeconfig"] = _env_or(cfg["k8s_kubeconfig"], "KUBECONFIG")
    cfg["k8s_context"] = _env_or(cfg["k8s_context"], "K8S_CONTEXT")
    cfg["k8s_namespace"] = _env_or(cfg["k8s_namespace"], "K8S_NAMESPACE")

    return cfg


def resolve_model_config(config_dir: str = "config") -> dict[str, Any]:
    cfg = load_model_config(config_dir)

    cfg["llm_provider"] = _env_or(cfg["llm_provider"], "LLM_PROVIDER").strip().lower()
    cfg["embed_provider"] = _env_or(cfg["embed_provider"], "EMBED_PROVIDER").strip().lower()

    if cfg["llm_provider"] == "ollama":
        cfg["llm_api_key"] = _env_first_or(
            cfg["llm_api_key"] or DEFAULT_OLLAMA_API_KEY,
            "OLLAMA_API_KEY",
        )
        cfg["llm_base_url"] = _normalize_ollama_base_url(
            _env_first_or(
                cfg["llm_base_url"] or DEFAULT_OLLAMA_BASE_URL,
                "OLLAMA_BASE_URL",
            )
        )
        cfg["llm_model"] = _env_first_or(
            cfg["llm_model"] or DEFAULT_OLLAMA_CHAT_MODEL,
            "OLLAMA_MODEL",
            "LLM_MODEL",
        )
    else:
        cfg["llm_api_key"] = _env_or(cfg["llm_api_key"], "OPENAI_API_KEY")
        cfg["llm_base_url"] = _env_or(cfg["llm_base_url"], "OPENAI_BASE_URL")
        cfg["llm_model"] = _env_or(
            cfg["llm_model"] or "gpt-4o-mini",
            "LLM_MODEL",
        )

    if cfg["embed_provider"] == "ollama":
        cfg["embed_api_key"] = _env_first_or(
            cfg["embed_api_key"] or DEFAULT_OLLAMA_API_KEY,
            "OLLAMA_API_KEY",
        )
        cfg["embed_base_url"] = _normalize_ollama_base_url(
            _env_first_or(
                cfg["embed_base_url"] or DEFAULT_OLLAMA_BASE_URL,
                "OLLAMA_BASE_URL",
            )
        )
        cfg["embed_model"] = _env_first_or(
            cfg["embed_model"] or DEFAULT_OLLAMA_EMBED_MODEL,
            "OLLAMA_EMBED_MODEL",
            "EMBED_MODEL",
        )
    else:
        cfg["embed_api_key"] = _env_first_or(
            cfg["embed_api_key"],
            "EMBED_API_KEY",
            "OPENAI_API_KEY",
        )
        cfg["embed_base_url"] = _env_or(cfg["embed_base_url"], "OPENAI_BASE_URL")
        cfg["embed_model"] = _env_or(
            cfg["embed_model"] or "text-embedding-3-small",
            "EMBED_MODEL",
        )

    return cfg


def load_rag_config(config_dir: str = "config") -> dict[str, Any]:
    """加载 RAG 存储与切分配置（向量库后端 / chunk 参数 / 检索参数）。"""
    cfg = _load_yaml(Path(config_dir) / "ops_config.yaml")
    rag = cfg.get("rag", {})
    qdrant = rag.get("qdrant", {})

    return {
        "store": str(rag.get("store", "local")).lower(),
        "collection": str(rag.get("collection", "ops_wiki")),
        "chunk_size": int(rag.get("chunk_size", 512)),
        "chunk_overlap": int(rag.get("chunk_overlap", 50)),
        "top_k": int(rag.get("top_k", 3)),
        "persist_dir": str(rag.get("persist_dir", ".rag_index")),
        "qdrant_url": str(qdrant.get("url", "")),
        "qdrant_path": str(qdrant.get("path", ".rag_qdrant")),
        "qdrant_api_key": str(qdrant.get("api_key", "")),
    }


def resolve_rag_config(config_dir: str = "config") -> dict[str, Any]:
    """加载 RAG 配置并用环境变量覆盖（部署环境优先）。"""
    cfg = load_rag_config(config_dir)

    cfg["store"] = _env_or(cfg["store"], "RAG_STORE")
    cfg["qdrant_url"] = _env_or(cfg["qdrant_url"], "QDRANT_URL")
    cfg["qdrant_api_key"] = _env_or(cfg["qdrant_api_key"], "QDRANT_API_KEY")
    cfg["top_k"] = int(os.getenv("RAG_TOP_K") or cfg["top_k"])

    return cfg
