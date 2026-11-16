"""Web 请求身份与角色授权。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import IntEnum

from fastapi import HTTPException, Request


class Role(IntEnum):
    VIEWER = 10
    OPERATOR = 20
    ADMIN = 30


ROLE_NAMES = {
    "viewer": Role.VIEWER,
    "operator": Role.OPERATOR,
    "admin": Role.ADMIN,
}


@dataclass(frozen=True)
class Principal:
    identity: str
    role: Role
    auth_mode: str
    source: str


def _auth_mode() -> str:
    return os.getenv("OPS_WEB_AUTH_MODE", "none").strip().lower()


def _roles(raw_roles: str) -> list[Role]:
    roles = []
    for raw in raw_roles.split(","):
        role = ROLE_NAMES.get(raw.strip().lower())
        if role is not None:
            roles.append(role)
    return roles or [Role.VIEWER]


def require_role(principal: Principal, minimum: Role) -> Principal:
    if principal.role < minimum:
        raise HTTPException(
            status_code=403,
            detail=f"当前身份缺少 {minimum.name.lower()} 权限",
        )
    return principal


def resolve_web_principal(request: Request) -> Principal:
    mode = _auth_mode()

    if mode == "token":
        expected = os.getenv("OPS_WEB_API_TOKEN", "")
        if not expected:
            raise HTTPException(status_code=503, detail="Web API token 未配置")

        provided = request.headers.get("X-Web-Token", "")
        authorization = request.headers.get("Authorization", "")
        if authorization.lower().startswith("bearer "):
            provided = authorization[7:].strip()
        if not provided or not secrets_equal(provided, expected):
            raise HTTPException(status_code=401, detail="Web 认证失败")

        return Principal(
            identity=os.getenv("OPS_WEB_API_USER", "web-token-user"),
            role=max(_roles(os.getenv("OPS_WEB_API_ROLES", "operator"))),
            auth_mode=mode,
            source="web_token",
        )

    if mode == "trusted_header":
        identity = request.headers.get(
            os.getenv("OPS_WEB_IDENTITY_HEADER", "X-User"),
            "",
        ).strip()
        if not identity:
            raise HTTPException(status_code=401, detail="可信身份头缺失")
        roles = request.headers.get(
            os.getenv("OPS_WEB_ROLES_HEADER", "X-Roles"),
            "",
        )
        return Principal(
            identity=identity[:120],
            role=max(_roles(roles)),
            auth_mode=mode,
            source="trusted_header",
        )

    if mode != "none":
        raise HTTPException(status_code=503, detail=f"未知 Web 认证模式: {mode}")

    # none 仅用于本地开发；生产部署必须切换 token 或 trusted_header。
    return Principal(
        identity="anonymous",
        role=Role.ADMIN,
        auth_mode=mode,
        source="development",
    )


def secrets_equal(left: str, right: str) -> bool:
    import secrets

    return secrets.compare_digest(left.encode("utf-8"), right.encode("utf-8"))
