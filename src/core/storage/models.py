from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON


def new_id() -> str:
    return str(uuid4())


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class SessionRecord(Base):
    __tablename__ = "clawbot_sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    session_kind: Mapped[str] = mapped_column(String(32), default="conversation", index=True)
    parent_session_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SessionSummaryRecord(Base):
    __tablename__ = "clawbot_session_summaries"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("clawbot_sessions.id"), index=True, unique=True)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)


class MessageRecord(Base):
    __tablename__ = "clawbot_messages"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("clawbot_sessions.id"), index=True)
    role: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)


class SourceEventRecord(Base):
    __tablename__ = "clawbot_source_events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("clawbot_sessions.id"), index=True)
    source_message_id: Mapped[str | None] = mapped_column(ForeignKey("clawbot_messages.id"), nullable=True, index=True)
    channel: Mapped[str] = mapped_column(String(32), default="chat", index=True)
    external_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    external_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    raw_text: Mapped[str] = mapped_column(Text, default="")
    original_file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stored_file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ItemRecord(Base):
    __tablename__ = "clawbot_items"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("clawbot_sessions.id"), index=True)
    source_message_id: Mapped[str] = mapped_column(ForeignKey("clawbot_messages.id"), index=True)
    source_event_id: Mapped[str | None] = mapped_column(ForeignKey("clawbot_source_events.id"), nullable=True, index=True)
    item_type: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(255))
    raw_content: Mapped[str] = mapped_column(Text)
    normalized_text: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    locator_hint: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    document_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    version: Mapped[int] = mapped_column(default=1)
    is_current: Mapped[int] = mapped_column(default=1, index=True)
    is_deleted: Mapped[int] = mapped_column(default=0, index=True)
    superseded_by_item_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class TopicRecord(Base):
    __tablename__ = "clawbot_topics"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("clawbot_sessions.id"), index=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    slug: Mapped[str] = mapped_column(String(255), index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    tags_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)


class TopicItemRecord(Base):
    __tablename__ = "clawbot_topic_items"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    topic_id: Mapped[str] = mapped_column(ForeignKey("clawbot_topics.id"), index=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("clawbot_items.id"), index=True)
    confidence: Mapped[str] = mapped_column(String(32), default="medium")
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)


class TopicActivityRecord(Base):
    __tablename__ = "clawbot_topic_activity"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    topic_id: Mapped[str] = mapped_column(ForeignKey("clawbot_topics.id"), index=True)
    item_id: Mapped[str | None] = mapped_column(ForeignKey("clawbot_items.id"), nullable=True, index=True)
    activity_type: Mapped[str] = mapped_column(String(64), index=True)
    message: Mapped[str] = mapped_column(Text, default="")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)


class PendingStateRecord(Base):
    __tablename__ = "clawbot_clarification_states"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("clawbot_sessions.id"), index=True)
    source_message_id: Mapped[str] = mapped_column(ForeignKey("clawbot_messages.id"), index=True)
    question: Mapped[str] = mapped_column(Text)
    candidate_intents_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    pending_payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class UserSignalRecord(Base):
    __tablename__ = "clawbot_user_signals"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("clawbot_sessions.id"), index=True)
    item_id: Mapped[str | None] = mapped_column(ForeignKey("clawbot_items.id"), nullable=True, index=True)
    signal_type: Mapped[str] = mapped_column(String(64), index=True)
    signal_value: Mapped[str] = mapped_column(String(255), index=True)
    confidence: Mapped[str] = mapped_column(String(32), default="medium")
    source: Mapped[str] = mapped_column(String(64), default="ingestion")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)


class ChannelSessionMapRecord(Base):
    __tablename__ = "clawbot_channel_session_map"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    channel: Mapped[str] = mapped_column(String(32), index=True)
    external_user_id: Mapped[str] = mapped_column(String(255), index=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("clawbot_sessions.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    session_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_interaction_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_reset_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ChannelEventRecord(Base):
    __tablename__ = "clawbot_channel_events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    channel: Mapped[str] = mapped_column(String(32), index=True)
    external_event_id: Mapped[str] = mapped_column(String(255), index=True)
    external_user_id: Mapped[str] = mapped_column(String(255), index=True)
    status: Mapped[str] = mapped_column(String(32), default="processed", index=True)
    session_id: Mapped[str | None] = mapped_column(ForeignKey("clawbot_sessions.id"), nullable=True, index=True)
    reply_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)


class AgentRunRecordModel(Base):
    __tablename__ = "clawbot_agent_runs"

    run_id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("clawbot_sessions.id"), index=True)
    source_message_id: Mapped[str] = mapped_column(String, index=True)
    harness_id: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    outcome: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    trace_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    parent_run_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    agent_role: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    failure_category: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    cleanup_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    steps: Mapped[int | None] = mapped_column(nullable=True)
    budget_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    input_metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    trace_events_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ScheduledTaskRecord(Base):
    __tablename__ = "clawbot_scheduled_tasks"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("clawbot_sessions.id"), index=True)
    owner_external_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    prompt_text: Mapped[str] = mapped_column(Text)
    schedule_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    schedule_display: Mapped[str] = mapped_column(String(255), default="")
    delivery_kind: Mapped[str] = mapped_column(String(32), default="session_channel")
    enabled: Mapped[int] = mapped_column(default=1, index=True)
    state: Mapped[str] = mapped_column(String(32), default="scheduled", index=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_delivery_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_reply_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
