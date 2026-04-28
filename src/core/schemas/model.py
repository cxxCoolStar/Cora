from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from core.schemas.tool import ToolCall


class ModelResponse(BaseModel):
    assistant_text: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    raw_response: dict[str, Any] = Field(default_factory=dict)
    usage: dict[str, Any] = Field(default_factory=dict)
