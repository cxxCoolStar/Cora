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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class MessageRecord(Base):
    __tablename__ = "clawbot_messages"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("clawbot_sessions.id"), index=True)
    role: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)


class ItemRecord(Base):
    __tablename__ = "clawbot_items"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("clawbot_sessions.id"), index=True)
    source_message_id: Mapped[str] = mapped_column(ForeignKey("clawbot_messages.id"), index=True)
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


class ClarificationStateRecord(Base):
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
