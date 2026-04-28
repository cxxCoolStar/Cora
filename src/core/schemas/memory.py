from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from core.schemas.common import utc_now


class MemoryRecord(BaseModel):
    id: str
    session_id: str
    memory_type: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
