from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from core.schemas.common import new_id


class ToolSpec(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]


class ToolCall(BaseModel):
    id: str = Field(default_factory=new_id)
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    success: bool
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
