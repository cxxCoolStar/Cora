from __future__ import annotations

from core.schemas.tool import ToolCall, ToolResult, ToolSpec
from core.tools.base import Tool


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.spec.name] = tool

    def list_specs(self) -> list[ToolSpec]:
        return [tool.spec for tool in self._tools.values()]

    def execute(self, tool_call: ToolCall) -> ToolResult:
        tool = self._tools.get(tool_call.tool_name)
        if tool is None:
            return ToolResult(
                success=False,
                content=f"Unknown tool: {tool_call.tool_name}",
                error="unknown_tool",
            )
        try:
            return tool.invoke(tool_call.arguments)
        except Exception as exc:  # pragma: no cover - defensive path
            return ToolResult(
                success=False,
                content=f"Tool `{tool_call.tool_name}` failed: {exc}",
                error=type(exc).__name__,
            )
