"""Skill 触发匹配、冲突处理和 Agent 上下文构建。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from src.skill_loader import load_skills


@dataclass(frozen=True)
class SkillSelection:
    active: list[dict[str, Any]]
    shadow: list[dict[str, Any]]
    allowed_tools: frozenset[str]


class SkillRegistry:
    def __init__(self, skills_dir: str = "skills", known_tools: set[str] | None = None):
        self.skills = load_skills(skills_dir, known_tools=known_tools)

    def select(
        self,
        text: str,
        alert: dict[str, Any] | None = None,
        service: str = "",
        max_skills: int = 3,
    ) -> SkillSelection:
        haystack = "\n".join(
            [
                text,
                json.dumps(alert or {}, ensure_ascii=False, sort_keys=True),
                service,
            ]
        ).casefold()
        matched = []
        for skill in self.skills:
            if skill["status"] not in {"enabled", "shadow", "deprecated"}:
                continue
            if any(trigger.casefold() in haystack for trigger in skill["triggers"]):
                matched.append(skill)

        matched.sort(key=lambda item: item["priority"], reverse=True)
        shadow = [item for item in matched if item["status"] in {"shadow", "deprecated"}]
        enabled = [item for item in matched if item["status"] == "enabled"]

        if enabled and any(
            item["conflict_policy"] in {"exclusive", "highest_priority"}
            for item in enabled
        ):
            enabled = enabled[:1]
        else:
            enabled = enabled[:max_skills]

        allowed_tools = {
            tool
            for skill in enabled
            for tool in (skill["allowed_tools"] or skill["required_tools"])
        }
        return SkillSelection(
            active=enabled,
            shadow=shadow,
            allowed_tools=frozenset(allowed_tools),
        )


def build_skill_context(
    text: str,
    alert: dict[str, Any] | None = None,
    service: str = "",
) -> dict[str, Any]:
    selection = _default_registry().select(text, alert=alert, service=service)
    return {
        "skills": selection.active,
        "shadow_skills": [skill["id"] for skill in selection.shadow],
        "allowed_tools": sorted(selection.allowed_tools),
    }


_registry: SkillRegistry | None = None


def get_skill_registry() -> SkillRegistry:
    return _default_registry()


def _default_registry() -> SkillRegistry:
    global _registry
    if _registry is None:
        from src.tools import get_tool_registry

        _registry = SkillRegistry(known_tools=get_tool_registry().names())
    return _registry
