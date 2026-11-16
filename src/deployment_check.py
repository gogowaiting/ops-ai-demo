"""部署环境配置校验。"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


@dataclass(frozen=True)
class CheckResult:
    errors: list[str]
    warnings: list[str]

    @property
    def ok(self) -> bool:
        return not self.errors


def parse_env_file(path: str | Path) -> tuple[dict[str, str], list[str]]:
    values: dict[str, str] = {}
    warnings: list[str] = []
    for line_no, raw_line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            warnings.append(f"{path}:{line_no}: 无法解析（缺少 =）: {raw_line}")
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not key or not key.replace("_", "").isalnum():
            warnings.append(f"{path}:{line_no}: 无效变量名: {key}")
            continue
        if key in values:
            warnings.append(f"{path}:{line_no}: 重复定义 {key}，后者覆盖前者")
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key] = value
    return values, warnings


def _is_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def validate_env(values: dict[str, Any]) -> CheckResult:
    errors: list[str] = []
    warnings: list[str] = []
    env = values.get("OPS_ENVIRONMENT", "production").strip().lower()
    tools_mode = values.get("OPS_TOOLS_MODE", "").strip().lower()
    auth_mode = values.get("OPS_WEB_AUTH_MODE", "none").strip().lower()
    db_backend = values.get("OPS_DB_BACKEND", "sqlite").strip().lower()

    for key in ("OPS_ENVIRONMENT", "OPS_TOOLS_MODE", "OPS_WEB_AUTH_MODE"):
        if not values.get(key, "").strip():
            errors.append(f"{key} 不能为空")

    if tools_mode not in {"real", "mock", "auto"}:
        errors.append("OPS_TOOLS_MODE 必须是 real/mock/auto")
    if auth_mode not in {"none", "token", "trusted_header"}:
        errors.append("OPS_WEB_AUTH_MODE 必须是 none/token/trusted_header")
    if db_backend not in {"sqlite", "postgres"}:
        errors.append("OPS_DB_BACKEND 必须是 sqlite/postgres")

    if tools_mode == "real":
        for key in ("PROMETHEUS_URL", "LOKI_URL"):
            value = values.get(key, "").strip()
            if not value:
                errors.append(f"OPS_TOOLS_MODE=real 时 {key} 不能为空")
            elif not _is_url(value):
                errors.append(f"{key} 必须是 http/https URL")

    if auth_mode == "none":
        message = "OPS_WEB_AUTH_MODE=none 仅用于本地开发"
        (errors if env == "production" else warnings).append(message)
    elif auth_mode == "token":
        for key in ("OPS_WEB_API_TOKEN", "OPS_WEB_API_USER"):
            if not values.get(key, "").strip():
                errors.append(f"OPS_WEB_AUTH_MODE=token 时 {key} 不能为空")
        role = values.get("OPS_WEB_API_ROLES", "operator").strip().lower()
        if role and role not in {"viewer", "operator", "admin"}:
            errors.append("OPS_WEB_API_ROLES 必须是 viewer/operator/admin")
    else:
        for key in ("OPS_WEB_IDENTITY_HEADER", "OPS_WEB_ROLES_HEADER"):
            if not values.get(key, "").strip():
                errors.append(f"OPS_WEB_AUTH_MODE=trusted_header 时 {key} 不能为空")

    llm_provider = values.get("LLM_PROVIDER", "").strip().lower()
    embed_provider = values.get("EMBED_PROVIDER", "").strip().lower()
    if llm_provider and embed_provider and llm_provider != embed_provider:
        warnings.append("LLM_PROVIDER 与 EMBED_PROVIDER 不一致，请确认是否有意混用")

    if llm_provider == "openai":
        if not values.get("OPENAI_API_KEY", "").strip():
            errors.append("LLM_PROVIDER=openai 时 OPENAI_API_KEY 不能为空")
    elif llm_provider == "ollama":
        for key in ("OLLAMA_BASE_URL", "OLLAMA_MODEL"):
            if not values.get(key, "").strip():
                errors.append(f"LLM_PROVIDER=ollama 时 {key} 不能为空")
        if not _is_url(values.get("OLLAMA_BASE_URL", "")):
            errors.append("OLLAMA_BASE_URL 必须是 http/https URL")

    if embed_provider == "openai" and not values.get("EMBED_API_KEY", "").strip():
        errors.append("EMBED_PROVIDER=openai 时 EMBED_API_KEY 不能为空")
    elif embed_provider == "ollama" and not values.get("OLLAMA_EMBED_MODEL", "").strip():
        errors.append("EMBED_PROVIDER=ollama 时 OLLAMA_EMBED_MODEL 不能为空")

    if env == "production":
        if tools_mode != "real":
            warnings.append("生产环境建议 OPS_TOOLS_MODE=real")
        if not values.get("ALERT_WEBHOOK_TOKEN", "").strip():
            warnings.append("生产环境建议配置 ALERT_WEBHOOK_TOKEN")
        if not values.get("K8S_NAMESPACE", "").strip():
            warnings.append("生产环境建议配置 K8S_NAMESPACE")
    if db_backend == "postgres":
        db_url = values.get("OPS_DB_URL", "").strip()
        if not db_url:
            errors.append("OPS_DB_BACKEND=postgres 时 OPS_DB_URL 不能为空")
        else:
            parsed = urlparse(db_url)
            if parsed.scheme not in {
                "postgres",
                "postgresql",
                "postgresql+psycopg",
            }:
                errors.append("OPS_DB_URL 必须使用 postgresql:// 或 postgresql+psycopg://")

    return CheckResult(errors=errors, warnings=warnings)


def main() -> int:
    parser = argparse.ArgumentParser(description="校验 Ops AI 部署环境变量")
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args()

    try:
        values, parse_warnings = parse_env_file(args.env_file)
    except FileNotFoundError:
        print(f"[FAIL] 环境文件不存在: {args.env_file}")
        return 1
    except OSError as exc:
        print(f"[FAIL] 环境文件读取失败: {exc}")
        return 1

    result = validate_env(values)
    for warning in parse_warnings:
        print(f"[WARN] {warning}")
    for warning in result.warnings:
        print(f"[WARN] {warning}")
    for error in result.errors:
        print(f"[FAIL] {error}")
    if result.ok:
        print("[OK] 部署配置校验通过")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
