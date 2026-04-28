from __future__ import annotations

from datetime import UTC, datetime

from core.schemas.tool import ToolResult, ToolSpec
from core.tools.base import Tool


class GetTimeTool(Tool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="get_time",
            description="Return the current UTC time in ISO 8601 format.",
            input_schema={"type": "object", "properties": {}},
        )

    def invoke(self, arguments: dict[str, object]) -> ToolResult:
        _ = arguments
        return ToolResult(success=True, content=datetime.now(UTC).isoformat())
