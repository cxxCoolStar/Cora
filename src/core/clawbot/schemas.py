from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class CreateSessionResponse(BaseModel):
    session_id: str


class ItemSummaryResponse(BaseModel):
    id: str
    item_type: Literal["text_note", "link", "document", "image", "file_upload"]
    title: str
    summary: str
    created_at: datetime


class ItemDetailResponse(ItemSummaryResponse):
    normalized_text: str
    locator_hint: str | None = None


class TurnResponse(BaseModel):
    reply: str
    status: str
    disposition: str
    action: str
    item_id: str | None = None
    needs_clarification: bool = False
    artifacts: list[dict] = Field(default_factory=list)
    trace: list[dict] = Field(default_factory=list)
    decision_source: str | None = None


class DeleteItemResponse(BaseModel):
    reply: str
    action: str = "delete"
    item_id: str


class MessageDebugResponse(BaseModel):
    id: str
    role: str
    content: str
    created_at: datetime

class UserSignalDebugResponse(BaseModel):
    id: str
    signal_type: str
    signal_value: str
    confidence: str
    source: str
    created_at: datetime


class UserProfileSection(BaseModel):
    name: str
    values: list[str]


class TopicDebugResponse(BaseModel):
    id: str
    name: str
    slug: str
    summary: str
    tags: list[str]
    created_at: datetime


class DecisionDebugResponse(BaseModel):
    action: str
    confidence: str
    reason: str
    source: str


class SessionDebugResponse(BaseModel):
    session_id: str
    session_kind: str = "conversation"
    parent_session_id: str | None = None
    session_metadata: dict = Field(default_factory=dict)
    created_at: datetime
    messages: list[MessageDebugResponse]
    items: list[ItemDetailResponse]
    user_signals: list[UserSignalDebugResponse]
    user_profile: list[UserProfileSection]
    topics: list[TopicDebugResponse]
    recent_decisions: list[DecisionDebugResponse]
