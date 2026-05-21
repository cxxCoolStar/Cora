from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import UploadFile


@dataclass(slots=True)
class InboundEvent:
    channel: str
    external_event_id: str
    external_user_id: str
    text: str | None = None
    file_name: str | None = None


@dataclass(slots=True)
class ChannelTurnInput:
    channel: str
    session_id: str
    source_message_id: str
    external_user_id: str
    user_text: str | None
    raw_text: str | None
    upload: UploadFile | None
    source_metadata: dict[str, Any]
    delivery_available: bool = False
    platform_preset: str | None = None
