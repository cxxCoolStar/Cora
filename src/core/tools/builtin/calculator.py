from __future__ import annotations

from core.schemas.tool import ToolResult, ToolSpec
from core.tools.base import Tool


class CalculatorTool(Tool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="calculator",
            description="Evaluate a simple arithmetic expression using Python arithmetic operators.",
            input_schema={
                "type": "object",
                "properties": {
                    "expression": {"type": "string"},
                },
                "required": ["expression"],
            },
        )

    def invoke(self, arguments: dict[str, object]) -> ToolResult:
        expression = str(arguments.get("expression", "")).strip()
        if not expression:
            return ToolResult(success=False, content="Missing `expression`.", error="validation_error")
        allowed_chars = set("0123456789+-*/(). ")
        if any(char not in allowed_chars for char in expression):
            return ToolResult(success=False, content="Expression contains unsupported characters.", error="validation_error")
        result = eval(expression, {"__builtins__": {}}, {})
        return ToolResult(success=True, content=str(result))
