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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ItemChunkRecord(Base):
    __tablename__ = "clawbot_item_chunks"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    item_id: Mapped[str] = mapped_column(ForeignKey("clawbot_items.id"), index=True)
    chunk_index: Mapped[int]
    content: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


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
