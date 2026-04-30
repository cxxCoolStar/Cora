from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class CreateSessionResponse(BaseModel):
    session_id: str


class ItemSummaryResponse(BaseModel):
    id: str
    item_type: Literal["text_note", "link", "document"]
    title: str
    summary: str
    created_at: datetime


class ItemDetailResponse(ItemSummaryResponse):
    normalized_text: str
    locator_hint: str | None = None


class IngestResponse(BaseModel):
    reply: str
    action: str
    item_id: str | None = None
    needs_clarification: bool = False
    decision_source: str | None = None


class SessionReplyResponse(BaseModel):
    reply: str
    action: str = "chat"


class MessageDebugResponse(BaseModel):
    id: str
    role: str
    content: str
    created_at: datetime


class ChunkDebugResponse(BaseModel):
    id: str
    item_id: str
    chunk_index: int
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
    created_at: datetime
    messages: list[MessageDebugResponse]
    items: list[ItemDetailResponse]
    chunks: list[ChunkDebugResponse]
    user_signals: list[UserSignalDebugResponse]
    user_profile: list[UserProfileSection]
    topics: list[TopicDebugResponse]
    recent_decisions: list[DecisionDebugResponse]
