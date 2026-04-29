from __future__ import annotations

from fastapi import UploadFile

from core.clawbot.schemas import (
    ChunkDebugResponse,
    IngestResponse,
    ItemDetailResponse,
    ItemSummaryResponse,
    MessageDebugResponse,
    SessionDebugResponse,
    SessionReplyResponse,
)
from core.clawbot.intent_router import IntentRouter
from core.ingestion.service import IngestionService
from core.storage.models import SessionRecord
from core.storage.repositories import (
    ClarificationRepository,
    ItemChunkRepository,
    ItemRepository,
    MessageRepository,
    SessionRepository,
)


class ClawBotService:
    def __init__(
        self,
        *,
        session_repository: SessionRepository,
        message_repository: MessageRepository,
        item_repository: ItemRepository,
        item_chunk_repository: ItemChunkRepository,
        ingestion_service: IngestionService,
        clarification_repository: ClarificationRepository,
        intent_router: IntentRouter | None = None,
    ) -> None:
        self.session_repository = session_repository
        self.message_repository = message_repository
        self.item_repository = item_repository
        self.item_chunk_repository = item_chunk_repository
        self.ingestion_service = ingestion_service
        self.clarification_repository = clarification_repository
        self.intent_router = intent_router or IntentRouter()

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
                self.message_repository.add_assistant_message(session_id=session_id, content=reply)
                return IngestResponse(reply=reply, action="capture", item_id=saved_item.item_id)
            if action == "organize":
                pending_text = pending.pending_payload_json.get("text", "")
                summary = self.ingestion_service.preview_summary(pending_text)
                reply = f"Here is a quick summary of the earlier content: {summary}"
                self.clarification_repository.resolve(clarification_id=pending.id, status="resolved")
                self.message_repository.add_assistant_message(session_id=session_id, content=reply)
                return IngestResponse(reply=reply, action="organize")
            if action == "cancel":
                reply = "Okay, I will leave that earlier content alone."
                self.clarification_repository.resolve(clarification_id=pending.id, status="cancelled")
                self.message_repository.add_assistant_message(session_id=session_id, content=reply)
                return IngestResponse(reply=reply, action="chat")

        has_upload = upload is not None and bool((upload.filename or "").strip())
        decision = self.intent_router.decide(text=text, has_upload=has_upload)

        if decision.intent == "chat":
            reply = "你好，我可以帮你保存文本、链接和文件，也可以帮你查找之前发过的资料。"
            self.message_repository.add_assistant_message(session_id=session_id, content=reply)
            return IngestResponse(reply=reply, action="chat")

        if decision.intent == "clarify":
            reply = "这段内容你是想让我先保存，还是先帮你总结一下？"
            self.clarification_repository.create(
                session_id=session_id,
                source_message_id=user_message.id,
                question=reply,
                candidate_intents=["capture", "organize"],
                pending_payload={"text": text or ""},
            )
            self.message_repository.add_assistant_message(session_id=session_id, content=reply)
            return IngestResponse(reply=reply, action="clarify", needs_clarification=True)

        if decision.intent == "retrieve":
            reply = "我后面会帮你做资料检索。当前版本你可以先去 Debug Explorer 看已保存内容，下一步我会把自然语言查找接上。"
            self.message_repository.add_assistant_message(session_id=session_id, content=reply)
            return IngestResponse(reply=reply, action="retrieve")

        if decision.intent == "organize":
            base_text = text or ""
            reply = f"我先理解成你想整理内容。当前版本的快速摘要是：{self.ingestion_service.preview_summary(base_text)}"
            self.message_repository.add_assistant_message(session_id=session_id, content=reply)
            return IngestResponse(reply=reply, action="organize")

        saved_item = await self.ingestion_service.ingest(
            session_id=session_id,
            source_message_id=user_message.id,
            text=text,
            upload=upload,
        )
        self.message_repository.add_assistant_message(session_id=session_id, content=saved_item.reply)
        return IngestResponse(reply=saved_item.reply, action="capture", item_id=saved_item.item_id)

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
        )
