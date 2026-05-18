from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class WechatInboundEvent:
    event_id: str
    user_id: str
    text: str | None = None
    context_token: str | None = None
    file_name: str | None = None
    file_path: str | None = None
    file_mime: str | None = None
    media_download_failed: bool = False
    media_download_error: str | None = None
    create_time_ms: int | None = None
    conversation_id: str | None = None
    raw_payload: dict[str, Any] | None = None


@dataclass(slots=True)
class WechatHandleResult:
    deduplicated: bool
    session_id: str
    reply: str
    action: str
