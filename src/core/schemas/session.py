from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from core.schemas.common import new_id, utc_now


class Session(BaseModel):
    id: str = Field(default_factory=new_id)
    agent_name: str
    status: Literal["active", "archived"] = "active"
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
