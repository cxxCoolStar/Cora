from __future__ import annotations

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
    UserSignalDebugResponse,
    UserProfileSection,
)
from core.clawbot.intent_router import IntentRouter
from core.clawbot.user_profile import UserProfileAggregator
from core.ingestion.service import IngestionService
from core.retrieval.service import RetrievalService
from core.storage.models import SessionRecord
from core.storage.repositories import (
    ClarificationRepository,
    ItemChunkRepository,
    ItemRepository,
    MessageRepository,
    SessionRepository,
    UserSignalRepository,
)


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
        retrieval_service: RetrievalService,
        intent_router: IntentRouter | None = None,
    ) -> None:
        self.session_repository = session_repository
        self.message_repository = message_repository
        self.item_repository = item_repository
        self.item_chunk_repository = item_chunk_repository
        self.ingestion_service = ingestion_service
        self.clarification_repository = clarification_repository
        self.user_signal_repository = user_signal_repository
        self.retrieval_service = retrieval_service
        self.intent_router = intent_router or IntentRouter()
        self.user_profile_aggregator = UserProfileAggregator()

    def create_session(self) -> SessionRecord:
        return self.session_repository.create()

    async def ingest(self, *, session_id: str, text: str | None, upload: UploadFile | None) -> IngestResponse:
        self.session_repository.get(session_id)
        user_content = text or (upload.filename if upload and upload.filename else "")
        user_message = self.message_repository.add_user_message(session_id=session_id, content=user_content)

        pending = self.clarification_repository.get_latest_pending(session_id=session_id)
        if pending is not None and text and not upload:
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
                self.message_repository.add_assistant_message(
                    session_id=session_id,
                    content=reply,
                    metadata={"decision": {"action": "capture", "confidence": "high", "reason": "Clarification reply resolved pending save.", "source": "rule"}},
                )
                return IngestResponse(reply=reply, action="capture", item_id=saved_item.item_id, decision_source="rule")
            if action == "organize":
                pending_text = pending.pending_payload_json.get("text", "")
                summary = self.ingestion_service.preview_summary(pending_text)
                reply = f"Here is a quick summary of the earlier content: {summary}"
                self.clarification_repository.resolve(clarification_id=pending.id, status="resolved")
                self.message_repository.add_assistant_message(
                    session_id=session_id,
                    content=reply,
                    metadata={"decision": {"action": "organize", "confidence": "high", "reason": "Clarification reply resolved pending organization.", "source": "rule"}},
                )
                return IngestResponse(reply=reply, action="organize", decision_source="rule")
            if action == "cancel":
                reply = "Okay, I will leave that earlier content alone."
                self.clarification_repository.resolve(clarification_id=pending.id, status="cancelled")
                self.message_repository.add_assistant_message(
                    session_id=session_id,
                    content=reply,
                    metadata={"decision": {"action": "chat", "confidence": "high", "reason": "Clarification cancelled by user.", "source": "rule"}},
                )
                return IngestResponse(reply=reply, action="chat", decision_source="rule")

        has_upload = upload is not None and bool((upload.filename or "").strip())
        decision = self.intent_router.decide(text=text, has_upload=has_upload)

        if decision.intent == "chat":
            reply = "你好，我可以帮你保存文本、链接和文件，也可以帮你查找之前发过的资料。"
            self.message_repository.add_assistant_message(
                session_id=session_id,
                content=reply,
                metadata={"decision": {"action": "chat", "confidence": decision.confidence, "reason": decision.reason, "source": decision.source}},
            )
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
            self.message_repository.add_assistant_message(
                session_id=session_id,
                content=reply,
                metadata={"decision": {"action": "clarify", "confidence": decision.confidence, "reason": decision.reason, "source": decision.source}},
            )
            return IngestResponse(reply=reply, action="clarify", needs_clarification=True, decision_source=decision.source)

        if decision.intent == "retrieve":
            result = self.retrieval_service.search(session_id=session_id, query=text or "")
            if result is None:
                reply = "我没有找到相关的已保存资料。你可以先发送内容给我保存，再来查询。"
            else:
                normalized_text = (result.item.normalized_text or "").strip()
                if normalized_text and len(normalized_text) <= self.FULL_TEXT_REPLY_THRESHOLD:
                    reply = (
                        f"我找到了一条相关资料：`{result.item.title}`。\n"
                        f"内容较短，直接给你全文：\n{normalized_text}"
                    )
                else:
                    snippet = result.matched_chunk.content if result.matched_chunk is not None else result.item.summary
                    reply = (
                        f"我找到了一条相关资料：`{result.item.title}`。\n"
                        f"摘要：{result.item.summary}\n"
                        f"相关内容：{snippet}"
                    )
                if result.item.locator_hint:
                    reply += f"\n定位提示：{result.item.locator_hint}"
            self.message_repository.add_assistant_message(
                session_id=session_id,
                content=reply,
                metadata={"decision": {"action": "retrieve", "confidence": decision.confidence, "reason": decision.reason, "source": decision.source}},
            )
            return IngestResponse(reply=reply, action="retrieve", decision_source=decision.source)

        if decision.intent == "organize":
            base_text = text or ""
            reply = f"我先理解成你想整理内容。当前版本的快速摘要是：{self.ingestion_service.preview_summary(base_text)}"
            self.message_repository.add_assistant_message(
                session_id=session_id,
                content=reply,
                metadata={"decision": {"action": "organize", "confidence": decision.confidence, "reason": decision.reason, "source": decision.source}},
            )
            return IngestResponse(reply=reply, action="organize", decision_source=decision.source)

        saved_item = await self.ingestion_service.ingest(
            session_id=session_id,
            source_message_id=user_message.id,
            text=text,
            upload=upload,
        )
        self.message_repository.add_assistant_message(
            session_id=session_id,
            content=saved_item.reply,
            metadata={"decision": {"action": "capture", "confidence": decision.confidence, "reason": decision.reason, "source": decision.source}},
        )
        return IngestResponse(reply=saved_item.reply, action="capture", item_id=saved_item.item_id, decision_source=decision.source)

    async def reply(self, *, session_id: str, text: str) -> SessionReplyResponse:
        result = await self.ingest(session_id=session_id, text=text, upload=None)
        return SessionReplyResponse(reply=result.reply, action=result.action)

    def list_items(self, *, session_id: str) -> list[ItemSummaryResponse]:
        self.session_repository.get(session_id)
        items = self.item_repository.list_by_session(session_id=session_id)
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
