from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from core.schemas.common import new_id
from core.schemas.execution import ExecutionHints


class ToolSpec(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]
    toolset: str | None = None
    read_only: bool = False
    risk: str = "medium"
    allowed_roles: list[str] = Field(default_factory=list)
    requires_confirmation: bool = False
    requires_sandbox: bool = False


class ToolCall(BaseModel):
    id: str = Field(default_factory=new_id)
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    success: bool
    content: str
    status: str = "completed"
    disposition: str = "continue"
    action: str | None = None
    state_delta: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    hints: ExecutionHints = Field(default_factory=ExecutionHints)
    metadata: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
