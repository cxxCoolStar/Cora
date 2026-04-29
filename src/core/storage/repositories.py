from __future__ import annotations

from typing import Any

from sqlalchemy import desc, select

from core.storage.db import DatabaseManager
from core.storage.models import ClarificationStateRecord, ItemChunkRecord, ItemRecord, MessageRecord, SessionRecord


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
        return self._create(session_id=session_id, role="user", content=content)

    def add_assistant_message(self, *, session_id: str, content: str) -> MessageRecord:
        return self._create(session_id=session_id, role="assistant", content=content)

    def _create(self, *, session_id: str, role: str, content: str) -> MessageRecord:
        with self.database.session() as session:
            record = MessageRecord(session_id=session_id, role=role, content=content)
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    def list_by_session(self, *, session_id: str) -> list[MessageRecord]:
        with self.database.session() as session:
            stmt = select(MessageRecord).where(MessageRecord.session_id == session_id).order_by(MessageRecord.created_at)
            return list(session.scalars(stmt))


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
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    def list_by_session(self, *, session_id: str) -> list[ItemRecord]:
        with self.database.session() as session:
            stmt = select(ItemRecord).where(ItemRecord.session_id == session_id).order_by(desc(ItemRecord.created_at))
            return list(session.scalars(stmt))

    def get(self, *, item_id: str, session_id: str) -> ItemRecord:
        with self.database.session() as session:
            stmt = select(ItemRecord).where(ItemRecord.id == item_id, ItemRecord.session_id == session_id)
            record = session.scalar(stmt)
            if record is None:
                raise KeyError(f"Item not found: {item_id}")
            return record

    def search_latest_by_text(self, *, session_id: str, query: str) -> ItemRecord | None:
        lowered = query.lower()
        with self.database.session() as session:
            stmt = select(ItemRecord).where(ItemRecord.session_id == session_id).order_by(desc(ItemRecord.created_at))
            records = list(session.scalars(stmt))
        for record in records:
            haystack = " ".join([record.title, record.summary, record.normalized_text]).lower()
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
