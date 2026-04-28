from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from core.schemas.common import new_id, utc_now


class Message(BaseModel):
    id: str = Field(default_factory=new_id)
    session_id: str
    role: Literal["system", "user", "assistant", "tool"]
    channel: str = "chat"
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)

    @classmethod
    def system(cls, *, session_id: str, content: str, channel: str = "system") -> "Message":
        return cls(session_id=session_id, role="system", content=content, channel=channel)

    @classmethod
    def user(cls, *, session_id: str, content: str, channel: str = "chat") -> "Message":
        return cls(session_id=session_id, role="user", content=content, channel=channel)

    @classmethod
    def assistant(cls, *, session_id: str, content: str, channel: str = "chat") -> "Message":
        return cls(session_id=session_id, role="assistant", content=content, channel=channel)

    @classmethod
    def tool(
        cls,
        *,
        session_id: str,
        content: str,
        name: str,
        tool_call_id: str,
        channel: str = "tool",
        metadata: dict[str, Any] | None = None,
    ) -> "Message":
        return cls(
            session_id=session_id,
            role="tool",
            content=content,
            name=name,
            tool_call_id=tool_call_id,
            channel=channel,
            metadata=metadata or {},
        )
