from __future__ import annotations

import logging
from fastapi import UploadFile

from core.clawbot.schemas import (
    ChunkDebugResponse,
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
from core.clawbot.intent_router import IntentRouter
from core.clawbot.planner import AgentPlanner, ToolPlan
from core.clawbot.tools import ArchiveToolExecutor, ToolExecutionResult
from core.clawbot.user_profile import UserProfileAggregator
from core.ingestion.service import IngestionService
from core.storage.models import SessionRecord
from core.storage.repositories import (
    ClarificationRepository,
    ItemChunkRepository,
    ItemRepository,
    MessageRepository,
    SessionRepository,
    TopicRepository,
    UserSignalRepository,
)
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
        item_chunk_repository: ItemChunkRepository,
        ingestion_service: IngestionService,
        clarification_repository: ClarificationRepository,
        user_signal_repository: UserSignalRepository,
        topic_repository: TopicRepository,
        intent_router: IntentRouter | None = None,
        planner: AgentPlanner | None = None,
        tool_executor: ArchiveToolExecutor | None = None,
        topic_organizer: TopicOrganizerService | None = None,
    ) -> None:
        self.session_repository = session_repository
        self.message_repository = message_repository
        self.item_repository = item_repository
        self.item_chunk_repository = item_chunk_repository
        self.ingestion_service = ingestion_service
        self.clarification_repository = clarification_repository
        self.user_signal_repository = user_signal_repository
        self.topic_repository = topic_repository
        self.intent_router = intent_router or IntentRouter()
        self.planner = planner or AgentPlanner()
        self.tool_executor = tool_executor or ArchiveToolExecutor(
            ingestion_service=ingestion_service,
            item_repository=item_repository,
            clarification_repository=clarification_repository,
        )
        self.topic_organizer = topic_organizer
        self.user_profile_aggregator = UserProfileAggregator()

    def create_session(self) -> SessionRecord:
        return self.session_repository.create()

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
                resolved_item = self._resolve_reference_candidate(
                    session_id=session_id,
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
                        source="rule",
                        tool="get_item",
                        tool_arguments={"target": {"type": "item_id", "value": resolved_item.id}, "mode": "full_text"},
                        context={
                            "working_set": pending_payload.get("working_set") or [],
                            "focus_item_id": resolved_item.id,
                            "last_action": "get_item",
                        },
                    )
                    self.message_repository.add_assistant_message(session_id=session_id, content=reply, metadata=assistant_metadata)
                    return IngestResponse(reply=reply, action="retrieve", item_id=resolved_item.id, decision_source="rule")

            action = self.intent_router.interpret_clarification_reply(text)
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
                self.message_repository.add_assistant_message(session_id=session_id, content=reply, metadata=self._build_assistant_metadata(action="capture", confidence="high", reason="Clarification reply resolved pending save.", source="rule", tool="save_text_or_link", tool_arguments={"text": pending_text, "force_type": "auto"}, context=context))
                return IngestResponse(reply=reply, action="capture", item_id=saved_item.item_id, decision_source="rule")
            if action == "organize":
                pending_text = pending.pending_payload_json.get("text", "")
                summary = self.ingestion_service.preview_summary(pending_text)
                reply = f"Here is a quick summary of the earlier content: {summary}"
                self.clarification_repository.resolve(clarification_id=pending.id, status="resolved")
                self.message_repository.add_assistant_message(session_id=session_id, content=reply, metadata=self._build_assistant_metadata(action="organize", confidence="high", reason="Clarification reply resolved pending organization.", source="rule", tool="summarize_item", tool_arguments={"target": {"type": "inline_text", "value": ""}}, context=context))
                return IngestResponse(reply=reply, action="organize", decision_source="rule")
            if action == "cancel":
                reply = "Okay, I will leave that earlier content alone."
                self.clarification_repository.resolve(clarification_id=pending.id, status="cancelled")
                self.message_repository.add_assistant_message(session_id=session_id, content=reply, metadata=self._build_assistant_metadata(action="chat", confidence="high", reason="Clarification cancelled by user.", source="rule", tool="clarify_reference", tool_arguments={}, context=context))
                return IngestResponse(reply=reply, action="chat", decision_source="rule")

        has_upload = upload is not None and bool((upload.filename or "").strip())
        decision = self.intent_router.decide(text=text, has_upload=has_upload, context=context)
        logger.info(
            "clawbot decision session_id=%s intent=%s confidence=%s source=%s reason=%s",
            session_id,
            decision.intent,
            decision.confidence,
            decision.source,
            decision.reason,
        )

        if decision.intent == "chat":
            reply = "你好，我是Cora,可以帮你保存文本、链接和文件，也可以帮你查找之前发过的资料。"
            self.message_repository.add_assistant_message(session_id=session_id, content=reply, metadata=self._build_assistant_metadata(action="chat", confidence=decision.confidence, reason=decision.reason, source=decision.source, tool="chat", tool_arguments={}, context=context))
            return IngestResponse(reply=reply, action="chat", decision_source=decision.source)

        if decision.intent == "clarify":
            reply = "这段内容你是想让我先保存，还是先帮你总结一下？"
            self.clarification_repository.create(
                session_id=session_id,
                source_message_id=user_message.id,
                question=reply,
                candidate_intents=["capture", "organize"],
                pending_payload={"text": text or ""},
            )
            self.message_repository.add_assistant_message(session_id=session_id, content=reply, metadata=self._build_assistant_metadata(action="clarify", confidence=decision.confidence, reason=decision.reason, source=decision.source, tool="clarify_reference", tool_arguments={"reference_text": text or ""}, context=context))
            return IngestResponse(reply=reply, action="clarify", needs_clarification=True, decision_source=decision.source)

        if decision.intent == "organize" and not context.get("focus_item_id") and not context.get("working_set"):
            base_text = text or ""
            reply = f"我先理解成你想整理这段新内容。当前版本的快速摘要是：{self.ingestion_service.preview_summary(base_text)}"
            self.message_repository.add_assistant_message(session_id=session_id, content=reply, metadata=self._build_assistant_metadata(action="organize", confidence=decision.confidence, reason="Inline summary for newly provided content.", source="rule", tool="summarize_inline", tool_arguments={"text": base_text}, context=context))
            return IngestResponse(reply=reply, action="organize", decision_source="rule")

        plan = self.planner.plan(text=text, has_upload=has_upload, coarse_intent=decision.intent, context=context)
        if plan is None:
            reply = "我暂时还不能理解这个请求，你可以换一种说法试试。"
            self.message_repository.add_assistant_message(session_id=session_id, content=reply, metadata=self._build_assistant_metadata(action="chat", confidence="low", reason="Planner returned no action.", source="fallback", tool="chat", tool_arguments={}, context=context))
            return IngestResponse(reply=reply, action="chat", decision_source="fallback")
        logger.info(
            "clawbot plan session_id=%s tool=%s source=%s reason=%s",
            session_id,
            plan.tool,
            plan.source,
            plan.reason,
        )

        execution = await self.tool_executor.execute(
            session_id=session_id,
            source_message_id=user_message.id,
            plan=plan,
            text=text,
            upload=upload,
            context=context,
        )
        assistant_metadata = self._build_assistant_metadata(
            action=execution.action,
            confidence=decision.confidence,
            reason=plan.reason,
            source=plan.source,
            tool=plan.tool,
            tool_arguments=plan.arguments,
            context=(execution.metadata or {}).get("context") if execution.metadata else context,
        )
        self.message_repository.add_assistant_message(session_id=session_id, content=execution.reply, metadata=assistant_metadata)
        logger.info(
            "clawbot execution_done session_id=%s action=%s item_id=%s needs_clarification=%s",
            session_id,
            execution.action,
            execution.item_id,
            execution.needs_clarification,
        )
        return IngestResponse(
            reply=execution.reply,
            action=execution.action,
            item_id=execution.item_id,
            needs_clarification=execution.needs_clarification,
            decision_source=plan.source,
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
        chunks = self.item_chunk_repository.list_by_item_ids(item_ids=[item.id for item in items])
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
            chunks=[
                ChunkDebugResponse(
                    id=chunk.id,
                    item_id=chunk.item_id,
                    chunk_index=chunk.chunk_index,
                    content=chunk.content,
                    created_at=chunk.created_at,
                )
                for chunk in chunks
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

    def _resolve_reference_candidate(self, *, session_id: str, text: str, working_set: list[dict]) -> object | None:
        rank = self._extract_rank_from_text(text)
        if rank is not None and 1 <= rank <= len(working_set):
            item_id = str((working_set[rank - 1] or {}).get("item_id") or "").strip()
            if item_id:
                return self.item_repository.get(item_id=item_id, session_id=session_id)
        lowered = text.lower()
        for snapshot in working_set:
            title = str(snapshot.get("title") or "")
            if title and (title in text or title.lower() in lowered):
                item_id = str(snapshot.get("item_id") or "").strip()
                if item_id:
                    return self.item_repository.get_any(item_id=item_id)
        return None

    @staticmethod
    def _extract_rank_from_text(text: str) -> int | None:
        mappings = {"第一个": 1, "第二个": 2, "第三个": 3, "1": 1, "2": 2, "3": 3}
        for phrase, rank in mappings.items():
            if phrase == text.strip() or phrase in text:
                return rank
        return None
