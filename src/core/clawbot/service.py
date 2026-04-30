from __future__ import annotations

import json
import logging
from typing import Any
from fastapi import UploadFile

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
from core.clawbot.planner import ToolPlan
from core.clawbot.tools import ArchiveToolExecutor
from core.clawbot.user_profile import UserProfileAggregator
from core.ingestion.service import IngestionService
from core.llm.base import ModelClient
from core.prompts import (
    build_capture_clarification_router_messages,
    build_reference_resolution_messages,
    build_tool_loop_messages,
    format_tool_result_payload,
)
from core.schemas.message import Message
from core.schemas.tool import ToolSpec as ModelToolSpec
from core.storage.models import SessionRecord
from core.storage.repositories import (
    ClarificationRepository,
    ItemRepository,
    MessageRepository,
    SessionRepository,
    TopicRepository,
    UserSignalRepository,
)
from core.tools import register_builtin_tools, registry
from core.tools.toolsets import resolve_toolsets
from core.topics.service import TopicOrganizerService

logger = logging.getLogger(__name__)


class ClawBotService:
    FULL_TEXT_REPLY_THRESHOLD = 420

    def __init__(
        self,
        *,
        session_repository: SessionRepository,
        message_repository: MessageRepository,
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
        register_builtin_tools()
        self._tool_specs = self._build_tool_specs()

    def create_session(self) -> SessionRecord:
        return self.session_repository.create()

    def _build_tool_specs(self) -> list[ModelToolSpec]:
        toolsets = ["capture", "wiki_browse", "wiki_read", "agent_state"]
        if self.tool_executor.can_send_files_to_user():
            toolsets.append("channel_delivery")
        tool_names = resolve_toolsets(toolsets)
        specs = []
        for registered in registry.get_many(tool_names):
            specs.append(
                ModelToolSpec(
                    name=registered.name,
                    description=registered.description,
                    input_schema=registered.schema,
                )
            )
        return specs

    def refresh_tool_specs(self) -> None:
        self._tool_specs = self._build_tool_specs()

    def _build_agent_messages(
        self,
        *,
        session_id: str,
        user_text: str,
        context: dict[str, Any],
        pending_payload: dict[str, Any] | None,
        tool_messages: list[Message],
    ) -> list[Message]:
        history = self.message_repository.list_by_session(session_id=session_id)
        history = [msg for msg in history if msg.id][:][-6:]
        if history and history[-1].role == "user" and history[-1].content == user_text:
            history = history[:-1]
        normalized_history: list[Message] = []
        for message in history:
            if message.role not in {"user", "assistant"}:
                continue
            if message.role == "user":
                normalized_history.append(Message.user(session_id=session_id, content=message.content))
            else:
                normalized_history.append(Message.assistant(session_id=session_id, content=message.content))
        return build_tool_loop_messages(
            session_id=session_id,
            user_text=user_text,
            context=context,
            pending_payload=pending_payload,
            history=normalized_history,
            tool_messages=tool_messages,
        )

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
        tool_messages: list[Message] = []
        latest_context = context
        last_execution: dict[str, Any] | None = None
        latest_reason = "The model responded directly."

        for step in range(3):
            messages = self._build_agent_messages(
                session_id=session_id,
                user_text=user_text,
                context=latest_context,
                pending_payload=pending_payload,
                tool_messages=tool_messages,
            )
            response = self.model_client.generate(messages=messages, tools=self._tool_specs)
            logger.info(
                "clawbot tool_loop step=%s assistant_text=%s tool_calls=%s",
                step,
                (response.assistant_text or "")[:300],
                [(tool_call.tool_name, tool_call.arguments) for tool_call in response.tool_calls],
            )
            if response.tool_calls:
                tool_messages.append(
                    Message.assistant_tool_calls(
                        session_id=session_id,
                        content=(response.assistant_text or "").strip(),
                        tool_calls=[
                            {
                                "id": tool_call.id,
                                "type": "function",
                                "function": {
                                    "name": tool_call.tool_name,
                                    "arguments": json.dumps(tool_call.arguments, ensure_ascii=False),
                                },
                            }
                            for tool_call in response.tool_calls
                        ],
                        metadata={"turn_type": "tool_calls"},
                    )
                )
                for tool_call in response.tool_calls:
                    plan = ToolPlan(
                        tool=tool_call.tool_name,
                        arguments=tool_call.arguments,
                        reason="LLM selected this tool via native tool calling.",
                        source="llm_tool_call",
                    )
                    execution = await self.tool_executor.execute(
                        session_id=session_id,
                        source_message_id=source_message_id,
                        plan=plan,
                        text=raw_text,
                        upload=upload,
                        context=latest_context,
                    )
                    latest_context = (execution.metadata or {}).get("context") if execution.metadata else latest_context
                    last_execution = {
                        "reply": execution.reply,
                        "action": execution.action,
                        "item_id": execution.item_id,
                        "needs_clarification": execution.needs_clarification,
                        "tool_name": tool_call.tool_name,
                        "tool_arguments": tool_call.arguments,
                        "context": latest_context,
                    }
                    tool_messages.append(
                        Message.tool(
                            session_id=session_id,
                            name=tool_call.tool_name,
                            tool_call_id=tool_call.id,
                            content=format_tool_result_payload(
                                tool_name=tool_call.tool_name,
                                action=execution.action,
                                item_id=execution.item_id,
                                needs_clarification=execution.needs_clarification,
                                reply=execution.reply,
                                context=latest_context,
                            ),
                            metadata={
                                "action": execution.action,
                                "item_id": execution.item_id,
                                "tool_name": tool_call.tool_name,
                            },
                        )
                    )
                    if execution.needs_clarification:
                        return {
                            **last_execution,
                            "confidence": "high",
                            "reason": "The model requested clarification through a tool call.",
                        }
                latest_reason = "The model used one or more tools before answering."
                continue

            if last_execution is None:
                return {
                    "reply": response.assistant_text or "我暂时还不能理解这个请求，你可以换一种说法试试。",
                    "action": "chat",
                    "item_id": None,
                    "needs_clarification": False,
                    "tool_name": "none",
                    "tool_arguments": {},
                    "context": latest_context,
                    "confidence": "medium",
                    "reason": latest_reason,
                }

            return {
                **last_execution,
                "reply": (response.assistant_text or "").strip() or last_execution["reply"],
                "confidence": "high",
                "reason": latest_reason,
            }

        if last_execution is not None:
            return {
                **last_execution,
                "confidence": "high",
                "reason": "The model completed after repeated tool use.",
            }
        return {
            "reply": "我暂时还不能理解这个请求，你可以换一种说法试试。",
            "action": "chat",
            "item_id": None,
            "needs_clarification": False,
            "tool_name": "none",
            "tool_arguments": {},
            "context": latest_context,
            "confidence": "low",
            "reason": "Tool-calling loop produced no final answer.",
        }

    def _interpret_clarification_reply(self, *, text: str) -> str | None:
        response = self.model_client.generate(
            messages=build_capture_clarification_router_messages(text=text),
            tools=[],
        )
        raw = (response.assistant_text or "").strip()
        logger.info("clawbot clarification_raw_output text=%s output=%s", text[:120], raw[:600])
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            fenced = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            try:
                payload = json.loads(fenced)
            except json.JSONDecodeError:
                return None
        action = str(payload.get("action") or "").strip()
        return action if action in {"capture", "organize", "cancel"} else None

    def _resolve_reference_candidate_via_llm(self, *, text: str, working_set: list[dict[str, Any]]) -> object | None:
        response = self.model_client.generate(
            messages=build_reference_resolution_messages(text=text, working_set=working_set),
            tools=[],
        )
        raw = (response.assistant_text or "").strip()
        logger.info("clawbot reference_raw_output text=%s output=%s", text[:120], raw[:600])
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            fenced = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            try:
                payload = json.loads(fenced)
            except json.JSONDecodeError:
                return None
        if str(payload.get("action") or "").strip() != "select":
            return None
        try:
            rank = int(payload.get("rank"))
        except (TypeError, ValueError):
            return None
        if not (1 <= rank <= len(working_set)):
            return None
        snapshot = working_set[rank - 1] or {}
        item_id = str(snapshot.get("item_id") or "").strip()
        if not item_id:
            return None
        return self.item_repository.get_any(item_id=item_id)

    async def ingest(self, *, session_id: str, text: str | None, upload: UploadFile | None) -> IngestResponse:
        logger.info(
            "clawbot ingest_start session_id=%s has_text=%s has_upload=%s",
            session_id,
            bool(text and text.strip()),
            bool(upload and (upload.filename or "").strip()),
        )
        self.session_repository.get(session_id)
        user_content = text or (upload.filename if upload and upload.filename else "")
        user_message = self.message_repository.add_user_message(session_id=session_id, content=user_content)

        context = self._load_context(session_id=session_id)

        pending = self.clarification_repository.get_latest_pending(session_id=session_id)
        if pending is not None and text and not upload:
            pending_payload = pending.pending_payload_json or {}
            if pending_payload.get("type") == "reference_resolution":
                resolved_item = self._resolve_reference_candidate_via_llm(
                    text=text,
                    working_set=pending_payload.get("working_set") or [],
                )
                if resolved_item is not None:
                    reply = self.tool_executor._format_item_reply(item=resolved_item, mode="full_text")
                    self.clarification_repository.resolve(clarification_id=pending.id, status="resolved")
                    assistant_metadata = self._build_assistant_metadata(
                        action="retrieve",
                        confidence="high",
                        reason="Clarification resolved a working-set reference.",
                        source="llm_tool_call",
                        tool="read_item",
                        tool_arguments={"target": {"type": "item_id", "value": resolved_item.id}, "mode": "full_text"},
                        context={
                            "working_set": pending_payload.get("working_set") or [],
                            "focus_item_id": resolved_item.id,
                            "last_action": "read_item",
                        },
                    )
                    self.message_repository.add_assistant_message(session_id=session_id, content=reply, metadata=assistant_metadata)
                    return IngestResponse(reply=reply, action="retrieve", item_id=resolved_item.id, decision_source="llm_tool_call")

            action = self._interpret_clarification_reply(text=text)
            if action == "capture":
                pending_text = pending.pending_payload_json.get("text", "")
                saved_item = await self.ingestion_service.ingest(
                    session_id=session_id,
                    source_message_id=pending.source_message_id,
                    text=pending_text,
                    upload=None,
                )
                reply = f"{saved_item.reply} I used your clarification to save the earlier content."
                self.clarification_repository.resolve(clarification_id=pending.id, status="resolved")
                self.message_repository.add_assistant_message(session_id=session_id, content=reply, metadata=self._build_assistant_metadata(action="capture", confidence="high", reason="Clarification reply resolved pending save.", source="llm_tool_call", tool="save_content", tool_arguments={"text": pending_text}, context=context))
                return IngestResponse(reply=reply, action="capture", item_id=saved_item.item_id, decision_source="llm_tool_call")
            if action == "organize":
                pending_text = pending.pending_payload_json.get("text", "")
                summary = self.ingestion_service.preview_summary(pending_text)
                reply = f"Here is a quick summary of the earlier content: {summary}"
                self.clarification_repository.resolve(clarification_id=pending.id, status="resolved")
                self.message_repository.add_assistant_message(session_id=session_id, content=reply, metadata=self._build_assistant_metadata(action="organize", confidence="high", reason="Clarification reply resolved pending organization.", source="llm_tool_call", tool="summarize_item", tool_arguments={"target": {"type": "focus_item", "value": ""}}, context=context))
                return IngestResponse(reply=reply, action="organize", decision_source="llm_tool_call")
            if action == "cancel":
                reply = "Okay, I will leave that earlier content alone."
                self.clarification_repository.resolve(clarification_id=pending.id, status="cancelled")
                self.message_repository.add_assistant_message(session_id=session_id, content=reply, metadata=self._build_assistant_metadata(action="chat", confidence="high", reason="Clarification cancelled by user.", source="llm_tool_call", tool="clarify_reference", tool_arguments={}, context=context))
                return IngestResponse(reply=reply, action="chat", decision_source="llm_tool_call")

        has_upload = upload is not None and bool((upload.filename or "").strip())
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
        context = self.message_repository.get_latest_assistant_context(session_id=session_id) or {}
        working_set = context.get("working_set") or []
        focus_item_id = str(context.get("focus_item_id") or "").strip() or None
        if focus_item_id:
            try:
                item = self.item_repository.get_any(item_id=focus_item_id)
                context["focus_item_title"] = item.title or ""
                context["focus_item_summary"] = item.summary or ""
            except Exception:
                context["focus_item_id"] = None
        context["working_set"] = [snapshot for snapshot in working_set if isinstance(snapshot, dict)]
        return context

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
