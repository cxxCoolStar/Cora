from __future__ import annotations

from core.schemas.tool import ToolResult, ToolSpec
from core.tools.base import Tool


class EchoTool(Tool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="echo",
            description="Return the input text unchanged.",
            input_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                },
                "required": ["text"],
            },
        )

    def invoke(self, arguments: dict[str, object]) -> ToolResult:
        text = str(arguments.get("text", ""))
        return ToolResult(success=True, content=text)
