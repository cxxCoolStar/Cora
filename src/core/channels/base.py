from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class InboundEvent:
    channel: str
    external_event_id: str
    external_user_id: str
    text: str | None = None
    file_name: str | None = None

