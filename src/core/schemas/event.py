from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from core.schemas.common import new_id, utc_now


class RuntimeEvent(BaseModel):
    id: str = Field(default_factory=new_id)
    session_id: str
    event_type: str
    channel: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
