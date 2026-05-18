from __future__ import annotations

from dataclasses import dataclass
import inspect
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from fastapi import UploadFile

if TYPE_CHECKING:
    from core.clawbot.planner import ToolPlan
    from core.clawbot.tools import ToolExecutionResult


ToolHandler = Callable[[Any, "ToolInvocation"], "ToolExecutionResult" | Awaitable["ToolExecutionResult"]]


@dataclass(slots=True)
class ToolInvocation:
    session_id: str
    source_message_id: str
    plan: "ToolPlan"
    text: str | None
    upload: UploadFile | None
    context: dict[str, Any]


@dataclass(slots=True)
class ToolSpec:
    name: str
    toolset: str
    description: str
    schema: dict[str, Any]
    handler: ToolHandler
    is_agent_stateful: bool = False
    read_only: bool = False
    requires_confirmation: bool = False


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def get_many(self, names: list[str]) -> list[ToolSpec]:
        return [spec for name in names if (spec := self.get(name)) is not None]

    def get_by_toolsets(self, toolsets: list[str]) -> list[ToolSpec]:
        allowed = set(toolsets)
        return [spec for spec in self._tools.values() if spec.toolset in allowed]

    def names_by_toolsets(self, toolsets: list[str]) -> list[str]:
        return [spec.name for spec in self.get_by_toolsets(toolsets)]

    def names(self) -> list[str]:
        return list(self._tools.keys())

    async def dispatch(self, executor: Any, *, name: str, invocation: ToolInvocation) -> "ToolExecutionResult":
        spec = self.get(name)
        if spec is None:
            raise KeyError(f"Unknown tool: {name}")
        result = spec.handler(executor, invocation)
        if inspect.isawaitable(result):
            return await result
        return result


registry = ToolRegistry()
