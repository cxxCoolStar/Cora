from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import desc, select

from core.storage.db import DatabaseManager
from core.agent.plan_execution_state import (
    StoredPlanExecution,
    stored_plan_execution_from_dict,
)
from core.agent.plan_store import StoredValidatedPlan
from core.agent.hitl_expiry import default_hitl_expires_at, is_hitl_expired
from core.schemas.plan import plan_spec_from_dict
from core.schemas.hitl import HitlRequest
from core.storage.models import (
    AgentRunRecordModel,
    HitlRequestModel,
    ChannelEventRecord,
    ChannelSessionMapRecord,
    PendingStateRecord,
    ItemRecord,
    MessageRecord,
    SessionPlanRecord,
    SessionRecord,
    SessionSummaryRecord,
    SourceEventRecord,
    ScheduledTaskRecord,
    TopicActivityRecord,
    TopicItemRecord,
    TopicRecord,
    UserSignalRecord,
    utc_now,
)
from core.agent.run_records import AgentRunRecord
from core.schemas.harness import HarnessRunInput, RunTraceEvent
from core.tasks.schedule import compute_next_run_at, format_schedule, normalize_schedule_input


class SessionRepository:
    def __init__(self, database: DatabaseManager) -> None:
        self.database = database

    def create(
        self,
        *,
        session_kind: str = "conversation",
        parent_session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SessionRecord:
        with self.database.session() as session:
            record = SessionRecord(
                session_kind=str(session_kind or "conversation").strip() or "conversation",
                parent_session_id=str(parent_session_id or "").strip() or None,
                metadata_json=dict(metadata or {}),
            )
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

    def list_recent(self, *, limit: int = 20, session_kind: str | None = None) -> list[SessionRecord]:
        with self.database.session() as session:
            stmt = select(SessionRecord)
            if session_kind:
                stmt = stmt.where(SessionRecord.session_kind == session_kind)
            stmt = stmt.order_by(desc(SessionRecord.created_at)).limit(limit)
            return list(session.scalars(stmt))

    def list_child_session_ids(
        self,
        *,
        parent_session_id: str,
        session_kind: str | None = None,
        limit: int = 20,
    ) -> list[str]:
        with self.database.session() as session:
            stmt = select(SessionRecord.id).where(SessionRecord.parent_session_id == parent_session_id)
            if session_kind:
                stmt = stmt.where(SessionRecord.session_kind == session_kind)
            stmt = stmt.order_by(desc(SessionRecord.created_at)).limit(limit)
            return [str(value) for value in session.scalars(stmt)]

    def get_parent_session_id(self, *, session_id: str) -> str | None:
        with self.database.session() as session:
            stmt = (
                select(SessionRecord.parent_session_id)
                .where(SessionRecord.id == session_id)
                .limit(1)
            )
            value = session.scalar(stmt)
            return str(value).strip() or None if value is not None else None


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

    def get_binding(self, *, channel: str, external_user_id: str) -> ChannelSessionMapRecord | None:
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
            return session.scalar(stmt)

    def get_session_id(self, *, channel: str, external_user_id: str) -> str | None:
        record = self.get_binding(channel=channel, external_user_id=external_user_id)
        return record.session_id if record is not None else None

    def list_session_ids_for_user(self, *, channel: str, external_user_id: str, limit: int = 20) -> list[str]:
        with self.database.session() as session:
            stmt = (
                select(ChannelSessionMapRecord)
                .where(
                    ChannelSessionMapRecord.channel == channel,
                    ChannelSessionMapRecord.external_user_id == external_user_id,
                )
                .order_by(desc(ChannelSessionMapRecord.updated_at))
            )
            records = list(session.scalars(stmt))
        session_ids: list[str] = []
        for record in records:
            session_id = str(record.session_id or "").strip()
            if not session_id or session_id in session_ids:
                continue
            session_ids.append(session_id)
            if len(session_ids) >= max(1, int(limit or 20)):
                break
        return session_ids

    def upsert(
        self,
        *,
        channel: str,
        external_user_id: str,
        session_id: str,
        session_started_at: datetime | None = None,
        last_interaction_at: datetime | None = None,
        last_reset_reason: str | None = None,
    ) -> ChannelSessionMapRecord:
        started_at = session_started_at if session_started_at is not None else None
        interacted_at = last_interaction_at if last_interaction_at is not None else None
        timestamp = interacted_at or started_at or utc_now()
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
                record = ChannelSessionMapRecord(
                    channel=channel,
                    external_user_id=external_user_id,
                    session_id=session_id,
                    session_started_at=started_at or timestamp,
                    last_interaction_at=interacted_at or timestamp,
                    last_reset_reason=last_reset_reason or "initial_bind",
                    updated_at=timestamp,
                )
                session.add(record)
            elif record.session_id != session_id:
                record = ChannelSessionMapRecord(
                    channel=channel,
                    external_user_id=external_user_id,
                    session_id=session_id,
                    session_started_at=started_at or timestamp,
                    last_interaction_at=interacted_at or timestamp,
                    last_reset_reason=last_reset_reason or "session_rollover",
                    updated_at=timestamp,
                )
                session.add(record)
            else:
                record.session_id = session_id
                record.updated_at = timestamp
                if started_at is not None:
                    record.session_started_at = started_at
                elif record.session_started_at is None:
                    record.session_started_at = timestamp
                if interacted_at is not None:
                    record.last_interaction_at = interacted_at
                elif record.last_interaction_at is None:
                    record.last_interaction_at = timestamp
                if last_reset_reason is not None:
                    record.last_reset_reason = last_reset_reason
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


class SqlAgentRunRecordRepository:
    def __init__(self, database: DatabaseManager) -> None:
        self.database = database

    def create_started(
        self,
        *,
        run_input: HarnessRunInput,
        harness_id: str,
        input_metadata: dict[str, Any] | None = None,
    ) -> AgentRunRecord:
        with self.database.session() as session:
            record = AgentRunRecordModel(
                run_id=run_input.run_id,
                session_id=run_input.session_id,
                source_message_id=run_input.source_message_id,
                harness_id=harness_id,
                status="running",
                trace_id=run_input.trace_id,
                parent_run_id=run_input.parent_run_id,
                agent_role=run_input.agent_role,
                cleanup_status="pending",
                budget_json=run_input.budget.to_dict(),
                input_metadata_json=dict(input_metadata or {}),
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            return self._to_agent_run_record(record)

    def mark_completed(
        self,
        *,
        run_id: str,
        status: str,
        outcome: str,
        steps: int | None,
        trace_events: list[RunTraceEvent],
        metadata: dict[str, Any] | None = None,
    ) -> AgentRunRecord:
        with self.database.session() as session:
            record = session.get(AgentRunRecordModel, run_id)
            if record is None:
                raise KeyError(f"Agent run record not found: {run_id}")
            record.status = status
            record.outcome = outcome
            record.failure_category = self._failure_category_for_outcome(status=status, outcome=outcome, metadata=metadata)
            record.cleanup_status = self._cleanup_status_from_metadata(metadata=metadata, fallback="completed")
            record.steps = steps
            record.completed_at = utc_now()
            record.trace_events_json = [self._trace_event_to_json(event) for event in trace_events]
            record.error = None
            record.metadata_json = dict(metadata or {})
            session.commit()
            session.refresh(record)
            return self._to_agent_run_record(record)

    def mark_failed(
        self,
        *,
        run_id: str,
        error: str,
        trace_events: list[RunTraceEvent],
        metadata: dict[str, Any] | None = None,
    ) -> AgentRunRecord:
        with self.database.session() as session:
            record = session.get(AgentRunRecordModel, run_id)
            if record is None:
                raise KeyError(f"Agent run record not found: {run_id}")
            record.status = "failed"
            record.outcome = "error"
            record.failure_category = self._failure_category_from_metadata(metadata=metadata) or "infrastructure_failure"
            record.cleanup_status = self._cleanup_status_from_metadata(metadata=metadata, fallback="failed")
            record.completed_at = utc_now()
            record.trace_events_json = [self._trace_event_to_json(event) for event in trace_events]
            record.error = error
            record.metadata_json = dict(metadata or {})
            session.commit()
            session.refresh(record)
            return self._to_agent_run_record(record)

    def get(self, *, run_id: str) -> AgentRunRecord:
        with self.database.session() as session:
            record = session.get(AgentRunRecordModel, run_id)
            if record is None:
                raise KeyError(f"Agent run record not found: {run_id}")
            return self._to_agent_run_record(record)

    def list_by_session(self, *, session_id: str) -> list[AgentRunRecord]:
        with self.database.session() as session:
            stmt = (
                select(AgentRunRecordModel)
                .where(AgentRunRecordModel.session_id == session_id)
                .order_by(desc(AgentRunRecordModel.started_at))
            )
            return [self._to_agent_run_record(record) for record in session.scalars(stmt)]

    @staticmethod
    def _trace_event_to_json(event: RunTraceEvent) -> dict[str, Any]:
        return {
            "event_type": event.event_type,
            "run_id": event.run_id,
            "session_id": event.session_id,
            "sequence": event.sequence,
            "severity": event.severity,
            "metadata": dict(event.metadata),
        }

    @staticmethod
    def _trace_event_from_json(payload: dict[str, Any]) -> RunTraceEvent:
        return RunTraceEvent(
            event_type=str(payload.get("event_type") or ""),
            run_id=str(payload.get("run_id") or ""),
            session_id=str(payload.get("session_id") or ""),
            sequence=int(payload.get("sequence") or 0),
            severity=str(payload.get("severity") or "info"),
            metadata=dict(payload.get("metadata") or {}),
        )

    @staticmethod
    def _failure_category_from_metadata(*, metadata: dict[str, Any] | None) -> str | None:
        value = (metadata or {}).get("failure_category")
        text = str(value or "").strip()
        return text or None

    @classmethod
    def _failure_category_for_outcome(
        cls,
        *,
        status: str,
        outcome: str,
        metadata: dict[str, Any] | None,
    ) -> str | None:
        category = cls._failure_category_from_metadata(metadata=metadata)
        if category:
            return category
        if outcome == "timeout":
            return "timeout"
        if status == "failed":
            return "infrastructure_failure"
        return None

    @staticmethod
    def _cleanup_status_from_metadata(*, metadata: dict[str, Any] | None, fallback: str) -> str:
        text = str((metadata or {}).get("cleanup_status") or "").strip()
        return text or fallback

    @classmethod
    def _to_agent_run_record(cls, record: AgentRunRecordModel) -> AgentRunRecord:
        return AgentRunRecord(
            run_id=record.run_id,
            session_id=record.session_id,
            source_message_id=record.source_message_id,
            harness_id=record.harness_id,
            status=record.status,
            outcome=record.outcome,
            trace_id=record.trace_id,
            parent_run_id=record.parent_run_id,
            agent_role=record.agent_role,
            failure_category=record.failure_category,
            cleanup_status=record.cleanup_status,
            started_at=record.started_at,
            completed_at=record.completed_at,
            steps=record.steps,
            budget=dict(record.budget_json or {}),
            input_metadata=dict(record.input_metadata_json or {}),
            trace_events=[
                cls._trace_event_from_json(payload)
                for payload in list(record.trace_events_json or [])
            ],
            error=record.error,
            metadata=dict(record.metadata_json or {}),
        )


class ScheduledTaskRepository:
    def __init__(self, database: DatabaseManager) -> None:
        self.database = database

    @staticmethod
    def _as_utc(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def create(
        self,
        *,
        session_id: str,
        owner_external_user_id: str | None,
        name: str,
        prompt_text: str,
        schedule: dict[str, Any],
        enabled: bool = True,
        run_immediately: bool = False,
        delivery_kind: str = "session_channel",
        metadata: dict[str, Any] | None = None,
    ) -> ScheduledTaskRecord:
        now = utc_now()
        normalized_schedule = normalize_schedule_input(schedule, now=now)
        next_run_at = now if run_immediately else compute_next_run_at(normalized_schedule, now=now)
        state = "scheduled" if enabled else "paused"
        with self.database.session() as session:
            record = ScheduledTaskRecord(
                session_id=session_id,
                owner_external_user_id=owner_external_user_id,
                name=name,
                prompt_text=prompt_text,
                schedule_json=normalized_schedule,
                schedule_display=format_schedule(normalized_schedule),
                delivery_kind=delivery_kind,
                enabled=1 if enabled else 0,
                state=state,
                next_run_at=next_run_at,
                metadata_json=dict(metadata or {}),
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    def get(self, *, task_id: str) -> ScheduledTaskRecord:
        with self.database.session() as session:
            record = session.get(ScheduledTaskRecord, task_id)
            if record is None:
                raise KeyError(f"Scheduled task not found: {task_id}")
            return record

    def list_for_scope(
        self,
        *,
        session_id: str,
        owner_external_user_id: str | None = None,
        limit: int = 50,
    ) -> list[ScheduledTaskRecord]:
        with self.database.session() as session:
            stmt = select(ScheduledTaskRecord)
            if owner_external_user_id:
                stmt = stmt.where(
                    (ScheduledTaskRecord.owner_external_user_id == owner_external_user_id)
                    | (ScheduledTaskRecord.session_id == session_id)
                )
            else:
                stmt = stmt.where(ScheduledTaskRecord.session_id == session_id)
            stmt = stmt.order_by(desc(ScheduledTaskRecord.created_at)).limit(limit)
            return list(session.scalars(stmt))

    def resolve_for_scope(
        self,
        *,
        task_ref: str,
        session_id: str,
        owner_external_user_id: str | None = None,
    ) -> ScheduledTaskRecord | None:
        needle = str(task_ref or "").strip()
        if not needle:
            return None
        records = self.list_for_scope(session_id=session_id, owner_external_user_id=owner_external_user_id, limit=100)
        for record in records:
            if record.id == needle:
                return record
        exact = [record for record in records if record.name.strip().lower() == needle.lower()]
        if len(exact) == 1:
            return exact[0]
        partial = [record for record in records if needle.lower() in record.name.strip().lower()]
        if len(partial) == 1:
            return partial[0]
        return None

    def update(
        self,
        *,
        task_id: str,
        name: str | None = None,
        prompt_text: str | None = None,
        schedule: dict[str, Any] | None = None,
        enabled: bool | None = None,
        run_immediately: bool | None = None,
        metadata: dict[str, Any] | None = None,
        delivery_kind: str | None = None,
    ) -> ScheduledTaskRecord:
        now = utc_now()
        with self.database.session() as session:
            record = session.get(ScheduledTaskRecord, task_id)
            if record is None:
                raise KeyError(f"Scheduled task not found: {task_id}")
            if name is not None:
                record.name = name
            if prompt_text is not None:
                record.prompt_text = prompt_text
            if schedule is not None:
                normalized_schedule = normalize_schedule_input(schedule, now=now)
                record.schedule_json = normalized_schedule
                record.schedule_display = format_schedule(normalized_schedule)
                if record.state != "running":
                    if bool(run_immediately):
                        record.next_run_at = now
                    else:
                        record.next_run_at = compute_next_run_at(
                            normalized_schedule,
                            now=now,
                            last_run_at=None,
                        )
            elif bool(run_immediately):
                record.next_run_at = now
            if enabled is not None:
                record.enabled = 1 if enabled else 0
                if record.state != "running":
                    record.state = "scheduled" if enabled else "paused"
            if metadata is not None:
                record.metadata_json = dict(metadata)
            if delivery_kind is not None:
                record.delivery_kind = delivery_kind
            record.updated_at = now
            session.commit()
            session.refresh(record)
            return record

    def pause(self, *, task_id: str) -> ScheduledTaskRecord:
        return self.update(task_id=task_id, enabled=False)

    def resume(self, *, task_id: str, run_immediately: bool = False) -> ScheduledTaskRecord:
        return self.update(task_id=task_id, enabled=True, run_immediately=run_immediately)

    def delete(self, *, task_id: str) -> bool:
        with self.database.session() as session:
            record = session.get(ScheduledTaskRecord, task_id)
            if record is None:
                return False
            session.delete(record)
            session.commit()
            return True

    def run_now(self, *, task_id: str) -> ScheduledTaskRecord:
        return self.update(task_id=task_id, enabled=True, run_immediately=True)

    def list_due(self, *, now: datetime, limit: int = 20) -> list[ScheduledTaskRecord]:
        with self.database.session() as session:
            stmt = (
                select(ScheduledTaskRecord)
                .where(
                    ScheduledTaskRecord.enabled == 1,
                    ScheduledTaskRecord.next_run_at.is_not(None),
                    ScheduledTaskRecord.next_run_at <= now,
                    (
                        (ScheduledTaskRecord.lease_expires_at.is_(None))
                        | (ScheduledTaskRecord.lease_expires_at <= now)
                    ),
                )
                .order_by(ScheduledTaskRecord.next_run_at, ScheduledTaskRecord.created_at)
                .limit(limit)
            )
            return list(session.scalars(stmt))

    def peek_next_run_at(self) -> datetime | None:
        with self.database.session() as session:
            stmt = (
                select(ScheduledTaskRecord.next_run_at)
                .where(
                    ScheduledTaskRecord.enabled == 1,
                    ScheduledTaskRecord.next_run_at.is_not(None),
                )
                .order_by(ScheduledTaskRecord.next_run_at, ScheduledTaskRecord.created_at)
                .limit(1)
            )
            next_run_at = session.scalar(stmt)
            return self._as_utc(next_run_at)

    def claim_for_run(
        self,
        *,
        task_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> ScheduledTaskRecord | None:
        with self.database.session() as session:
            record = session.get(ScheduledTaskRecord, task_id)
            if record is None or record.enabled != 1:
                return None
            next_run_at = self._as_utc(record.next_run_at)
            lease_expires_at = self._as_utc(record.lease_expires_at)
            if next_run_at is None or next_run_at > now:
                return None
            if lease_expires_at is not None and lease_expires_at > now:
                return None
            schedule = dict(record.schedule_json or {})
            if str(schedule.get("kind") or "") != "once":
                record.next_run_at = compute_next_run_at(schedule, now=now, last_run_at=now)
            record.state = "running"
            record.last_started_at = now
            record.lease_expires_at = now + timedelta(seconds=max(30, int(lease_seconds)))
            record.updated_at = now
            session.commit()
            session.refresh(record)
            return record

    def finish_run(
        self,
        *,
        task_id: str,
        finished_at: datetime,
        success: bool,
        error: str | None = None,
        delivery_error: str | None = None,
        reply_preview: str | None = None,
        run_metadata: dict[str, Any] | None = None,
    ) -> ScheduledTaskRecord:
        with self.database.session() as session:
            record = session.get(ScheduledTaskRecord, task_id)
            if record is None:
                raise KeyError(f"Scheduled task not found: {task_id}")
            schedule = dict(record.schedule_json or {})
            kind = str(schedule.get("kind") or "").strip().lower()
            record.last_run_at = finished_at
            record.last_status = "ok" if success else "error"
            record.last_error = error if not success else None
            record.last_delivery_error = delivery_error
            record.last_reply_preview = (reply_preview or "").strip() or None
            record.lease_expires_at = None
            if run_metadata is not None:
                metadata = dict(record.metadata_json or {})
                metadata["last_run"] = dict(run_metadata)
                record.metadata_json = metadata
            if success:
                if kind == "once":
                    record.enabled = 0
                    record.state = "completed"
                    record.next_run_at = None
                else:
                    record.state = "scheduled" if record.enabled else "paused"
            else:
                if kind == "once":
                    record.enabled = 0
                    record.state = "error"
                    record.next_run_at = None
                else:
                    record.state = "scheduled" if record.enabled else "paused"
            record.updated_at = utc_now()
            session.commit()
            session.refresh(record)
            return record


class SqlHitlStore:
    def __init__(self, database: DatabaseManager) -> None:
        self.database = database

    def create_pending(
        self,
        *,
        run_id: str,
        session_id: str,
        tool_name: str,
        reason: str,
        policy_profile: str | None = None,
        tool_risk: str = "medium",
        tool_arguments: dict[str, Any] | None = None,
        metadata: dict[str, object] | None = None,
    ) -> HitlRequest:
        from uuid import uuid4

        hitl_id = f"hitl-{uuid4().hex}"
        created_at = utc_now()
        with self.database.session() as session:
            record = HitlRequestModel(
                hitl_id=hitl_id,
                run_id=run_id,
                session_id=session_id,
                tool_name=tool_name,
                status="pending",
                reason=reason,
                policy_profile=policy_profile,
                tool_risk=tool_risk,
                tool_arguments_json=dict(tool_arguments or {}),
                metadata_json=dict(metadata or {}),
                created_at=created_at,
                expires_at=default_hitl_expires_at(created_at=created_at),
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            return self._to_hitl_request(record)

    def get(self, *, hitl_id: str) -> HitlRequest | None:
        with self.database.session() as session:
            record = session.get(HitlRequestModel, hitl_id)
            if record is None:
                return None
            return self._to_hitl_request(record)

    def approve(self, *, hitl_id: str) -> HitlRequest:
        return self._resolve(hitl_id=hitl_id, status="approved")

    def reject(self, *, hitl_id: str) -> HitlRequest:
        return self._resolve(hitl_id=hitl_id, status="rejected")

    def expire(self, *, hitl_id: str) -> HitlRequest:
        with self.database.session() as session:
            record = session.get(HitlRequestModel, hitl_id)
            if record is None:
                raise KeyError(f"HITL request not found: {hitl_id}")
            if record.status == "expired":
                return self._to_hitl_request(record)
            if record.status != "pending":
                raise ValueError(f"HITL request is not pending: {hitl_id}")
            record.status = "expired"
            record.resolved_at = utc_now()
            session.commit()
            session.refresh(record)
            return self._to_hitl_request(record)

    def get_latest_pending_for_session(self, *, session_id: str) -> HitlRequest | None:
        normalized_session_id = str(session_id or "").strip()
        with self.database.session() as session:
            stmt = (
                select(HitlRequestModel)
                .where(HitlRequestModel.session_id == normalized_session_id)
                .where(HitlRequestModel.status == "pending")
                .order_by(desc(HitlRequestModel.created_at))
                .limit(1)
            )
            record = session.scalars(stmt).first()
            if record is None:
                return None
            request = self._to_hitl_request(record)
            if is_hitl_expired(request):
                self.expire(hitl_id=request.hitl_id)
                return None
            return request

    def _resolve(self, *, hitl_id: str, status: str) -> HitlRequest:
        with self.database.session() as session:
            record = session.get(HitlRequestModel, hitl_id)
            if record is None:
                raise KeyError(f"HITL request not found: {hitl_id}")
            request = self._to_hitl_request(record)
            if request.status == "expired":
                raise ValueError(f"HITL request expired: {hitl_id}")
            if is_hitl_expired(request):
                record.status = "expired"
                record.resolved_at = utc_now()
                session.commit()
                raise ValueError(f"HITL request expired: {hitl_id}")
            if record.status != "pending":
                raise ValueError(f"HITL request is not pending: {hitl_id}")
            record.status = status
            record.resolved_at = utc_now()
            session.commit()
            session.refresh(record)
            return self._to_hitl_request(record)

    @staticmethod
    def _to_hitl_request(record: HitlRequestModel) -> HitlRequest:
        return HitlRequest(
            hitl_id=record.hitl_id,
            run_id=record.run_id,
            session_id=record.session_id,
            tool_name=record.tool_name,
            status=record.status,  # type: ignore[arg-type]
            reason=record.reason,
            policy_profile=record.policy_profile,
            tool_risk=record.tool_risk,
            tool_arguments=dict(record.tool_arguments_json or {}),
            created_at=record.created_at,
            expires_at=record.expires_at,
            resolved_at=record.resolved_at,
            metadata=dict(record.metadata_json or {}),
        )


class SqlPlanStore:
    def __init__(self, database: DatabaseManager) -> None:
        self.database = database

    def save(self, *, stored: StoredValidatedPlan) -> None:
        normalized_session_id = str(stored.session_id or "").strip()
        with self.database.session() as session:
            record = session.get(SessionPlanRecord, normalized_session_id)
            if record is None:
                record = SessionPlanRecord(
                    session_id=normalized_session_id,
                    plan_id=stored.plan.plan_id,
                    planner_run_id=stored.planner_run_id,
                    plan_json=stored.plan.to_dict(),
                    execution_state_json=None,
                )
                session.add(record)
            else:
                record.plan_id = stored.plan.plan_id
                record.planner_run_id = stored.planner_run_id
                record.plan_json = stored.plan.to_dict()
                record.execution_state_json = None
                record.updated_at = utc_now()
            session.commit()

    def get_latest(self, *, session_id: str, plan_id: str | None = None) -> StoredValidatedPlan | None:
        normalized_session_id = str(session_id or "").strip()
        with self.database.session() as session:
            record = session.get(SessionPlanRecord, normalized_session_id)
            if record is None:
                return None
            if plan_id and str(record.plan_id or "").strip() != str(plan_id).strip():
                return None
            return StoredValidatedPlan(
                session_id=record.session_id,
                plan=plan_spec_from_dict(dict(record.plan_json or {})),
                planner_run_id=str(record.planner_run_id or ""),
            )

    def clear_session(self, *, session_id: str) -> None:
        normalized_session_id = str(session_id or "").strip()
        with self.database.session() as session:
            record = session.get(SessionPlanRecord, normalized_session_id)
            if record is None:
                return
            session.delete(record)
            session.commit()

    def save_execution(self, *, execution: StoredPlanExecution) -> None:
        normalized_session_id = str(execution.session_id or "").strip()
        with self.database.session() as session:
            record = session.get(SessionPlanRecord, normalized_session_id)
            if record is None:
                record = SessionPlanRecord(
                    session_id=normalized_session_id,
                    plan_id=execution.plan.plan_id,
                    planner_run_id=execution.planner_run_id,
                    plan_json=execution.plan.to_dict(),
                    execution_state_json=execution.to_dict(),
                )
                session.add(record)
            else:
                record.plan_id = execution.plan.plan_id
                record.planner_run_id = execution.planner_run_id
                record.plan_json = execution.plan.to_dict()
                record.execution_state_json = execution.to_dict()
                record.updated_at = utc_now()
            session.commit()

    def get_execution(self, *, session_id: str) -> StoredPlanExecution | None:
        normalized_session_id = str(session_id or "").strip()
        with self.database.session() as session:
            record = session.get(SessionPlanRecord, normalized_session_id)
            if record is None:
                return None
            payload = record.execution_state_json
            if not isinstance(payload, dict) or not payload:
                return None
            return stored_plan_execution_from_dict(payload)

    def clear_execution(self, *, session_id: str) -> None:
        normalized_session_id = str(session_id or "").strip()
        with self.database.session() as session:
            record = session.get(SessionPlanRecord, normalized_session_id)
            if record is None:
                return
            record.execution_state_json = None
            record.updated_at = utc_now()
            session.commit()
