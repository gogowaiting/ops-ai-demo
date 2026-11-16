"""受控运维工具注册与第三方 Connector 扩展接口。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


class ToolRegistrationError(ValueError):
    """工具定义重复或元数据无效。"""


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    execute: Callable[..., str]
    risk: str = "low"
    write_op: bool = False
    input_schema: dict[str, Any] = field(default_factory=dict)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        if not definition.name or not definition.name.replace("_", "").isalnum():
            raise ToolRegistrationError(f"invalid tool name: {definition.name}")
        if definition.name in self._tools:
            raise ToolRegistrationError(f"duplicate tool: {definition.name}")
        if definition.risk not in {"low", "medium", "high", "critical"}:
            raise ToolRegistrationError(f"invalid risk for {definition.name}")
        if not callable(definition.execute):
            raise ToolRegistrationError(f"tool is not callable: {definition.name}")
        self._tools[definition.name] = definition

    def register_many(self, definitions: list[ToolDefinition]) -> None:
        for definition in definitions:
            self.register(definition)

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def names(self) -> set[str]:
        return set(self._tools)

    def write_tools(self) -> set[str]:
        return {
            name for name, definition in self._tools.items() if definition.write_op
        }

    def specs(self) -> dict[str, dict[str, Any]]:
        return {
            name: {
                "description": definition.description,
                "risk": definition.risk,
                "write_op": definition.write_op,
                "input_schema": definition.input_schema,
            }
            for name, definition in self._tools.items()
        }

    def dispatch(
        self,
        name: str,
        kwargs: dict[str, Any],
        *,
        approved: bool = False,
    ) -> str:
        definition = self._tools.get(name)
        if definition is None:
            return f"未知动作: {name}"
        if definition.write_op:
            kwargs = {**kwargs, "approved": approved}
        try:
            return definition.execute(**kwargs)
        except TypeError as exc:
            return f"参数错误: {name}: {exc}"
