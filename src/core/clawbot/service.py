from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from uuid import uuid4
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
    build_input_followup_router_messages,
    build_input_interpretation_messages,
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
    SourceEventRepository,
    TopicRepository,
    UserSignalRepository,
)
from core.tools import register_builtin_tools, registry
from core.tools.toolsets import resolve_toolsets
from core.topics.service import TopicOrganizerService

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


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

    def _detect_media_kind(self, *, upload: UploadFile | None) -> str | None:
        if upload is None or not (upload.filename or "").strip():
            return None
        suffix = Path(upload.filename or "").suffix.lower()
        if suffix in IMAGE_EXTENSIONS:
            return "image"
        return "file"

    def _interpret_initial_input(
        self,
        *,
        text: str | None,
        upload: UploadFile | None,
        context: dict[str, Any],
    ) -> dict[str, Any] | None:
        response = self.model_client.generate(
            messages=build_input_interpretation_messages(
                text=text,
                has_upload=upload is not None and bool((upload.filename or "").strip()),
                upload_filename=upload.filename if upload is not None else None,
                media_kind=self._detect_media_kind(upload=upload),
                context=context,
            ),
            tools=[],
        )
        raw = (response.assistant_text or "").strip()
        logger.info("clawbot input_interpreter_raw text=%s upload=%s output=%s", (text or "")[:120], getattr(upload, "filename", None), raw[:800])
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
        if not isinstance(payload, dict):
            return None
        return payload

    def _interpret_pending_input_reply(self, *, text: str, pending_payload: dict[str, Any]) -> dict[str, Any] | None:
        response = self.model_client.generate(
            messages=build_input_followup_router_messages(text=text, pending_payload=pending_payload),
            tools=[],
        )
        raw = (response.assistant_text or "").strip()
        logger.info("clawbot pending_input_raw text=%s output=%s", text[:120], raw[:800])
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
        if not isinstance(payload, dict):
            return None
        return payload

    async def _persist_pending_upload(self, *, upload: UploadFile) -> dict[str, str]:
        target_dir = self.ingestion_service.storage_dir / "pending"
        target_dir.mkdir(parents=True, exist_ok=True)
        filename = (upload.filename or "unnamed.bin").strip() or "unnamed.bin"
        suffix = Path(filename).suffix
        target = target_dir / f"{uuid4()}{suffix}"
        data = await upload.read()
        target.write_bytes(data)
        return {"upload_path": str(target), "upload_filename": filename}

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
                    resolved_context = self._compose_context(
                        session_id=session_id,
                        base_context=context,
                        last_action="read_item",
                        working_set=pending_payload.get("working_set") or [],
                        selected_item_id=resolved_item.id,
                    )
                    assistant_metadata = self._build_assistant_metadata(
                        action="retrieve",
                        confidence="high",
                        reason="Clarification resolved a working-set reference.",
                        source="llm_tool_call",
                        tool="read_item",
                        tool_arguments={"target": {"type": "item_id", "value": resolved_item.id}, "mode": "full_text"},
                        context=resolved_context,
                    )
                    self.message_repository.add_assistant_message(session_id=session_id, content=reply, metadata=assistant_metadata)
                    return IngestResponse(reply=reply, action="retrieve", item_id=resolved_item.id, decision_source="llm_tool_call")

            if pending_payload.get("type") == "input_interpretation":
                interpretation = self._interpret_pending_input_reply(text=text, pending_payload=pending_payload)
                action = str((interpretation or {}).get("action") or "").strip()
                note = str((interpretation or {}).get("note") or "").strip()
                if action == "capture":
                    upload_path = str(pending_payload.get("upload_path") or "").strip()
                    upload_filename = str(pending_payload.get("upload_filename") or "").strip()
                    if upload_path and upload_filename:
                        saved_item = await self.ingestion_service.ingest_saved_upload(
                            session_id=session_id,
                            source_message_id=pending.source_message_id,
                            source_event_id=str(pending_payload.get("source_event_id") or source_event.id),
                            file_path=Path(upload_path),
                            filename=upload_filename,
                            user_note=note or text,
                        )
                        reply = saved_item.reply
                    else:
                        original_text = str(pending_payload.get("original_text") or "").strip()
                        saved_item = await self.ingestion_service.ingest(
                            session_id=session_id,
                            source_message_id=pending.source_message_id,
                            source_event_id=str(pending_payload.get("source_event_id") or source_event.id),
                            text=original_text,
                            upload=None,
                        )
                        reply = f"{saved_item.reply} I used your clarification to handle the earlier content."
                    self.clarification_repository.resolve(clarification_id=pending.id, status="resolved")
                    saved_context = self._compose_context(
                        session_id=session_id,
                        base_context=context,
                        last_action="save_content",
                        working_set=[self._item_snapshot(item=self.item_repository.get_any(item_id=saved_item.item_id), rank=1)],
                        selected_item_id=saved_item.item_id,
                    )
                    self.message_repository.add_assistant_message(
                        session_id=session_id,
                        content=reply,
                        metadata=self._build_assistant_metadata(
                            action="capture",
                            confidence="high",
                            reason="Input clarification resolved pending content handling.",
                            source="llm_tool_call",
                            tool="save_content",
                            tool_arguments={"text": str(pending_payload.get('original_text') or note or '')},
                            context=saved_context,
                        ),
                    )
                    return IngestResponse(reply=reply, action="capture", item_id=saved_item.item_id, decision_source="llm_tool_call")
                if action == "cancel":
                    reply = "Okay, I will leave that pending content alone."
                    self.clarification_repository.resolve(clarification_id=pending.id, status="cancelled")
                    self.message_repository.add_assistant_message(
                        session_id=session_id,
                        content=reply,
                        metadata=self._build_assistant_metadata(
                            action="chat",
                            confidence="high",
                            reason="Input clarification cancelled by user.",
                            source="llm_tool_call",
                            tool="clarify_capture_intent",
                            tool_arguments={},
                            context=context,
                        ),
                    )
                    return IngestResponse(reply=reply, action="chat", decision_source="llm_tool_call")
                reply = str(pending_payload.get("clarification_question") or pending.question)
                return IngestResponse(reply=reply, action="clarify", needs_clarification=True, decision_source="llm_tool_call")

            action = self._interpret_clarification_reply(text=text)
            if action == "capture":
                pending_text = pending.pending_payload_json.get("text", "")
                saved_item = await self.ingestion_service.ingest(
                    session_id=session_id,
                    source_message_id=pending.source_message_id,
                    source_event_id=str(pending.pending_payload_json.get("source_event_id") or source_event.id),
                    text=pending_text,
                    upload=None,
                )
                reply = f"{saved_item.reply} I used your clarification to save the earlier content."
                self.clarification_repository.resolve(clarification_id=pending.id, status="resolved")
                saved_context = self._compose_context(
                    session_id=session_id,
                    base_context=context,
                    last_action="save_content",
                    working_set=[self._item_snapshot(item=self.item_repository.get_any(item_id=saved_item.item_id), rank=1)],
                    selected_item_id=saved_item.item_id,
                )
                self.message_repository.add_assistant_message(session_id=session_id, content=reply, metadata=self._build_assistant_metadata(action="capture", confidence="high", reason="Clarification reply resolved pending save.", source="llm_tool_call", tool="save_content", tool_arguments={"text": pending_text}, context=saved_context))
                return IngestResponse(reply=reply, action="capture", item_id=saved_item.item_id, decision_source="llm_tool_call")
            if action == "organize":
                pending_text = pending.pending_payload_json.get("text", "")
                summary = self.ingestion_service.preview_summary(pending_text)
                reply = f"Here is a quick summary of the earlier content: {summary}"
                self.clarification_repository.resolve(clarification_id=pending.id, status="resolved")
                organize_context = self._compose_context(
                    session_id=session_id,
                    base_context=context,
                    last_action="summarize_item",
                )
                self.message_repository.add_assistant_message(session_id=session_id, content=reply, metadata=self._build_assistant_metadata(action="organize", confidence="high", reason="Clarification reply resolved pending organization.", source="llm_tool_call", tool="summarize_item", tool_arguments={"target": {"type": "auto", "value": ""}}, context=organize_context))
                return IngestResponse(reply=reply, action="organize", decision_source="llm_tool_call")
            if action == "cancel":
                reply = "Okay, I will leave that earlier content alone."
                self.clarification_repository.resolve(clarification_id=pending.id, status="cancelled")
                self.message_repository.add_assistant_message(session_id=session_id, content=reply, metadata=self._build_assistant_metadata(action="chat", confidence="high", reason="Clarification cancelled by user.", source="llm_tool_call", tool="clarify_reference", tool_arguments={}, context=context))
                return IngestResponse(reply=reply, action="chat", decision_source="llm_tool_call")

        has_upload = upload is not None and bool((upload.filename or "").strip())
        if pending is None:
            interpretation = self._interpret_initial_input(text=text, upload=upload, context=context)
            if interpretation and bool(interpretation.get("needs_clarification")):
                question = str(interpretation.get("clarification_question") or "").strip()
                if question:
                    pending_payload: dict[str, Any] = {
                        "type": "input_interpretation",
                        "pending_input_type": "upload" if has_upload else "text",
                        "media_kind": self._detect_media_kind(upload=upload) or ("text" if text else "unknown"),
                        "original_text": text or "",
                        "clarification_question": question,
                        "source_event_id": source_event.id,
                    }
                    if has_upload and upload is not None:
                        pending_payload.update(await self._persist_pending_upload(upload=upload))
                    self.clarification_repository.create(
                        session_id=session_id,
                        source_message_id=user_message.id,
                        question=question,
                        candidate_intents=["capture", "cancel"],
                        pending_payload=pending_payload,
                    )
                    return IngestResponse(
                        reply=question,
                        action="clarify",
                        needs_clarification=True,
                        decision_source="llm_tool_call",
                    )
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
