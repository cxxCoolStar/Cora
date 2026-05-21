from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Any

from fastapi import UploadFile

from core.agent.turn_runner import AgentTurnResult
from core.clawbot.schemas import (
    DecisionDebugResponse,
    ItemDetailResponse,
    MessageDebugResponse,
    SessionDebugResponse,
    TopicDebugResponse,
    TurnResponse,
    UserProfileSection,
    UserSignalDebugResponse,
)
from core.clawbot.source_events import SourceEventManager
from core.clawbot.tools import RuntimeToolExecutor
from core.clawbot.user_profile import UserProfileAggregator
from core.storage.repositories import (
    ItemRepository,
    MessageRepository,
    PendingStateRepository,
    SessionRepository,
    TopicRepository,
    UserSignalRepository,
)


@dataclass(slots=True)
class RecordedInboundTurn:
    source_message_id: str
    source_event_id: str
    model_text: str
    buffered_response: TurnResponse | None = None


@dataclass(slots=True)
class AssistantTurnOutcome:
    reply: str
    action: str
    disposition: str
    status: str
    tool_name: str
    tool_arguments: dict[str, Any]
    context: dict[str, Any] | None
    confidence: str
    reason: str
    artifacts: list[dict[str, Any]]
    trace: list[dict[str, Any]]
    tool_trace: list[dict[str, Any]]
    item_id: str | None = None


@dataclass(slots=True)
class ClawBotSessionShell:
    message_repository: MessageRepository
    pending_state_repository: PendingStateRepository
    tool_executor: RuntimeToolExecutor
    source_event_manager: SourceEventManager
    pending_upload_batch_limit: int = 12

    async def record_inbound_turn(
        self,
        *,
        session_id: str,
        text: str | None,
        upload: UploadFile | None,
        source_metadata: dict[str, Any] | None = None,
    ) -> RecordedInboundTurn:
        user_content = text or (upload.filename if upload and upload.filename else "")
        user_message = self.message_repository.add_user_message(session_id=session_id, content=user_content)
        source_event = self.source_event_manager.create_source_event(
            session_id=session_id,
            source_message_id=user_message.id,
            text=text,
            upload=upload,
            metadata=source_metadata,
        )
        if upload is not None and (upload.filename or "").strip():
            buffered_upload = UploadFile(
                filename=upload.filename,
                file=BytesIO(await upload.read()),
                headers=getattr(upload, "headers", None),
            )
            try:
                entry = await self.tool_executor.persist_pending_upload_entry(
                    upload=buffered_upload,
                    source_event_id=source_event.id,
                )
            finally:
                await buffered_upload.close()
            await upload.seek(0)
            self.source_event_manager.source_event_repository.update_upload_reference(
                event_id=source_event.id,
                stored_file_path=str(entry.get("upload_path") or ""),
                original_file_name=upload.filename,
                mime_type=getattr(upload, "content_type", None),
            )
        buffered = None
        if (
            upload is not None
            and not (text or "").strip()
            and self.source_event_manager.detect_media_kind(upload=upload) == "image"
        ):
            buffered = await self._buffer_upload_into_pending_clarification(
                session_id=session_id,
                source_message_id=user_message.id,
                source_event_id=source_event.id,
                upload=upload,
            )
        return RecordedInboundTurn(
            source_message_id=user_message.id,
            source_event_id=source_event.id,
            model_text=self.model_text_for_turn(text=text, upload=upload),
            buffered_response=buffered,
        )

    async def _buffer_upload_into_pending_clarification(
        self,
        *,
        session_id: str,
        source_message_id: str,
        source_event_id: str,
        upload: UploadFile,
    ) -> TurnResponse | None:
        pending = self.pending_state_repository.get_latest_pending(session_id=session_id)
        if pending is None:
            return None
        payload = dict(pending.pending_payload_json or {})
        if str(payload.get("type") or "").strip() != "upload_save":
            return None
        if str(payload.get("pending_input_type") or "").strip() != "upload":
            return None
        if str(payload.get("media_kind") or "").strip() != "image":
            return None

        normalized_entries = self._normalized_upload_entries(payload)
        if len(normalized_entries) >= self.pending_upload_batch_limit:
            return None

        buffered_upload = UploadFile(
            filename=upload.filename,
            file=BytesIO(await upload.read()),
            headers=getattr(upload, "headers", None),
        )
        try:
            entry = await self.tool_executor.persist_pending_upload_entry(
                upload=buffered_upload,
                source_event_id=source_event_id,
            )
        finally:
            await buffered_upload.close()

        normalized_entries.append(entry)
        payload["upload_entries"] = normalized_entries
        if normalized_entries:
            first_entry = normalized_entries[0]
            payload["upload_path"] = first_entry["upload_path"]
            payload["upload_filename"] = first_entry["upload_filename"]
            payload["source_event_id"] = first_entry.get("source_event_id")
        self.pending_state_repository.update_pending(
            pending_state_id=pending.id,
            pending_payload=payload,
        )
        return TurnResponse(
            reply="",
            status="completed",
            disposition="respond",
            action="buffered",
            item_id=None,
            needs_clarification=False,
            artifacts=[],
            trace=[],
            decision_source="system",
        )

    def persist_assistant_turn(
        self,
        *,
        session_id: str,
        outcome: AssistantTurnOutcome,
    ) -> None:
        self.message_repository.add_assistant_message(
            session_id=session_id,
            content=outcome.reply,
            metadata=self.assistant_metadata(outcome=outcome),
        )

    @staticmethod
    def assistant_metadata(*, outcome: AssistantTurnOutcome) -> dict[str, Any]:
        return {
            "decision": {
                "action": outcome.action,
                "confidence": outcome.confidence,
                "reason": outcome.reason,
                "source": "llm_tool_call",
            },
            "tool": {
                "name": outcome.tool_name,
                "arguments": outcome.tool_arguments,
            },
            "context": outcome.context or {},
            "artifacts": list(outcome.artifacts),
        }

    @staticmethod
    def to_turn_response(*, outcome: AssistantTurnOutcome) -> TurnResponse:
        return TurnResponse(
            reply=outcome.reply,
            status=outcome.status,
            disposition=outcome.disposition,
            action=outcome.action,
            item_id=outcome.item_id,
            needs_clarification=(outcome.disposition == "clarify"),
            artifacts=list(outcome.artifacts),
            trace=list(outcome.trace),
            decision_source="llm_tool_call",
        )

    @staticmethod
    def outcome_from_turn_result(result: AgentTurnResult) -> AssistantTurnOutcome:
        primary_tool = result.primary_tool
        item_id = None
        if primary_tool is not None:
            item_id = primary_tool.metadata.get("item_id")
        return AssistantTurnOutcome(
            reply=result.reply,
            action=primary_tool.action if primary_tool is not None else "chat",
            disposition=result.disposition,
            status=result.status,
            tool_name=primary_tool.tool_name if primary_tool is not None else "none",
            tool_arguments=dict(primary_tool.arguments) if primary_tool is not None else {},
            context=result.runtime_context,
            confidence=result.confidence,
            reason=result.reason,
            artifacts=list(result.artifacts),
            trace=list(result.trace),
            tool_trace=[
                {
                    "tool_name": entry.tool_name,
                    "arguments": dict(entry.arguments),
                    "action": entry.action,
                    "status": entry.status,
                    "disposition": entry.disposition,
                    "artifacts": list(entry.artifacts),
                    "hints": entry.hints.model_dump(exclude_none=True),
                    "metadata": {
                        key: value
                        for key, value in entry.metadata.items()
                        if key != "runtime_state"
                    },
                }
                for entry in result.tool_trace
            ],
            item_id=item_id if isinstance(item_id, str) else None,
        )

    @staticmethod
    def model_text_for_turn(*, text: str | None, upload: UploadFile | None) -> str:
        if text and text.strip():
            return text.strip()
        filename = (upload.filename if upload is not None else "") or "unnamed"
        return f"[file upload: {filename}]"

    @staticmethod
    def _normalized_upload_entries(payload: dict[str, Any]) -> list[dict[str, str | None]]:
        upload_entries = payload.get("upload_entries")
        normalized_entries: list[dict[str, str | None]] = []
        if isinstance(upload_entries, list):
            for entry in upload_entries:
                if not isinstance(entry, dict):
                    continue
                upload_path = str(entry.get("upload_path") or "").strip()
                upload_filename = str(entry.get("upload_filename") or "").strip()
                if not upload_path or not upload_filename:
                    continue
                normalized_entries.append(
                    {
                        "upload_path": upload_path,
                        "upload_filename": upload_filename,
                        "source_event_id": str(entry.get("source_event_id") or "").strip() or None,
                    }
                )
        if normalized_entries:
            return normalized_entries
        upload_path = str(payload.get("upload_path") or "").strip()
        upload_filename = str(payload.get("upload_filename") or "").strip()
        if upload_path and upload_filename:
            normalized_entries.append(
                {
                    "upload_path": upload_path,
                    "upload_filename": upload_filename,
                    "source_event_id": str(payload.get("source_event_id") or "").strip() or None,
                }
            )
        return normalized_entries


@dataclass(slots=True)
class SessionDebugAssembler:
    session_repository: SessionRepository
    message_repository: MessageRepository
    item_repository: ItemRepository
    user_signal_repository: UserSignalRepository
    topic_repository: TopicRepository
    user_profile_aggregator: UserProfileAggregator

    def build(self, *, session_id: str) -> SessionDebugResponse:
        session = self.session_repository.get(session_id)
        messages = self.message_repository.list_by_session(session_id=session_id)
        items = self.item_repository.list_by_session(session_id=session_id)
        signals = self.user_signal_repository.list_by_session(session_id=session_id)
        topics = self.topic_repository.list_by_session(session_id=session_id)
        profile = self.user_profile_aggregator.build(signals=signals)
        return SessionDebugResponse(
            session_id=session.id,
            session_kind=str(getattr(session, "session_kind", "conversation") or "conversation"),
            parent_session_id=str(getattr(session, "parent_session_id", "") or "").strip() or None,
            session_metadata=dict(getattr(session, "metadata_json", {}) or {}),
            created_at=session.created_at,
            messages=[
                MessageDebugResponse(
                    id=message.id,
                    role=message.role,
                    content=message.content,
                    created_at=message.created_at,
                )
                for message in messages
            ],
            items=[
                ItemDetailResponse(
                    id=item.id,
                    item_type=item.item_type,
                    title=item.title,
                    summary=item.summary,
                    normalized_text=item.normalized_text,
                    locator_hint=item.locator_hint,
                    created_at=item.created_at,
                )
                for item in items
            ],
            user_signals=[
                UserSignalDebugResponse(
                    id=signal.id,
                    signal_type=signal.signal_type,
                    signal_value=signal.signal_value,
                    confidence=signal.confidence,
                    source=signal.source,
                    created_at=signal.created_at,
                )
                for signal in signals
            ],
            user_profile=[
                UserProfileSection(name=section.name, values=section.values)
                for section in profile
            ],
            topics=[
                TopicDebugResponse(
                    id=topic.id,
                    name=topic.name,
                    slug=topic.slug,
                    summary=topic.summary,
                    tags=list(topic.tags_json or []),
                    created_at=topic.created_at,
                )
                for topic in topics
            ],
            recent_decisions=[
                DecisionDebugResponse(
                    action=(message.metadata_json.get("decision") or {}).get("action", ""),
                    confidence=(message.metadata_json.get("decision") or {}).get("confidence", ""),
                    reason=(message.metadata_json.get("decision") or {}).get("reason", ""),
                    source=(message.metadata_json.get("decision") or {}).get("source", ""),
                )
                for message in messages
                if message.role == "assistant" and message.metadata_json.get("decision")
            ],
        )
