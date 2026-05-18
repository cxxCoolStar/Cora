from __future__ import annotations

from typing import Any

from sqlalchemy import desc, select

from core.storage.db import DatabaseManager
from core.storage.models import (
    ChannelEventRecord,
    ChannelSessionMapRecord,
    PendingStateRecord,
    ItemRecord,
    MessageRecord,
    SessionRecord,
    SessionSummaryRecord,
    SourceEventRecord,
    TopicActivityRecord,
    TopicItemRecord,
    TopicRecord,
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


class SessionSummaryRepository:
    def __init__(self, database: DatabaseManager) -> None:
        self.database = database

    def get_by_session(self, *, session_id: str) -> SessionSummaryRecord | None:
        with self.database.session() as session:
            stmt = (
                select(SessionSummaryRecord)
                .where(SessionSummaryRecord.session_id == session_id)
                .limit(1)
            )
            return session.scalar(stmt)

    def upsert(self, *, session_id: str, summary: dict[str, Any]) -> SessionSummaryRecord:
        with self.database.session() as session:
            stmt = (
                select(SessionSummaryRecord)
                .where(SessionSummaryRecord.session_id == session_id)
                .limit(1)
            )
            record = session.scalar(stmt)
            if record is None:
                record = SessionSummaryRecord(session_id=session_id, summary_json=summary)
                session.add(record)
            else:
                record.summary_json = summary
            session.commit()
            session.refresh(record)
            return record


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
        source_event_id: str | None,
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
                source_event_id=source_event_id,
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

    def list_by_session(self, *, session_id: str, current_only: bool = False, include_deleted: bool = False) -> list[ItemRecord]:
        with self.database.session() as session:
            stmt = select(ItemRecord).where(ItemRecord.session_id == session_id)
            if current_only:
                stmt = stmt.where(ItemRecord.is_current == 1)
            if not include_deleted:
                stmt = stmt.where(ItemRecord.is_deleted == 0)
            stmt = stmt.order_by(desc(ItemRecord.created_at))
            return list(session.scalars(stmt))

    def list_all(self, *, current_only: bool = False, include_deleted: bool = False) -> list[ItemRecord]:
        with self.database.session() as session:
            stmt = select(ItemRecord)
            if current_only:
                stmt = stmt.where(ItemRecord.is_current == 1)
            if not include_deleted:
                stmt = stmt.where(ItemRecord.is_deleted == 0)
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
                    ItemRecord.is_deleted == 0,
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

    def get_any(self, *, item_id: str, include_deleted: bool = False) -> ItemRecord:
        with self.database.session() as session:
            record = session.get(ItemRecord, item_id)
            if record is None or (not include_deleted and record.is_deleted):
                raise KeyError(f"Item not found: {item_id}")
            return record

    def get(self, *, item_id: str, session_id: str, include_deleted: bool = False) -> ItemRecord:
        with self.database.session() as session:
            stmt = select(ItemRecord).where(ItemRecord.id == item_id, ItemRecord.session_id == session_id)
            record = session.scalar(stmt)
            if record is None or (not include_deleted and record.is_deleted):
                raise KeyError(f"Item not found: {item_id}")
            return record

    def update_metadata(self, *, item_id: str, metadata: dict[str, Any]) -> ItemRecord:
        with self.database.session() as session:
            record = session.get(ItemRecord, item_id)
            if record is None:
                raise KeyError(f"Item not found: {item_id}")
            record.metadata_json = metadata
            session.commit()
            session.refresh(record)
            return record

    def search_latest_by_text(self, *, session_id: str | None, query: str) -> ItemRecord | None:
        lowered = query.lower()
        with self.database.session() as session:
            stmt = select(ItemRecord).where(ItemRecord.is_current == 1, ItemRecord.is_deleted == 0)
            if session_id is not None:
                stmt = stmt.where(ItemRecord.session_id == session_id)
            stmt = stmt.order_by(desc(ItemRecord.created_at))
            records = list(session.scalars(stmt))
        for record in records:
            metadata = record.metadata_json or {}
            metadata_values = " ".join(str(value) for value in metadata.values() if value is not None)
            haystack = " ".join(
                [
                    record.title,
                    record.summary,
                    record.normalized_text,
                    record.locator_hint or "",
                    metadata_values,
                ]
            ).lower()
            compact_query = lowered.replace(" ", "")
            compact_haystack = haystack.replace(" ", "")
            if compact_query and compact_query in compact_haystack:
                return record
            if any(token for token in lowered.split() if token in haystack):
                return record
        return None

    def list_current_by_ids(self, *, item_ids: list[str]) -> list[ItemRecord]:
        if not item_ids:
            return []
        with self.database.session() as session:
            stmt = (
                select(ItemRecord)
                .where(ItemRecord.id.in_(item_ids), ItemRecord.is_current == 1, ItemRecord.is_deleted == 0)
                .order_by(desc(ItemRecord.created_at))
            )
            return list(session.scalars(stmt))

    def soft_delete(self, *, item_id: str, session_id: str) -> ItemRecord:
        with self.database.session() as session:
            stmt = select(ItemRecord).where(ItemRecord.id == item_id, ItemRecord.session_id == session_id).limit(1)
            record = session.scalar(stmt)
            if record is None or record.is_deleted:
                raise KeyError(f"Item not found: {item_id}")
            record.is_deleted = 1
            record.is_current = 0
            session.commit()
            session.refresh(record)
            return record


class SourceEventRepository:
    def __init__(self, database: DatabaseManager) -> None:
        self.database = database

    def create(
        self,
        *,
        session_id: str,
        source_message_id: str | None,
        channel: str,
        event_type: str,
        raw_text: str = "",
        original_file_name: str | None = None,
        stored_file_path: str | None = None,
        mime_type: str | None = None,
        external_event_id: str | None = None,
        external_user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SourceEventRecord:
        with self.database.session() as session:
            record = SourceEventRecord(
                session_id=session_id,
                source_message_id=source_message_id,
                channel=channel,
                external_event_id=external_event_id,
                external_user_id=external_user_id,
                event_type=event_type,
                raw_text=raw_text,
                original_file_name=original_file_name,
                stored_file_path=stored_file_path,
                mime_type=mime_type,
                metadata_json=metadata or {},
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    def list_by_session(self, *, session_id: str, limit: int = 20) -> list[SourceEventRecord]:
        with self.database.session() as session:
            stmt = (
                select(SourceEventRecord)
                .where(SourceEventRecord.session_id == session_id)
                .order_by(desc(SourceEventRecord.created_at))
                .limit(limit)
            )
            return list(session.scalars(stmt))

    def get_any(self, *, event_id: str) -> SourceEventRecord:
        with self.database.session() as session:
            record = session.get(SourceEventRecord, event_id)
            if record is None:
                raise KeyError(f"Source event not found: {event_id}")
            return record

    def update_upload_reference(
        self,
        *,
        event_id: str,
        stored_file_path: str,
        original_file_name: str | None = None,
        mime_type: str | None = None,
    ) -> SourceEventRecord:
        with self.database.session() as session:
            record = session.get(SourceEventRecord, event_id)
            if record is None:
                raise KeyError(f"Source event not found: {event_id}")
            record.stored_file_path = stored_file_path
            if original_file_name:
                record.original_file_name = original_file_name
            if mime_type:
                record.mime_type = mime_type
            session.commit()
            session.refresh(record)
            return record

class TopicRepository:
    def __init__(self, database: DatabaseManager) -> None:
        self.database = database

    def create(
        self,
        *,
        session_id: str,
        name: str,
        slug: str,
        summary: str,
        tags: list[str],
        metadata: dict[str, Any],
    ) -> TopicRecord:
        with self.database.session() as session:
            record = TopicRecord(
                session_id=session_id,
                name=name,
                slug=slug,
                summary=summary,
                tags_json=tags,
                metadata_json=metadata,
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    def find_by_slug(self, *, slug: str) -> TopicRecord | None:
        with self.database.session() as session:
            stmt = (
                select(TopicRecord)
                .where(TopicRecord.slug == slug)
                .order_by(desc(TopicRecord.updated_at))
                .limit(1)
            )
            return session.scalar(stmt)

    def list_by_session(self, *, session_id: str) -> list[TopicRecord]:
        with self.database.session() as session:
            stmt = select(TopicRecord).where(TopicRecord.session_id == session_id).order_by(desc(TopicRecord.updated_at))
            return list(session.scalars(stmt))

    def list_all(self) -> list[TopicRecord]:
        with self.database.session() as session:
            stmt = select(TopicRecord).order_by(desc(TopicRecord.updated_at))
            return list(session.scalars(stmt))

    def search(self, *, query: str, session_id: str, limit: int = 3) -> list[TopicRecord]:
        lowered = query.lower()
        with self.database.session() as session:
            stmt = select(TopicRecord).where(TopicRecord.session_id == session_id).order_by(desc(TopicRecord.updated_at))
            topics = list(session.scalars(stmt))
        scored: list[tuple[TopicRecord, int]] = []
        for topic in topics:
            haystack = " ".join([topic.name or "", topic.slug or "", topic.summary or "", " ".join(topic.tags_json or [])]).lower()
            score = 0
            compact_query = "".join(lowered.split())
            compact_haystack = haystack.replace(" ", "")
            if compact_query and (topic.name.lower() in compact_query or topic.slug.lower() in compact_query):
                score += max(len(topic.name), len(topic.slug)) ** 2 * 3
            if compact_query and compact_query in compact_haystack:
                score += len(compact_query) ** 2
            for token in [t for t in lowered.replace("，", " ").split() if t]:
                if token in haystack:
                    score += len(token) ** 2
            if query.lower() in haystack:
                score += len(query.strip()) ** 2
            if score > 0:
                scored.append((topic, score))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return [topic for topic, _ in scored[:limit]]

    def update_summary_and_tags(self, *, topic_id: str, summary: str, tags: list[str]) -> None:
        with self.database.session() as session:
            record = session.get(TopicRecord, topic_id)
            if record is None:
                raise KeyError(f"Topic not found: {topic_id}")
            record.summary = summary
            record.tags_json = tags
            session.commit()


class TopicItemRepository:
    def __init__(self, database: DatabaseManager) -> None:
        self.database = database

    def link_item(self, *, topic_id: str, item_id: str, confidence: str, reason: str) -> TopicItemRecord:
        with self.database.session() as session:
            stmt = (
                select(TopicItemRecord)
                .where(TopicItemRecord.topic_id == topic_id, TopicItemRecord.item_id == item_id)
                .limit(1)
            )
            existing = session.scalar(stmt)
            if existing is not None:
                return existing
            record = TopicItemRecord(topic_id=topic_id, item_id=item_id, confidence=confidence, reason=reason)
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    def list_item_ids_for_topic(self, *, topic_id: str, limit: int = 20) -> list[str]:
        with self.database.session() as session:
            stmt = (
                select(TopicItemRecord)
                .where(TopicItemRecord.topic_id == topic_id)
                .order_by(desc(TopicItemRecord.created_at))
                .limit(limit)
            )
            return [record.item_id for record in session.scalars(stmt)]

    def list_topics_for_item(self, *, item_id: str) -> list[TopicItemRecord]:
        with self.database.session() as session:
            stmt = select(TopicItemRecord).where(TopicItemRecord.item_id == item_id).order_by(desc(TopicItemRecord.created_at))
            return list(session.scalars(stmt))


class TopicActivityRepository:
    def __init__(self, database: DatabaseManager) -> None:
        self.database = database

    def create(
        self,
        *,
        topic_id: str,
        item_id: str | None,
        activity_type: str,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> TopicActivityRecord:
        with self.database.session() as session:
            record = TopicActivityRecord(
                topic_id=topic_id,
                item_id=item_id,
                activity_type=activity_type,
                message=message,
                metadata_json=metadata or {},
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            return record


class PendingStateRepository:
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
    ) -> PendingStateRecord:
        with self.database.session() as session:
            record = PendingStateRecord(
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

    def get_latest_pending(self, *, session_id: str) -> PendingStateRecord | None:
        with self.database.session() as session:
            stmt = (
                select(PendingStateRecord)
                .where(
                    PendingStateRecord.session_id == session_id,
                    PendingStateRecord.status == "pending",
                )
                .order_by(desc(PendingStateRecord.created_at))
                .limit(1)
            )
            return session.scalar(stmt)

    def resolve(self, *, pending_state_id: str, status: str) -> None:
        with self.database.session() as session:
            record = session.get(PendingStateRecord, pending_state_id)
            if record is None:
                raise KeyError(f"Pending state not found: {pending_state_id}")
            record.status = status
            session.commit()

    def update_pending(
        self,
        *,
        pending_state_id: str,
        pending_payload: dict[str, Any] | None = None,
        question: str | None = None,
        candidate_intents: list[str] | None = None,
    ) -> PendingStateRecord:
        with self.database.session() as session:
            record = session.get(PendingStateRecord, pending_state_id)
            if record is None:
                raise KeyError(f"Pending state not found: {pending_state_id}")
            if pending_payload is not None:
                record.pending_payload_json = pending_payload
            if question is not None:
                record.question = question
            if candidate_intents is not None:
                record.candidate_intents_json = candidate_intents
            session.commit()
            session.refresh(record)
            return record


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

    def get_external_user_id(self, *, channel: str, session_id: str) -> str | None:
        """Get external user ID by channel and session ID (reverse lookup)."""
        with self.database.session() as session:
            stmt = (
                select(ChannelSessionMapRecord)
                .where(
                    ChannelSessionMapRecord.channel == channel,
                    ChannelSessionMapRecord.session_id == session_id,
                )
                .order_by(desc(ChannelSessionMapRecord.updated_at))
                .limit(1)
            )
            record = session.scalar(stmt)
            return record.external_user_id if record is not None else None


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
