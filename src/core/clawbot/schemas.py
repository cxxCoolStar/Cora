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


class AgentRunTraceEventResponse(BaseModel):
    event_type: str
    run_id: str
    session_id: str
    sequence: int
    severity: str
    metadata: dict = Field(default_factory=dict)


class AgentRunSummaryResponse(BaseModel):
    run_id: str
    session_id: str
    source_message_id: str
    harness_id: str
    status: str
    outcome: str | None = None
    trace_id: str | None = None
    parent_run_id: str | None = None
    agent_role: str | None = None
    failure_category: str | None = None
    cleanup_status: str | None = None
    steps: int | None = None
    started_at: datetime
    completed_at: datetime | None = None


class HitlRequestResponse(BaseModel):
    hitl_id: str
    run_id: str
    session_id: str
    tool_name: str
    status: str
    reason: str
    policy_profile: str | None = None
    tool_risk: str = "medium"
    tool_arguments: dict = Field(default_factory=dict)
    created_at: str
    resolved_at: str | None = None
    metadata: dict = Field(default_factory=dict)


class HitlActionResponse(BaseModel):
    hitl: HitlRequestResponse
    turn: TurnResponse


class AgentRunDetailResponse(AgentRunSummaryResponse):
    budget: dict = Field(default_factory=dict)
    input_metadata: dict = Field(default_factory=dict)
    metadata: dict = Field(default_factory=dict)
    error: str | None = None
    trace_events: list[AgentRunTraceEventResponse] = Field(default_factory=list)


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
