from __future__ import annotations

from typing import Any

from sqlalchemy import desc, select

from core.storage.db import DatabaseManager
from core.storage.models import (
    ChannelEventRecord,
    ChannelSessionMapRecord,
    ClarificationStateRecord,
    ItemChunkRecord,
    ItemRecord,
    MessageRecord,
    SessionRecord,
    UserSignalRecord,
)


class SessionRepository:
    def __init__(self, database: DatabaseManager) -> None:
        self.database = database

    def create(self) -> SessionRecord:
        with self.database.session() as session:
            record = SessionRecord()
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    def get(self, session_id: str) -> SessionRecord:
        with self.database.session() as session:
            record = session.get(SessionRecord, session_id)
            if record is None:
                raise KeyError(f"Session not found: {session_id}")
            return record

    def list_recent(self, *, limit: int = 20) -> list[SessionRecord]:
        with self.database.session() as session:
            stmt = select(SessionRecord).order_by(desc(SessionRecord.created_at)).limit(limit)
            return list(session.scalars(stmt))


class MessageRepository:
    def __init__(self, database: DatabaseManager) -> None:
        self.database = database

    def add_user_message(self, *, session_id: str, content: str) -> MessageRecord:
        return self._create(session_id=session_id, role="user", content=content, metadata={})

    def add_assistant_message(self, *, session_id: str, content: str, metadata: dict[str, Any] | None = None) -> MessageRecord:
        return self._create(session_id=session_id, role="assistant", content=content, metadata=metadata or {})

    def _create(self, *, session_id: str, role: str, content: str, metadata: dict[str, Any]) -> MessageRecord:
        with self.database.session() as session:
            record = MessageRecord(session_id=session_id, role=role, content=content, metadata_json=metadata)
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    def list_by_session(self, *, session_id: str) -> list[MessageRecord]:
        with self.database.session() as session:
            stmt = select(MessageRecord).where(MessageRecord.session_id == session_id).order_by(MessageRecord.created_at)
            return list(session.scalars(stmt))

    def get_latest_assistant_context(self, *, session_id: str) -> dict[str, Any] | None:
        """Return the most recent assistant message context blob, if any."""
        with self.database.session() as session:
            stmt = (
                select(MessageRecord)
                .where(MessageRecord.session_id == session_id, MessageRecord.role == "assistant")
                .order_by(desc(MessageRecord.created_at))
                .limit(10)
            )
            records = list(session.scalars(stmt))
        for record in records:
            meta = record.metadata_json or {}
            ctx = meta.get("context")
            if isinstance(ctx, dict) and ctx:
                return dict(ctx)
        return None


class ItemRepository:
    def __init__(self, database: DatabaseManager) -> None:
        self.database = database

    def create(
        self,
        *,
        session_id: str,
        source_message_id: str,
        item_type: str,
        title: str,
        raw_content: str,
        normalized_text: str,
        summary: str,
        metadata: dict[str, Any],
        locator_hint: str | None,
        document_key: str | None = None,
        version: int = 1,
        is_current: int = 1,
        superseded_by_item_id: str | None = None,
    ) -> ItemRecord:
        with self.database.session() as session:
            record = ItemRecord(
                session_id=session_id,
                source_message_id=source_message_id,
                item_type=item_type,
                title=title,
                raw_content=raw_content,
                normalized_text=normalized_text,
                summary=summary,
                metadata_json=metadata,
                locator_hint=locator_hint,
                document_key=document_key,
                version=version,
                is_current=is_current,
                superseded_by_item_id=superseded_by_item_id,
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    def list_by_session(self, *, session_id: str, current_only: bool = False) -> list[ItemRecord]:
        with self.database.session() as session:
            stmt = select(ItemRecord).where(ItemRecord.session_id == session_id)
            if current_only:
                stmt = stmt.where(ItemRecord.is_current == 1)
            stmt = stmt.order_by(desc(ItemRecord.created_at))
            return list(session.scalars(stmt))

    def list_all(self, *, current_only: bool = False) -> list[ItemRecord]:
        with self.database.session() as session:
            stmt = select(ItemRecord)
            if current_only:
                stmt = stmt.where(ItemRecord.is_current == 1)
            stmt = stmt.order_by(desc(ItemRecord.created_at))
            return list(session.scalars(stmt))

    def find_current_by_document_key(self, *, session_id: str, document_key: str) -> ItemRecord | None:
        with self.database.session() as session:
            stmt = (
                select(ItemRecord)
                .where(
                    ItemRecord.session_id == session_id,
                    ItemRecord.document_key == document_key,
                    ItemRecord.is_current == 1,
                )
                .order_by(desc(ItemRecord.created_at))
                .limit(1)
            )
            return session.scalar(stmt)

    def mark_superseded(self, *, item_id: str, superseded_by_item_id: str) -> None:
        with self.database.session() as session:
            record = session.get(ItemRecord, item_id)
            if record is None:
                raise KeyError(f"Item not found: {item_id}")
            record.is_current = 0
            record.superseded_by_item_id = superseded_by_item_id
            session.commit()

    def get_any(self, *, item_id: str) -> ItemRecord:
        with self.database.session() as session:
            record = session.get(ItemRecord, item_id)
            if record is None:
                raise KeyError(f"Item not found: {item_id}")
            return record

    def get(self, *, item_id: str, session_id: str) -> ItemRecord:
        with self.database.session() as session:
            stmt = select(ItemRecord).where(ItemRecord.id == item_id, ItemRecord.session_id == session_id)
            record = session.scalar(stmt)
            if record is None:
                raise KeyError(f"Item not found: {item_id}")
            return record

    def search_latest_by_text(self, *, session_id: str | None, query: str) -> ItemRecord | None:
        lowered = query.lower()
        with self.database.session() as session:
            stmt = select(ItemRecord)
            if session_id is not None:
                stmt = stmt.where(ItemRecord.session_id == session_id)
            stmt = stmt.order_by(desc(ItemRecord.created_at))
            records = list(session.scalars(stmt))
        for record in records:
            haystack = " ".join([record.title, record.summary, record.normalized_text]).lower()
            compact_query = lowered.replace(" ", "")
            compact_haystack = haystack.replace(" ", "")
            if compact_query and compact_query in compact_haystack:
                return record
            if any(token for token in lowered.split() if token in haystack):
                return record
        return records[0] if records else None


class ItemChunkRepository:
    def __init__(self, database: DatabaseManager) -> None:
        self.database = database

    def create(self, *, item_id: str, chunk_index: int, content: str, metadata: dict[str, Any]) -> ItemChunkRecord:
        with self.database.session() as session:
            record = ItemChunkRecord(
                item_id=item_id,
                chunk_index=chunk_index,
                content=content,
                metadata_json=metadata,
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    def list_by_item_ids(self, *, item_ids: list[str]) -> list[ItemChunkRecord]:
        if not item_ids:
            return []
        with self.database.session() as session:
            stmt = (
                select(ItemChunkRecord)
                .where(ItemChunkRecord.item_id.in_(item_ids))
                .order_by(ItemChunkRecord.item_id, ItemChunkRecord.chunk_index)
            )
            return list(session.scalars(stmt))


class ClarificationRepository:
    def __init__(self, database: DatabaseManager) -> None:
        self.database = database

    def create(
        self,
        *,
        session_id: str,
        source_message_id: str,
        question: str,
        candidate_intents: list[str],
        pending_payload: dict[str, Any],
    ) -> ClarificationStateRecord:
        with self.database.session() as session:
            record = ClarificationStateRecord(
                session_id=session_id,
                source_message_id=source_message_id,
                question=question,
                candidate_intents_json=candidate_intents,
                pending_payload_json=pending_payload,
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    def get_latest_pending(self, *, session_id: str) -> ClarificationStateRecord | None:
        with self.database.session() as session:
            stmt = (
                select(ClarificationStateRecord)
                .where(
                    ClarificationStateRecord.session_id == session_id,
                    ClarificationStateRecord.status == "pending",
                )
                .order_by(desc(ClarificationStateRecord.created_at))
                .limit(1)
            )
            return session.scalar(stmt)

    def resolve(self, *, clarification_id: str, status: str) -> None:
        with self.database.session() as session:
            record = session.get(ClarificationStateRecord, clarification_id)
            if record is None:
                raise KeyError(f"Clarification not found: {clarification_id}")
            record.status = status
            session.commit()


class UserSignalRepository:
    def __init__(self, database: DatabaseManager) -> None:
        self.database = database

    def create(
        self,
        *,
        session_id: str,
        item_id: str | None,
        signal_type: str,
        signal_value: str,
        confidence: str = "medium",
        source: str = "ingestion",
        metadata: dict[str, Any] | None = None,
    ) -> UserSignalRecord:
        with self.database.session() as session:
            record = UserSignalRecord(
                session_id=session_id,
                item_id=item_id,
                signal_type=signal_type,
                signal_value=signal_value,
                confidence=confidence,
                source=source,
                metadata_json=metadata or {},
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    def list_by_session(self, *, session_id: str, limit: int = 50) -> list[UserSignalRecord]:
        with self.database.session() as session:
            stmt = (
                select(UserSignalRecord)
                .where(UserSignalRecord.session_id == session_id)
                .order_by(desc(UserSignalRecord.created_at))
                .limit(limit)
            )
            return list(session.scalars(stmt))


class ChannelSessionMapRepository:
    def __init__(self, database: DatabaseManager) -> None:
        self.database = database

    def get_session_id(self, *, channel: str, external_user_id: str) -> str | None:
        with self.database.session() as session:
            stmt = (
                select(ChannelSessionMapRecord)
                .where(
                    ChannelSessionMapRecord.channel == channel,
                    ChannelSessionMapRecord.external_user_id == external_user_id,
                )
                .order_by(desc(ChannelSessionMapRecord.updated_at))
                .limit(1)
            )
            record = session.scalar(stmt)
            return record.session_id if record is not None else None

    def upsert(self, *, channel: str, external_user_id: str, session_id: str) -> ChannelSessionMapRecord:
        with self.database.session() as session:
            stmt = (
                select(ChannelSessionMapRecord)
                .where(
                    ChannelSessionMapRecord.channel == channel,
                    ChannelSessionMapRecord.external_user_id == external_user_id,
                )
                .order_by(desc(ChannelSessionMapRecord.updated_at))
                .limit(1)
            )
            record = session.scalar(stmt)
            if record is None:
                record = ChannelSessionMapRecord(channel=channel, external_user_id=external_user_id, session_id=session_id)
                session.add(record)
            else:
                record.session_id = session_id
            session.commit()
            session.refresh(record)
            return record


class ChannelEventRepository:
    def __init__(self, database: DatabaseManager) -> None:
        self.database = database

    def get(self, *, channel: str, external_event_id: str) -> ChannelEventRecord | None:
        with self.database.session() as session:
            stmt = (
                select(ChannelEventRecord)
                .where(
                    ChannelEventRecord.channel == channel,
                    ChannelEventRecord.external_event_id == external_event_id,
                )
                .order_by(desc(ChannelEventRecord.created_at))
                .limit(1)
            )
            return session.scalar(stmt)

    def create(
        self,
        *,
        channel: str,
        external_event_id: str,
        external_user_id: str,
        status: str,
        session_id: str | None,
        reply_preview: str | None,
    ) -> ChannelEventRecord:
        with self.database.session() as session:
            record = ChannelEventRecord(
                channel=channel,
                external_event_id=external_event_id,
                external_user_id=external_user_id,
                status=status,
                session_id=session_id,
                reply_preview=reply_preview,
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            return record
