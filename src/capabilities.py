"""组件能力自描述，供集群平台和外部连接器发现。"""

from __future__ import annotations

from typing import Any

from src.config_loader import resolve_model_config, resolve_ops_config
from src.skill_registry import get_skill_registry
from src.tools import get_tool_registry


def build_capabilities() -> dict[str, Any]:
    model = resolve_model_config()
    ops = resolve_ops_config()
    tools = get_tool_registry()
    skills = get_skill_registry().skills

    return {
        "component": {
            "name": "ops-ai",
            "version": "0.3.0",
            "kind": "cluster-operations-agent",
            "protocol_version": "1.0",
        },
        "environment": {
            "name": ops["environment_name"],
            "tools_mode": ops["tools_mode"],
            "prometheus_url": ops["prometheus_base_url"],
            "loki_url": ops["loki_base_url"],
            "kubernetes_context": ops["k8s_context"],
            "kubernetes_namespace": ops["k8s_namespace"],
        },
        "models": {
            "llm_provider": model["llm_provider"],
            "llm_model": model["llm_model"],
            "embedding_provider": model["embed_provider"],
            "embedding_model": model["embed_model"],
        },
        "interfaces": ["web_chat", "cli_chat", "alertmanager_webhook"],
        "data_sources": ["prometheus", "loki", "kubernetes", "llm_wiki"],
        "tools": [
            {"name": name, **spec}
            for name, spec in sorted(tools.specs().items())
        ],
        "skills": [
            {
                "id": skill["id"],
                "name": skill["name"],
                "version": skill["version"],
                "description": skill["description"],
                "risk": skill["risk"],
                "write_op": skill["write_op"],
                "status": skill["status"],
                "required_tools": skill["required_tools"],
                "allowed_tools": skill["allowed_tools"],
            }
            for skill in sorted(skills, key=lambda item: item["id"])
        ],
    }
