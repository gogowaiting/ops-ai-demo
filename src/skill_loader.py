"""运维 Skill Manifest 加载与校验。"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import yaml

VALID_RISKS = {"low", "medium", "high", "critical"}
VALID_STATUSES = {"disabled", "shadow", "enabled", "deprecated"}
VALID_APPROVALS = {"not_required", "recommended", "required"}
VALID_CONFLICT_POLICIES = {"merge", "highest_priority", "exclusive"}

_FRONTMATTER_RE = re.compile(
    r"\A---[ \t]*\n(?P<meta>.*?)\n---[ \t]*\n?(?P<body>.*)\Z",
    re.DOTALL,
)


class SkillValidationError(ValueError):
    """Skill Manifest 不符合扩展协议。"""


def _string_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise SkillValidationError(f"{field} 必须是字符串数组")
    return [item.strip() for item in value if item.strip()]


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    match = _FRONTMATTER_RE.match(text.strip())
    if not match:
        return {}, text.strip()
    try:
        meta = yaml.safe_load(match.group("meta")) or {}
    except yaml.YAMLError as exc:
        raise SkillValidationError(f"frontmatter YAML 解析失败: {exc}") from exc
    if not isinstance(meta, dict):
        raise SkillValidationError("frontmatter 必须是对象")
    return meta, match.group("body").strip()


def _validate(skill: dict[str, Any], known_tools: set[str] | None) -> None:
    if not skill["name"]:
        raise SkillValidationError(f"skill missing name: {skill['file']}")
    if skill["risk"] not in VALID_RISKS:
        raise SkillValidationError(f"invalid risk for {skill['name']}: {skill['risk']}")
    if skill["status"] not in VALID_STATUSES:
        raise SkillValidationError(f"invalid status for {skill['name']}: {skill['status']}")
    if skill["approval"] not in VALID_APPROVALS:
        raise SkillValidationError(f"invalid approval for {skill['name']}: {skill['approval']}")
    if skill["conflict_policy"] not in VALID_CONFLICT_POLICIES:
        raise SkillValidationError(
            f"invalid conflict_policy for {skill['name']}: {skill['conflict_policy']}"
        )
    if not skill["triggers"]:
        raise SkillValidationError(f"skill missing triggers: {skill['name']}")
    if known_tools is not None:
        unknown = (set(skill["required_tools"]) | set(skill["allowed_tools"])) - known_tools
        if unknown:
            raise SkillValidationError(
                f"skill {skill['name']} references unknown tools: {sorted(unknown)}"
            )


def load_skills(
    skills_dir: str = "skills",
    known_tools: set[str] | None = None,
) -> list[dict[str, Any]]:
    """加载递归目录下的 SKILL.md，并返回标准化 Skill Manifest。"""
    skills: list[dict[str, Any]] = []
    for skill_file in sorted(Path(skills_dir).rglob("SKILL.md")):
        text = skill_file.read_text(encoding="utf-8")
        meta, body = _split_frontmatter(text)
        name = str(meta.get("name", "") or "").strip()
        write_op = bool(meta.get("write_op", False))
        risk = str(meta.get("risk", "low") or "low").strip().lower()
        approval = str(
            meta.get("approval", "required" if write_op else "not_required") or ""
        ).strip().lower()
        skill_id = str(meta.get("id", "") or skill_file.parent.name or name).strip()
        try:
            priority = int(meta.get("priority", 50))
        except (TypeError, ValueError) as exc:
            raise SkillValidationError(
                f"priority must be an integer for {name or skill_file}"
            ) from exc
        skill = {
            "id": skill_id,
            "name": name,
            "version": str(meta.get("version", "1.0.0") or "1.0.0").strip(),
            "description": str(meta.get("description", "") or "").strip(),
            "risk": risk,
            "write_op": write_op,
            "triggers": _string_list(meta.get("triggers"), "triggers"),
            "required_tools": _string_list(meta.get("required_tools"), "required_tools"),
            "allowed_tools": _string_list(meta.get("allowed_tools"), "allowed_tools"),
            "approval": approval,
            "priority": priority,
            "conflict_policy": str(
                meta.get("conflict_policy", "merge") or "merge"
            ).strip().lower(),
            "status": str(meta.get("status", "enabled") or "enabled").strip().lower(),
            "file": str(skill_file),
            "body": body,
            "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }
        _validate(skill, known_tools)
        skills.append(skill)

    names = [skill["name"] for skill in skills]
    if len(names) != len(set(names)):
        raise SkillValidationError("duplicate skill names detected")
    ids = [skill["id"] for skill in skills]
    if len(ids) != len(set(ids)):
        raise SkillValidationError("duplicate skill ids detected")
    return skills
