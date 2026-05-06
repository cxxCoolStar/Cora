from __future__ import annotations

import logging
from copy import deepcopy
from pathlib import Path
from typing import Any
from fastapi import UploadFile

from core.agent.loop import AgentLoop, AgentToolExecutor, LoopResult
from core.agent.orchestrator import AgentOrchestrator, OrchestratorInput
from core.agent.prompt_builder import AgentPromptBuilder
from core.agent.runtime_state import ConversationRuntimeState, EventSnapshot, ItemSnapshot, PendingState
from core.clawbot.schemas import (
    DecisionDebugResponse,
    IngestResponse,
    ItemDetailResponse,
    ItemSummaryResponse,
    MessageDebugResponse,
    SessionDebugResponse,
    SessionReplyResponse,
    TopicDebugResponse,
    UserSignalDebugResponse,
    UserProfileSection,
)
from core.agent.skill_loader import SkillLoader
from core.clawbot.planner import ToolPlan
from core.clawbot.tools import ArchiveToolExecutor
from core.clawbot.user_profile import UserProfileAggregator
from core.ingestion.service import IngestionService
from core.llm.base import ModelClient
from core.schemas.message import Message
from core.schemas.tool import ToolCall, ToolResult
from core.schemas.tool import ToolSpec as ModelToolSpec
from core.storage.models import SessionRecord
from core.storage.repositories import (
    ClarificationRepository,
    ItemRepository,
    MessageRepository,
    SessionRepository,
    SourceEventRepository,
    TopicRepository,
    UserSignalRepository,
)
from core.tools import register_builtin_tools, registry
from core.tools.toolsets import resolve_toolsets
from core.topics.service import TopicOrganizerService

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


class _ArchiveAgentExecutor(AgentToolExecutor):
    def __init__(
        self,
        *,
        tool_executor: ArchiveToolExecutor,
        runtime_builder: Any,
    ) -> None:
        self.tool_executor = tool_executor
        self.runtime_builder = runtime_builder

    async def execute(
        self,
        *,
        session_id: str,
        tool_call: ToolCall,
        runtime: ConversationRuntimeState,
    ) -> ToolResult:
        plan = ToolPlan(
            tool=tool_call.tool_name,
            arguments=tool_call.arguments,
            reason="LLM selected this tool via native tool calling.",
            source="llm_tool_call",
        )
        context = self.runtime_builder.runtime_to_context(runtime)
        execution = await self.tool_executor.execute(
            session_id=session_id,
            source_message_id=str(runtime.metadata.get("source_message_id") or ""),
            plan=plan,
            text=runtime.metadata.get("raw_text"),
            upload=runtime.metadata.get("upload"),
            context=context,
        )
        next_context = (execution.metadata or {}).get("context") if execution.metadata else context
        next_runtime = self.runtime_builder.build_runtime_state(
            session_id=session_id,
            context=next_context,
            source_message_id=str(runtime.metadata.get("source_message_id") or ""),
            raw_text=runtime.metadata.get("raw_text"),
            upload=runtime.metadata.get("upload"),
        )
        return ToolResult(
            success=True,
            content=execution.reply,
            metadata={
                "action": execution.action,
                "item_id": execution.item_id,
                "needs_clarification": execution.needs_clarification,
                "runtime_state": next_runtime,
            },
        )


class ClawBotService:
    FULL_TEXT_REPLY_THRESHOLD = 420

    def __init__(
        self,
        *,
        session_repository: SessionRepository,
        message_repository: MessageRepository,
        source_event_repository: SourceEventRepository,
        item_repository: ItemRepository,
        ingestion_service: IngestionService,
        clarification_repository: ClarificationRepository,
        user_signal_repository: UserSignalRepository,
        topic_repository: TopicRepository,
        model_client: ModelClient,
        tool_executor: ArchiveToolExecutor | None = None,
        topic_organizer: TopicOrganizerService | None = None,
    ) -> None:
        self.session_repository = session_repository
        self.message_repository = message_repository
        self.source_event_repository = source_event_repository
        self.item_repository = item_repository
        self.ingestion_service = ingestion_service
        self.clarification_repository = clarification_repository
        self.user_signal_repository = user_signal_repository
        self.topic_repository = topic_repository
        self.model_client = model_client
        self.tool_executor = tool_executor or ArchiveToolExecutor(
            ingestion_service=ingestion_service,
            item_repository=item_repository,
            clarification_repository=clarification_repository,
        )
        self.topic_organizer = topic_organizer
        self.user_profile_aggregator = UserProfileAggregator()
        self.skill_loader = SkillLoader()
        register_builtin_tools()
        self._tool_specs = self._build_tool_specs()
        self._agent_executor = _ArchiveAgentExecutor(
            tool_executor=self.tool_executor,
            runtime_builder=self,
        )
        self._agent_loop = AgentLoop(
            model_client=self.model_client,
            tool_executor=self._agent_executor,
            tool_specs=self._tool_specs,
        )
        self._agent_orchestrator = AgentOrchestrator(
            loop=self._agent_loop,
            prompt_builder=AgentPromptBuilder(),
            skill_loader=self.skill_loader,
        )

    def create_session(self) -> SessionRecord:
        return self.session_repository.create()

    def _build_tool_specs(self) -> list[ModelToolSpec]:
        toolsets = ["archive_capture", "archive_search", "archive_read", "archive_state"]
        if self.tool_executor.can_send_files_to_user():
            toolsets.append("archive_delivery")
        tool_names = resolve_toolsets(toolsets)
        specs = []
        for registered in registry.get_many(tool_names):
            input_schema = deepcopy(registered.schema)
            if registered.name == "archive" and not self.tool_executor.can_send_files_to_user():
                action_schema = ((input_schema.get("properties") or {}).get("action") or {})
                allowed_actions = action_schema.get("enum")
                if isinstance(allowed_actions, list):
                    action_schema["enum"] = [value for value in allowed_actions if value != "deliver"]
            specs.append(
                ModelToolSpec(
                    name=registered.name,
                    description=registered.description,
                    input_schema=input_schema,
                )
            )
        return specs

    def refresh_tool_specs(self) -> None:
        self._tool_specs = self._build_tool_specs()
        self._agent_loop.tool_specs = self._tool_specs

    def _build_agent_messages(
        self,
        *,
        session_id: str,
        user_text: str,
        context: dict[str, Any],
        pending_payload: dict[str, Any] | None,
        tool_messages: list[Message],
    ) -> list[Message]:
        runtime = self.build_runtime_state(
            session_id=session_id,
            context=context,
            source_message_id="",
            raw_text=user_text,
            upload=None,
        )
        history = self._load_agent_history(session_id=session_id, user_text=user_text)
        messages = self._agent_orchestrator.prompt_builder.build_messages(
            session_id=session_id,
            user_text=user_text,
            runtime=runtime,
            skills=self.skill_loader.list_skills(),
            history=history,
        )
        messages.extend(tool_messages)
        return messages

    @staticmethod
    def _select_final_agent_reply(*, last_execution: dict[str, Any], assistant_text: str | None) -> str:
        final_reply = (assistant_text or "").strip()
        if not final_reply:
            return str(last_execution["reply"])
        # File delivery has real side effects. If the delivery tool did not report
        # success, do not let a follow-up model utterance overwrite the failure.
        tool_name = last_execution.get("tool_name")
        tool_arguments = last_execution.get("tool_arguments") or {}
        if (
            tool_name == "archive" and str(tool_arguments.get("action") or "").strip() == "deliver"
        ) and last_execution.get("action") != "retrieve":
            return str(last_execution["reply"])
        return final_reply

    async def _run_agent_loop(
        self,
        *,
        session_id: str,
        source_message_id: str,
        user_text: str,
        raw_text: str | None,
        upload: UploadFile | None,
        context: dict[str, Any],
        pending_payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        self._agent_loop.model_client = self.model_client
        runtime = self.build_runtime_state(
            session_id=session_id,
            context=context,
            source_message_id=source_message_id,
            raw_text=raw_text,
            upload=upload,
        )
        loop_result = await self._agent_orchestrator.handle_turn(
            OrchestratorInput(
                session_id=session_id,
                user_text=user_text,
                runtime=runtime,
                upload_name=upload.filename if upload is not None else None,
                history=self._load_agent_history(session_id=session_id, user_text=user_text),
            )
        )
        return self._loop_result_to_legacy_response(loop_result)

    def build_runtime_state(
        self,
        *,
        session_id: str,
        context: dict[str, Any],
        source_message_id: str,
        raw_text: str | None,
        upload: UploadFile | None,
    ) -> ConversationRuntimeState:
        pending_record = self.clarification_repository.get_latest_pending(session_id=session_id)
        working_set = [item for snapshot in context.get("working_set", []) if isinstance(snapshot, dict) for item in [self._runtime_item_snapshot(snapshot)] if item is not None]
        recent_items = [item for snapshot in context.get("recent_items", []) if isinstance(snapshot, dict) for item in [self._runtime_item_snapshot(snapshot)] if item is not None]
        primary_focus = self._runtime_item_snapshot(context.get("primary_focus")) if isinstance(context.get("primary_focus"), dict) else None
        return ConversationRuntimeState(
            session_id=session_id,
            current_source_event_id=str(context.get("current_source_event_id") or "") or None,
            working_set=working_set,
            recent_items=recent_items,
            recent_events=[self._runtime_event_snapshot(snapshot) for snapshot in context.get("recent_events", []) if isinstance(snapshot, dict)],
            primary_focus=primary_focus,
            pending_state=self._runtime_pending_state(pending_record),
            last_action=str(context.get("last_action") or "") or None,
            metadata={
                "source_message_id": source_message_id,
                "raw_text": raw_text,
                "upload": upload,
            },
        )

    def runtime_to_context(self, runtime: ConversationRuntimeState) -> dict[str, Any]:
        context = {
            "working_set": [self._context_item_snapshot(item) for item in runtime.working_set],
            "recent_items": [self._context_item_snapshot(item) for item in runtime.recent_items],
            "recent_events": [self._context_event_snapshot(event) for event in runtime.recent_events],
            "primary_focus": self._context_item_snapshot(runtime.primary_focus) if runtime.primary_focus is not None else None,
            "last_action": runtime.last_action,
        }
        if runtime.current_source_event_id:
            context["current_source_event_id"] = runtime.current_source_event_id
        return context

    def _load_agent_history(self, *, session_id: str, user_text: str) -> list[Message]:
        history = self.message_repository.list_by_session(session_id=session_id)
        history = [msg for msg in history if msg.id][-6:]
        if history and history[-1].role == "user" and history[-1].content == user_text:
            history = history[:-1]
        normalized_history: list[Message] = []
        for message in history:
            if message.role == "user":
                normalized_history.append(Message.user(session_id=session_id, content=message.content))
            elif message.role == "assistant":
                normalized_history.append(Message.assistant(session_id=session_id, content=message.content))
        return normalized_history

    def _loop_result_to_legacy_response(self, result: LoopResult) -> dict[str, Any]:
        context = self.runtime_to_context(result.runtime)
        if result.tool_name == "none":
            reply = result.final_response or "我暂时还不能理解这个请求，你可以换一种说法试试。"
            action = result.action or "chat"
            reason = "The model responded directly." if result.exit_reason == "assistant_text" else "Tool-calling loop produced no final answer."
            confidence = "medium" if result.exit_reason == "assistant_text" else "low"
        else:
            reply = self._select_final_agent_reply(
                last_execution={
                    "reply": result.last_tool_reply or result.final_response,
                    "action": result.action,
                    "tool_name": result.tool_name,
                    "tool_arguments": result.tool_arguments,
                },
                assistant_text=result.assistant_text,
            )
            action = result.action
            if result.needs_clarification:
                reason = "The model requested clarification through a tool call."
            elif result.exit_reason == "assistant_text":
                reason = "The model used one or more tools before answering."
            else:
                reason = "The model completed after repeated tool use."
            confidence = "high"
        return {
            "reply": reply,
            "action": action,
            "item_id": result.item_id,
            "needs_clarification": result.needs_clarification,
            "tool_name": result.tool_name,
            "tool_arguments": result.tool_arguments,
            "context": context,
            "confidence": confidence,
            "reason": reason,
        }

    @staticmethod
    def _runtime_item_snapshot(snapshot: dict[str, Any] | None) -> ItemSnapshot | None:
        if not isinstance(snapshot, dict):
            return None
        item_id = str(snapshot.get("item_id") or "").strip()
        title = str(snapshot.get("title") or "").strip()
        item_type = str(snapshot.get("item_type") or "").strip()
        if not item_id or not title or not item_type:
            return None
        metadata = {
            key: value
            for key, value in snapshot.items()
            if key not in {"item_id", "title", "item_type", "summary", "rank"}
        }
        return ItemSnapshot(
            item_id=item_id,
            title=title,
            item_type=item_type,
            summary=str(snapshot.get("summary") or ""),
            rank=snapshot.get("rank"),
            metadata=metadata,
        )

    @staticmethod
    def _runtime_event_snapshot(snapshot: dict[str, Any]) -> EventSnapshot:
        metadata = {
            key: value
            for key, value in snapshot.items()
            if key not in {"source_event_id", "event_type", "channel", "raw_text", "original_file_name"}
        }
        return EventSnapshot(
            source_event_id=str(snapshot.get("source_event_id") or ""),
            event_type=str(snapshot.get("event_type") or ""),
            channel=str(snapshot.get("channel") or ""),
            raw_text=str(snapshot.get("raw_text") or ""),
            original_file_name=snapshot.get("original_file_name"),
            metadata=metadata,
        )

    @staticmethod
    def _runtime_pending_state(pending: object | None) -> PendingState | None:
        if pending is None:
            return None
        payload = dict(getattr(pending, "pending_payload_json", {}) or {})
        kind_map = {
            "reference_resolution": "reference",
            "capture_intent": "capture_intent",
            "input_interpretation": "pending_upload_note",
        }
        pending_type = str(payload.get("type") or "")
        return PendingState(
            pending_id=str(getattr(pending, "id", "")),
            kind=kind_map.get(pending_type, "pending_upload_note"),
            question=str(getattr(pending, "question", "")),
            choices=list(getattr(pending, "candidate_intents_json", []) or []),
            payload=payload,
        )

    @staticmethod
    def _context_item_snapshot(item: ItemSnapshot | None) -> dict[str, Any] | None:
        if item is None:
            return None
        snapshot = {
            "item_id": item.item_id,
            "title": item.title,
            "item_type": item.item_type,
            "summary": item.summary,
            "rank": item.rank,
        }
        snapshot.update(item.metadata)
        return snapshot

    @staticmethod
    def _context_event_snapshot(event: EventSnapshot) -> dict[str, Any]:
        snapshot = {
            "source_event_id": event.source_event_id,
            "event_type": event.event_type,
            "channel": event.channel,
            "raw_text": event.raw_text,
            "original_file_name": event.original_file_name,
        }
        snapshot.update(event.metadata)
        return snapshot

    def _detect_media_kind(self, *, upload: UploadFile | None) -> str | None:
        if upload is None or not (upload.filename or "").strip():
            return None
        suffix = Path(upload.filename or "").suffix.lower()
        if suffix in IMAGE_EXTENSIONS:
            return "image"
        return "file"

    def _create_source_event(
        self,
        *,
        session_id: str,
        source_message_id: str,
        text: str | None,
        upload: UploadFile | None,
        metadata: dict[str, Any] | None = None,
    ) -> object:
        has_upload = upload is not None and bool((upload.filename or "").strip())
        event_type = "file" if has_upload else "text"
        media_kind = self._detect_media_kind(upload=upload)
        if media_kind == "image":
            event_type = "image"
        elif text and text.strip():
            stripped = text.strip()
            if stripped.startswith("http://") or stripped.startswith("https://"):
                event_type = "link"
        return self.source_event_repository.create(
            session_id=session_id,
            source_message_id=source_message_id,
            channel=str((metadata or {}).get("channel") or "chat"),
            external_event_id=(metadata or {}).get("external_event_id"),
            external_user_id=(metadata or {}).get("external_user_id"),
            event_type=event_type,
            raw_text=text or "",
            original_file_name=upload.filename if has_upload and upload is not None else None,
            mime_type=getattr(upload, "content_type", None) if has_upload and upload is not None else None,
            metadata=metadata or {},
        )

    async def ingest(self, *, session_id: str, text: str | None, upload: UploadFile | None, source_metadata: dict[str, Any] | None = None) -> IngestResponse:
        logger.info(
            "clawbot ingest_start session_id=%s has_text=%s has_upload=%s",
            session_id,
            bool(text and text.strip()),
            bool(upload and (upload.filename or "").strip()),
        )
        self.session_repository.get(session_id)
        user_content = text or (upload.filename if upload and upload.filename else "")
        user_message = self.message_repository.add_user_message(session_id=session_id, content=user_content)
        source_event = self._create_source_event(
            session_id=session_id,
            source_message_id=user_message.id,
            text=text,
            upload=upload,
            metadata=source_metadata,
        )

        context = self._load_context(session_id=session_id)
        context["current_source_event_id"] = source_event.id

        pending = self.clarification_repository.get_latest_pending(session_id=session_id)
        user_text_for_model = text.strip() if text and text.strip() else f"[file upload: {(upload.filename if upload else '') or 'unnamed'}]"
        agent_result = await self._run_agent_loop(
            session_id=session_id,
            source_message_id=user_message.id,
            user_text=user_text_for_model,
            raw_text=text,
            upload=upload,
            context=context,
            pending_payload=pending.pending_payload_json if pending is not None else None,
        )
        self.message_repository.add_assistant_message(
            session_id=session_id,
            content=agent_result["reply"],
            metadata=self._build_assistant_metadata(
                action=agent_result["action"],
                confidence=agent_result["confidence"],
                reason=agent_result["reason"],
                source="llm_tool_call",
                tool=agent_result["tool_name"],
                tool_arguments=agent_result["tool_arguments"],
                context=agent_result["context"],
            ),
        )
        logger.info(
            "clawbot execution_done session_id=%s action=%s item_id=%s needs_clarification=%s tool=%s",
            session_id,
            agent_result["action"],
            agent_result["item_id"],
            agent_result["needs_clarification"],
            agent_result["tool_name"],
        )
        return IngestResponse(
            reply=agent_result["reply"],
            action=agent_result["action"],
            item_id=agent_result["item_id"],
            needs_clarification=agent_result["needs_clarification"],
            decision_source="llm_tool_call",
        )

    async def reply(self, *, session_id: str, text: str) -> SessionReplyResponse:
        result = await self.ingest(session_id=session_id, text=text, upload=None)
        return SessionReplyResponse(reply=result.reply, action=result.action)

    def list_items(self, *, session_id: str) -> list[ItemSummaryResponse]:
        self.session_repository.get(session_id)
        items = self.item_repository.list_by_session(session_id=session_id, current_only=True)
        return [
            ItemSummaryResponse(
                id=item.id,
                item_type=item.item_type,
                title=item.title,
                summary=item.summary,
                created_at=item.created_at,
            )
            for item in items
        ]

    def get_item(self, *, session_id: str, item_id: str) -> ItemDetailResponse:
        self.session_repository.get(session_id)
        item = self.item_repository.get(item_id=item_id, session_id=session_id)
        return ItemDetailResponse(
            id=item.id,
            item_type=item.item_type,
            title=item.title,
            summary=item.summary,
            normalized_text=item.normalized_text,
            locator_hint=item.locator_hint,
            created_at=item.created_at,
        )

    def list_sessions(self) -> list[SessionRecord]:
        return self.session_repository.list_recent()

    def get_session_debug(self, *, session_id: str) -> SessionDebugResponse:
        session = self.session_repository.get(session_id)
        messages = self.message_repository.list_by_session(session_id=session_id)
        items = self.item_repository.list_by_session(session_id=session_id)
        signals = self.user_signal_repository.list_by_session(session_id=session_id)
        topics = self.topic_repository.list_by_session(session_id=session_id)
        profile = self.user_profile_aggregator.build(signals=signals)
        return SessionDebugResponse(
            session_id=session.id,
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

    def _load_context(self, *, session_id: str) -> dict:
        base_context = self.message_repository.get_latest_assistant_context(session_id=session_id) or {}
        return self._compose_context(session_id=session_id, base_context=base_context)

    def _compose_context(
        self,
        *,
        session_id: str,
        base_context: dict[str, Any],
        last_action: str | None = None,
        working_set: list[dict[str, Any]] | None = None,
        selected_item_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_working_set = [
            snapshot for snapshot in (working_set if working_set is not None else base_context.get("working_set") or [])
            if isinstance(snapshot, dict)
        ]
        recent_items = self._merge_recent_item_snapshots(
            session_id=session_id,
            selected_item_id=selected_item_id,
            working_set=normalized_working_set,
            prior_recent_items=base_context.get("recent_items") or [],
        )
        primary_focus = self._resolve_primary_focus(
            session_id=session_id,
            selected_item_id=selected_item_id,
            base_context=base_context,
            working_set=normalized_working_set,
            recent_items=recent_items,
        )
        context = {
            "working_set": normalized_working_set,
            "recent_items": recent_items,
            "recent_events": self._load_recent_event_snapshots(session_id=session_id),
            "primary_focus": primary_focus,
            "last_action": last_action or base_context.get("last_action"),
        }
        if base_context.get("current_source_event_id"):
            context["current_source_event_id"] = base_context.get("current_source_event_id")
        return context

    def _merge_recent_item_snapshots(
        self,
        *,
        session_id: str,
        selected_item_id: str | None,
        working_set: list[dict[str, Any]],
        prior_recent_items: list[dict[str, Any]],
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()

        def add_snapshot(snapshot: dict[str, Any] | None) -> None:
            if not isinstance(snapshot, dict):
                return
            item_id = str(snapshot.get("item_id") or "").strip()
            if not item_id or item_id in seen:
                return
            try:
                item = self.item_repository.get_any(item_id=item_id)
            except Exception:
                return
            seen.add(item_id)
            merged.append(self._item_snapshot(item=item, rank=snapshot.get("rank")))

        if selected_item_id:
            try:
                selected = self.item_repository.get_any(item_id=selected_item_id)
                merged.append(self._item_snapshot(item=selected, rank=1))
                seen.add(selected.id)
            except Exception:
                pass
        for snapshot in working_set:
            add_snapshot(snapshot)
        for snapshot in prior_recent_items:
            add_snapshot(snapshot if isinstance(snapshot, dict) else None)
        for item in self.item_repository.list_by_session(session_id=session_id, current_only=True)[:limit]:
            if item.id in seen:
                continue
            merged.append(self._item_snapshot(item=item, rank=None))
            seen.add(item.id)
            if len(merged) >= limit:
                break
        return merged[:limit]

    def _resolve_primary_focus(
        self,
        *,
        session_id: str,
        selected_item_id: str | None,
        base_context: dict[str, Any],
        working_set: list[dict[str, Any]],
        recent_items: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        candidate_ids: list[str] = []
        if selected_item_id:
            candidate_ids.append(selected_item_id)
        primary_focus = base_context.get("primary_focus") or {}
        primary_focus_id = str(primary_focus.get("item_id") or "").strip()
        if primary_focus_id:
            candidate_ids.append(primary_focus_id)
        legacy_focus_id = str(base_context.get("focus_item_id") or "").strip()
        if legacy_focus_id:
            candidate_ids.append(legacy_focus_id)
        for snapshot in working_set + recent_items:
            item_id = str((snapshot or {}).get("item_id") or "").strip()
            if item_id:
                candidate_ids.append(item_id)
        for item_id in candidate_ids:
            try:
                item = self.item_repository.get_any(item_id=item_id)
                return self._item_snapshot(item=item, rank=None)
            except Exception:
                continue
        return None

    def _load_recent_event_snapshots(self, *, session_id: str, limit: int = 5) -> list[dict[str, Any]]:
        snapshots: list[dict[str, Any]] = []
        for event in self.source_event_repository.list_by_session(session_id=session_id, limit=limit):
            snapshots.append(
                {
                    "source_event_id": event.id,
                    "event_type": event.event_type,
                    "channel": event.channel,
                    "raw_text": event.raw_text,
                    "original_file_name": event.original_file_name,
                    "mime_type": event.mime_type,
                    "created_at": event.created_at.isoformat(),
                }
            )
        return snapshots

    @staticmethod
    def _item_snapshot(item: object, rank: int | None) -> dict[str, Any]:
        return {
            "item_id": item.id,
            "session_id": item.session_id,
            "source_event_id": item.source_event_id,
            "item_type": item.item_type,
            "title": item.title,
            "summary": item.summary,
            "locator_hint": item.locator_hint,
            "saved_at": item.created_at.isoformat(),
            "rank": rank,
        }

    def _build_assistant_metadata(
        self,
        *,
        action: str,
        confidence: str,
        reason: str,
        source: str,
        tool: str,
        tool_arguments: dict,
        context: dict | None,
    ) -> dict:
        return {
            "decision": {
                "action": action,
                "confidence": confidence,
                "reason": reason,
                "source": source,
            },
            "tool": {
                "name": tool,
                "arguments": tool_arguments,
            },
            "context": context or {},
        }
