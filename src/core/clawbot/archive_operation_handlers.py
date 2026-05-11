from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol
from uuid import uuid4

from fastapi import UploadFile

if TYPE_CHECKING:
    from core.clawbot.tools import AmbiguousItemReferenceError, ToolExecutionResult
    from core.storage.models import ClarificationStateRecord, ItemRecord
    from core.tools import ToolInvocation


class ArchiveCaptureHost(Protocol):
    ingestion_service: Any
    clarification_repository: Any
    item_repository: Any

    def _build_context(self, *, invocation: "ToolInvocation", last_action: str) -> dict[str, Any]:
        ...

    @staticmethod
    def _detect_media_kind(*, upload: UploadFile | None) -> str | None:
        ...


@dataclass(slots=True)
class ArchiveCaptureOperationHandler:
    host: ArchiveCaptureHost

    async def save_file(self, invocation: "ToolInvocation") -> "ToolExecutionResult":
        from core.clawbot.tools import ToolExecutionResult

        if invocation.upload is None:
            return ToolExecutionResult(
                reply="没有可保存的文件。如果你的意图是保存文本内容，请使用 save_content 工具。",
                action="chat",
            )
        if not (invocation.text or "").strip():
            question = self.build_upload_clarification_question(upload=invocation.upload)
            pending_payload = {
                "type": "input_interpretation",
                "pending_input_type": "upload",
                "media_kind": self.host._detect_media_kind(upload=invocation.upload) or "file",
                "original_text": "",
                "clarification_question": question,
                "source_event_id": str(invocation.context.get("current_source_event_id") or "") or None,
            }
            pending_payload.update(await self.persist_pending_upload(upload=invocation.upload))
            self.host.clarification_repository.create(
                session_id=invocation.session_id,
                source_message_id=invocation.source_message_id,
                question=question,
                candidate_intents=["capture", "cancel"],
                pending_payload=pending_payload,
            )
            return ToolExecutionResult(
                reply=question,
                action="clarify",
                needs_clarification=True,
            )
        saved = await self.host.ingestion_service.ingest(
            session_id=invocation.session_id,
            source_message_id=invocation.source_message_id,
            source_event_id=str(invocation.context.get("current_source_event_id") or "") or None,
            text=None,
            upload=invocation.upload,
        )
        context = self.host._build_context(
            invocation=invocation,
            last_action="save_file",
        )
        return ToolExecutionResult(
            reply=saved.reply,
            action="capture",
            item_id=saved.item_id,
            metadata={"context": context},
        )

    async def save_content(self, invocation: "ToolInvocation") -> "ToolExecutionResult":
        from core.clawbot.tools import ToolExecutionResult

        saved = await self.host.ingestion_service.ingest(
            session_id=invocation.session_id,
            source_message_id=invocation.source_message_id,
            source_event_id=str(invocation.context.get("current_source_event_id") or "") or None,
            text=str(invocation.plan.arguments.get("text") or invocation.text or ""),
            upload=None,
        )
        context = self.host._build_context(
            invocation=invocation,
            last_action="save_content",
        )
        return ToolExecutionResult(
            reply=saved.reply,
            action="capture",
            item_id=saved.item_id,
            metadata={"context": context},
        )

    async def persist_pending_upload(self, *, upload: UploadFile) -> dict[str, str]:
        target_dir = self.host.ingestion_service.storage_dir / "pending"
        target_dir.mkdir(parents=True, exist_ok=True)
        filename = (upload.filename or "unnamed.bin").strip() or "unnamed.bin"
        suffix = Path(filename).suffix
        target = target_dir / f"{uuid4()}{suffix}"
        data = await upload.read()
        target.write_bytes(data)
        return {"upload_path": str(target), "upload_filename": filename}

    async def persist_pending_upload_entry(self, *, upload: UploadFile, source_event_id: str | None) -> dict[str, str | None]:
        persisted = await self.persist_pending_upload(upload=upload)
        return {
            "upload_path": persisted["upload_path"],
            "upload_filename": persisted["upload_filename"],
            "source_event_id": source_event_id,
        }

    def build_upload_clarification_question(self, *, upload: UploadFile) -> str:
        media_kind = self.host._detect_media_kind(upload=upload)
        if media_kind == "image":
            return "这张图片你希望我怎么处理？我可以先保存，也可以按你的说明备注后再保存。"
        return "这份文件你希望我怎么处理？我可以先保存，也可以按你的说明一起记录。"


class ArchiveClarificationHost(Protocol):
    clarification_repository: Any
    ingestion_service: Any
    item_repository: Any

    def _build_context(self, *, invocation: "ToolInvocation", last_action: str) -> dict[str, Any]:
        ...

    def _format_item_reply(self, *, item: "ItemRecord", mode: str) -> str:
        ...

    def _extract_rank_from_text(self, text: str) -> int | None:
        ...


@dataclass(slots=True)
class ArchiveClarificationOperationHandler:
    host: ArchiveClarificationHost

    def clarify_reference(self, invocation: "ToolInvocation") -> "ToolExecutionResult":
        from core.clawbot.tools import ToolExecutionResult

        candidates = [
            candidate
            for candidate in (invocation.plan.arguments.get("candidates") or [])
            if isinstance(candidate, dict)
        ]
        labels = [snapshot.get("title", f"候选 {index + 1}") for index, snapshot in enumerate(candidates[:3])]
        question = "你想看哪一条资料？" if labels else "你想让我展开哪一条资料？"
        if labels:
            question += " " + "；".join(f"{index + 1}. {label}" for index, label in enumerate(labels))
        self.host.clarification_repository.create(
            session_id=invocation.session_id,
            source_message_id=invocation.source_message_id,
            question=question,
            candidate_intents=["reference_resolution"],
            pending_payload={
                "type": "reference_resolution",
                "reference_text": invocation.plan.arguments.get("reference_text") or invocation.text or "",
                "candidates": candidates[:5],
            },
        )
        return ToolExecutionResult(reply=question, action="clarify", needs_clarification=True)

    def create_reference_clarification(
        self,
        *,
        invocation: "ToolInvocation",
        query: str,
        candidates: list[dict[str, Any]],
    ) -> "ToolExecutionResult":
        from core.clawbot.tools import ToolExecutionResult

        labels = [candidate.get("title", f"候选 {index + 1}") for index, candidate in enumerate(candidates[:3])]
        question = "我找到了多条可能匹配的资料，你要哪一条？"
        if labels:
            question += " " + "；".join(f"{index + 1}. {label}" for index, label in enumerate(labels))
        self.host.clarification_repository.create(
            session_id=invocation.session_id,
            source_message_id=invocation.source_message_id,
            question=question,
            candidate_intents=["reference_resolution"],
            pending_payload={
                "type": "reference_resolution",
                "reference_text": query,
                "candidates": candidates[:5],
            },
        )
        return ToolExecutionResult(reply=question, action="clarify", needs_clarification=True)

    def clarify_capture_intent(self, invocation: "ToolInvocation") -> "ToolExecutionResult":
        from core.clawbot.tools import ToolExecutionResult

        question = str(invocation.plan.arguments.get("question") or "").strip() or "这段内容你是想让我先保存，还是先帮你总结一下？"
        self.host.clarification_repository.create(
            session_id=invocation.session_id,
            source_message_id=invocation.source_message_id,
            question=question,
            candidate_intents=["capture", "organize"],
            pending_payload={
                "text": invocation.text or "",
                "type": "capture_intent",
                "source_event_id": str(invocation.context.get("current_source_event_id") or "") or None,
            },
        )
        return ToolExecutionResult(reply=question, action="clarify", needs_clarification=True)

    async def resolve_pending(self, invocation: "ToolInvocation", *, capture_handler: ArchiveCaptureOperationHandler) -> "ToolExecutionResult":
        from core.clawbot.tools import ToolExecutionResult

        pending = self.host.clarification_repository.get_latest_pending(session_id=invocation.session_id)
        if pending is None:
            return ToolExecutionResult(reply="当前没有待处理的确认事项。", action="chat")

        pending_payload = pending.pending_payload_json or {}
        pending_type = str(pending_payload.get("type") or "").strip()
        resolution = str(invocation.plan.arguments.get("resolution") or "").strip()
        note = str(invocation.plan.arguments.get("note") or invocation.text or "").strip()

        if resolution == "cancel":
            self.host.clarification_repository.resolve(clarification_id=pending.id, status="cancelled")
            return ToolExecutionResult(reply="好，我先不处理这条待确认内容。", action="chat")

        if pending_type == "input_interpretation":
            if resolution != "save":
                return ToolExecutionResult(reply=pending.question, action="clarify", needs_clarification=True)
            return await self.resolve_pending_input_interpretation(
                invocation=invocation,
                pending=pending,
                pending_payload=pending_payload,
                note=note,
                capture_handler=capture_handler,
            )

        if pending_type == "capture_intent":
            if resolution == "summarize":
                self.host.clarification_repository.resolve(clarification_id=pending.id, status="resolved")
                pending_text = str(pending_payload.get("text") or "")
                reply = f"Here is a quick summary of the earlier content: {self.host.ingestion_service.preview_summary(pending_text)}"
                context = self.host._build_context(
                    invocation=invocation,
                    last_action="summarize_item",
                )
                return ToolExecutionResult(reply=reply, action="organize", metadata={"context": context})
            if resolution == "save":
                pending_text = str(pending_payload.get("text") or "")
                saved_item = await self.host.ingestion_service.ingest(
                    session_id=invocation.session_id,
                    source_message_id=pending.source_message_id,
                    source_event_id=str(pending_payload.get("source_event_id") or invocation.context.get("current_source_event_id") or "") or None,
                    text=pending_text,
                    upload=None,
                )
                self.host.clarification_repository.resolve(clarification_id=pending.id, status="resolved")
                context = self.host._build_context(
                    invocation=invocation,
                    last_action="save_content",
                )
                reply = f"{saved_item.reply} I used your clarification to save the earlier content."
                return ToolExecutionResult(reply=reply, action="capture", item_id=saved_item.item_id, metadata={"context": context})
            return ToolExecutionResult(reply=pending.question, action="clarify", needs_clarification=True)

        if pending_type == "reference_resolution":
            if resolution != "select":
                return ToolExecutionResult(reply=pending.question, action="clarify", needs_clarification=True)
            item = self.resolve_pending_selected_item(invocation=invocation, pending_payload=pending_payload)
            if item is None:
                return ToolExecutionResult(reply=pending.question, action="clarify", needs_clarification=True)
            self.host.clarification_repository.resolve(clarification_id=pending.id, status="resolved")
            mode = str(invocation.plan.arguments.get("mode") or "full_text")
            reply = self.host._format_item_reply(item=item, mode=mode)
            context = self.host._build_context(
                invocation=invocation,
                last_action="read_item",
            )
            return ToolExecutionResult(reply=reply, action="retrieve", item_id=item.id, metadata={"context": context})

        return ToolExecutionResult(reply=pending.question, action="clarify", needs_clarification=True)

    async def resolve_pending_input_interpretation(
        self,
        *,
        invocation: "ToolInvocation",
        pending: "ClarificationStateRecord",
        pending_payload: dict[str, Any],
        note: str,
        capture_handler: ArchiveCaptureOperationHandler,
    ) -> "ToolExecutionResult":
        from core.clawbot.tools import ToolExecutionResult

        upload_entries = pending_payload.get("upload_entries")
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
        if not normalized_entries:
            upload_path = str(pending_payload.get("upload_path") or "").strip()
            upload_filename = str(pending_payload.get("upload_filename") or "").strip()
            if upload_path and upload_filename:
                normalized_entries.append(
                    {
                        "upload_path": upload_path,
                        "upload_filename": upload_filename,
                        "source_event_id": str(pending_payload.get("source_event_id") or "").strip() or None,
                    }
                )

        if normalized_entries:
            saved_items = []
            entry_note = note if note.strip() else ""
            for entry in normalized_entries:
                saved_items.append(
                    await self.host.ingestion_service.ingest_saved_upload(
                        session_id=invocation.session_id,
                        source_message_id=pending.source_message_id,
                        source_event_id=entry.get("source_event_id")
                        or str(invocation.context.get("current_source_event_id") or "").strip()
                        or None,
                        file_path=Path(str(entry["upload_path"])),
                        filename=str(entry["upload_filename"]),
                        user_note=entry_note,
                    )
                )
            saved_item = saved_items[-1]
            if len(saved_items) == 1:
                reply = saved_item.reply
            else:
                reply = f"已按同一批次为你保存 {len(saved_items)} 个文件。最后一项：{saved_item.reply}"
        else:
            original_text = str(pending_payload.get("original_text") or "").strip()
            saved_item = await self.host.ingestion_service.ingest(
                session_id=invocation.session_id,
                source_message_id=pending.source_message_id,
                source_event_id=str(pending_payload.get("source_event_id") or invocation.context.get("current_source_event_id") or "") or None,
                text=original_text,
                upload=None,
            )
            reply = f"{saved_item.reply} I used your clarification to handle the earlier content."
        self.host.clarification_repository.resolve(clarification_id=pending.id, status="resolved")
        context = self.host._build_context(
            invocation=invocation,
            last_action="save_file" if normalized_entries else "save_content",
        )
        return ToolExecutionResult(reply=reply, action="capture", item_id=saved_item.item_id, metadata={"context": context})

    def resolve_pending_selected_item(self, *, invocation: "ToolInvocation", pending_payload: dict[str, Any]) -> "ItemRecord | None":
        candidates = [
            candidate
            for candidate in (pending_payload.get("candidates") or [])
            if isinstance(candidate, dict)
        ]
        target = invocation.plan.arguments.get("target")
        if isinstance(target, dict):
            target_type = str(target.get("type") or "").strip()
            target_value = target.get("value")
            if target_type == "item_id" and target_value:
                return self.host.item_repository.get_any(item_id=str(target_value))
        content = (invocation.text or "").strip()
        rank = self.host._extract_rank_from_text(content)
        if rank is not None and 1 <= rank <= len(candidates):
            item_id = str((candidates[rank - 1] or {}).get("item_id") or "").strip()
            if item_id:
                return self.host.item_repository.get_any(item_id=item_id)
        lowered = content.lower()
        for snapshot in candidates:
            title = str(snapshot.get("title") or "")
            item_id = str(snapshot.get("item_id") or "").strip()
            if title and item_id and (title in content or title.lower() in lowered):
                return self.host.item_repository.get_any(item_id=item_id)
        return None


class ArchiveRetrieveHost(Protocol):
    topic_organizer: Any
    item_repository: Any
    archive_runner: Any
    gateway_service: Any
    session_map_repository: Any
    channel_name: str

    def _build_context(self, *, invocation: "ToolInvocation", last_action: str) -> dict[str, Any]:
        ...

    def _single_short_item_from_topic_matches(self, topic_matches: list[tuple[object, list["ItemRecord"]]]) -> "ItemRecord | None":
        ...

    def _format_item_reply(self, *, item: "ItemRecord", mode: str) -> str:
        ...

    def _format_topic_reply(self, topic_matches: list[tuple[object, list["ItemRecord"]]]) -> tuple[str, list[dict[str, Any]]]:
        ...

    def _resolve_target_item(
        self,
        *,
        session_id: str,
        plan: Any,
        context: dict[str, Any],
        user_text: str | None,
        purpose: str,
    ) -> "ItemRecord":
        ...

    def _format_summary_reply(self, *, item: "ItemRecord") -> str:
        ...

    def _resolve_delivery_targets(
        self,
        *,
        session_id: str,
        item: "ItemRecord",
        user_text: str | None,
        limit: int = 9,
    ) -> list[dict[str, Any]]:
        ...

    def _create_reference_clarification(
        self,
        *,
        invocation: "ToolInvocation",
        query: str,
        candidates: list[dict[str, Any]],
    ) -> "ToolExecutionResult":
        ...


@dataclass(slots=True)
class ArchiveRetrieveOperationHandler:
    host: ArchiveRetrieveHost

    def overview_knowledge_base(self, invocation: "ToolInvocation") -> "ToolExecutionResult":
        from core.clawbot.tools import ToolExecutionResult

        if self.host.topic_organizer is None:
            return ToolExecutionResult(reply="当前还没有可用的知识库索引。", action="retrieve")
        self.host.topic_organizer.ensure_topic_index()
        topics = self.host.topic_organizer.topic_repository.list_all()
        items = self.host.item_repository.list_all(current_only=True)[:8]
        if not topics and not items:
            return ToolExecutionResult(reply="当前知识库还是空的。你可以先发文本、链接或文件给我。", action="retrieve")
        lines = [f"当前知识库里共有 {len(topics)} 个 topic，{len(self.host.item_repository.list_all(current_only=True))} 条当前资料。"]
        if topics:
            lines.append("主要 topic：")
            for index, topic in enumerate(topics[:5], start=1):
                lines.append(f"{index}. {topic.name} - {topic.summary or '暂无摘要'}")
        if items:
            lines.append("最近资料：")
            for index, item in enumerate(items[:5], start=1):
                lines.append(f"{index}. {item.title} - {item.summary}")
        return ToolExecutionResult(reply="\n".join(lines), action="retrieve")

    def list_topics(self, invocation: "ToolInvocation") -> "ToolExecutionResult":
        from core.clawbot.tools import ToolExecutionResult

        if self.host.topic_organizer is None:
            return ToolExecutionResult(reply="当前还没有可用的 topic 索引。", action="retrieve")
        self.host.topic_organizer.ensure_topic_index()
        topics = self.host.topic_organizer.topic_repository.list_all()
        if not topics:
            return ToolExecutionResult(reply="当前还没有任何 topic。", action="retrieve")
        lines = [f"当前共有 {len(topics)} 个 topic："]
        for index, topic in enumerate(topics[:12], start=1):
            lines.append(f"{index}. {topic.name} - {topic.summary or '暂无摘要'}")
        return ToolExecutionResult(reply="\n".join(lines), action="retrieve")

    def open_topic(self, invocation: "ToolInvocation") -> "ToolExecutionResult":
        from core.clawbot.tools import ToolExecutionResult
        import logging

        logger = logging.getLogger(__name__)
        query = str(invocation.plan.arguments.get("query") or invocation.text or "")
        if self.host.topic_organizer is None:
            return ToolExecutionResult(reply="当前还没有可用的 topic/wiki 索引。", action="retrieve")
        topic_matches = self.host.topic_organizer.search_topics(
            session_id=invocation.session_id,
            query=query,
            limit=int(invocation.plan.arguments.get("top_k") or 3),
        )
        if topic_matches:
            logger.info(
                "tool open_topic topic_path session_id=%s query=%s topics=%s",
                invocation.session_id,
                str(invocation.plan.arguments.get("query") or invocation.text or "")[:160],
                [getattr(topic, "slug", "") for topic, _ in topic_matches],
            )
            single_item = self.host._single_short_item_from_topic_matches(topic_matches)
            if single_item is not None:
                logger.info(
                    "tool open_topic topic_single_item session_id=%s item_id=%s title=%s",
                    invocation.session_id,
                    single_item.id,
                    single_item.title[:120],
                )
                reply = self.host._format_item_reply(item=single_item, mode="full_text")
                context = self.host._build_context(
                    invocation=invocation,
                    last_action="open_topic",
                )
                return ToolExecutionResult(
                    reply=reply,
                    action="retrieve",
                    item_id=single_item.id,
                    metadata={"context": context},
                )
            reply, working_set = self.host._format_topic_reply(topic_matches)
            focus_item_id = working_set[0]["item_id"] if working_set else None
            context = self.host._build_context(
                invocation=invocation,
                last_action="open_topic",
            )
            return ToolExecutionResult(
                reply=reply,
                action="retrieve",
                item_id=focus_item_id,
                metadata={"context": context},
            )
        logger.info(
            "tool open_topic miss session_id=%s query=%s",
            invocation.session_id,
            str(invocation.plan.arguments.get("query") or invocation.text or "")[:160],
        )
        return ToolExecutionResult(
            reply="我没有在当前的 topic/wiki 索引里找到相关资料。请先确认这份内容已经被归档，或者让我重新整理知识库。",
            action="retrieve",
            metadata={"context": self.host._build_context(invocation=invocation, last_action="open_topic")},
        )

    def read_item(self, invocation: "ToolInvocation") -> "ToolExecutionResult":
        from core.clawbot.tools import AmbiguousItemReferenceError, ToolExecutionResult

        try:
            item = self.host._resolve_target_item(
                session_id=invocation.session_id,
                plan=invocation.plan,
                context=invocation.context,
                user_text=invocation.text,
                purpose="read",
            )
        except AmbiguousItemReferenceError as exc:
            return self.host._create_reference_clarification(invocation=invocation, query=exc.query, candidates=exc.candidates)
        except KeyError as exc:
            return ToolExecutionResult(reply=str(exc.args[0]), action="clarify", needs_clarification=True)
        mode = str(invocation.plan.arguments.get("mode") or "summary")
        reply = self.host._format_item_reply(item=item, mode=mode)
        context = self.host._build_context(
            invocation=invocation,
            last_action="read_item",
        )
        return ToolExecutionResult(
            reply=reply,
            action="retrieve",
            item_id=item.id,
            metadata={"context": context},
        )

    def summarize_item(self, invocation: "ToolInvocation") -> "ToolExecutionResult":
        from core.clawbot.tools import AmbiguousItemReferenceError, ToolExecutionResult

        try:
            item = self.host._resolve_target_item(
                session_id=invocation.session_id,
                plan=invocation.plan,
                context=invocation.context,
                user_text=invocation.text,
                purpose="summarize",
            )
        except AmbiguousItemReferenceError as exc:
            return self.host._create_reference_clarification(invocation=invocation, query=exc.query, candidates=exc.candidates)
        except KeyError as exc:
            return ToolExecutionResult(reply=str(exc.args[0]), action="clarify", needs_clarification=True)
        reply = self.host._format_summary_reply(item=item)
        context = self.host._build_context(
            invocation=invocation,
            last_action="summarize_item",
        )
        return ToolExecutionResult(
            reply=reply,
            action="organize",
            item_id=item.id,
            metadata={"context": context},
        )

    def delete_item(self, invocation: "ToolInvocation") -> "ToolExecutionResult":
        from core.clawbot.tools import AmbiguousItemReferenceError, ToolExecutionResult

        try:
            item = self.host._resolve_target_item(
                session_id=invocation.session_id,
                plan=invocation.plan,
                context=invocation.context,
                user_text=invocation.text,
                purpose="delete",
            )
        except AmbiguousItemReferenceError as exc:
            return self.host._create_reference_clarification(invocation=invocation, query=exc.query, candidates=exc.candidates)
        except KeyError as exc:
            return ToolExecutionResult(reply=str(exc.args[0]), action="clarify", needs_clarification=True)
        deleted = self.host.item_repository.soft_delete(item_id=item.id, session_id=invocation.session_id)
        context = self.host._build_context(
            invocation=invocation,
            last_action="delete_item",
        )
        return ToolExecutionResult(
            reply=f"已删除资料 `{deleted.title}`。它不会再出现在默认列表和检索结果里。",
            action="delete",
            item_id=deleted.id,
            metadata={"context": context},
        )

    async def send_file_to_user(self, invocation: "ToolInvocation") -> "ToolExecutionResult":
        from core.clawbot.tools import AmbiguousItemReferenceError, ToolExecutionResult
        import logging

        logger = logging.getLogger(__name__)
        if self.host.gateway_service is None:
            return ToolExecutionResult(reply="当前通道暂不支持把文件发送给用户。", action="chat")

        try:
            item = self.host._resolve_target_item(
                session_id=invocation.session_id,
                plan=invocation.plan,
                context=invocation.context,
                user_text=invocation.text,
                purpose="send_file",
            )
        except AmbiguousItemReferenceError as exc:
            return self.host._create_reference_clarification(invocation=invocation, query=exc.query, candidates=exc.candidates)
        except KeyError as e:
            return ToolExecutionResult(reply=f"我没定位到要发送的资料：{e.args[0]}", action="clarify", needs_clarification=True)

        caption = str(invocation.plan.arguments.get("caption") or "").strip()
        if self.host.session_map_repository is None:
            return ToolExecutionResult(reply="当前会话没有可用的用户映射，暂时无法发送文件。", action="chat")

        user_id = self.host.session_map_repository.get_external_user_id(
            channel=self.host.channel_name,
            session_id=invocation.session_id,
        )
        if not user_id:
            return ToolExecutionResult(
                reply="我没找到这个会话对应的微信用户，暂时无法把文件发回去。",
                action="chat",
            )

        delivery_targets = self.host._resolve_delivery_targets(
            session_id=invocation.session_id,
            item=item,
            user_text=invocation.text,
        )
        if not delivery_targets:
            return ToolExecutionResult(
                reply=f"资料 `{item.title}` 没有可发送的原始文件路径，暂时无法回传给你。",
                action="chat",
            )

        try:
            sent_count = 0
            for index, target in enumerate(delivery_targets):
                path = target["path"]
                send_caption = caption if index == 0 else ""
                result = await self.host.gateway_service.send_file_to_user(
                    user_id=user_id,
                    file_path=str(path),
                    caption=send_caption,
                )
                if result.get("ret") not in (0, None) or result.get("errcode") not in (0, None):
                    error_msg = result.get("errmsg") or result.get("msg") or "未知错误"
                    if sent_count > 0:
                        return ToolExecutionResult(reply=f"前 {sent_count} 个文件已发送，但后续发送失败：{error_msg}", action="chat")
                    return ToolExecutionResult(reply=f"发送文件失败：{error_msg}", action="chat")
                sent_count += 1
            context_item = delivery_targets[0].get("item") or item
            context = self.host._build_context(
                invocation=invocation,
                last_action="send_file_to_user",
            )
            if len(delivery_targets) == 1:
                return ToolExecutionResult(
                    reply=f"已经把 `{delivery_targets[0]['display_title']}` 发给你了，请查收。",
                    action="retrieve",
                    item_id=context_item.id,
                    metadata={"context": context},
                )
            return ToolExecutionResult(
                reply=f"已经把与 `{item.title}` 相关的 {len(delivery_targets)} 个文件发给你了，请查收。",
                action="retrieve",
                item_id=context_item.id,
                metadata={"context": context},
            )
        except Exception as exc:
            logger.exception("Failed to send file to user")
            return ToolExecutionResult(reply=f"发送文件失败：{exc}", action="chat")

    def resolve_delivery_targets(
        self,
        *,
        session_id: str,
        item: "ItemRecord",
        user_text: str | None,
        limit: int = 9,
    ) -> list[dict[str, Any]]:
        direct_target = self.resolve_direct_delivery_target(item=item)
        return [direct_target] if direct_target is not None else []

    def resolve_direct_delivery_target(self, *, item: "ItemRecord") -> dict[str, Any] | None:
        metadata = item.metadata_json or {}
        stored_path = str(metadata.get("stored_file_path") or "").strip()
        if not stored_path:
            return None
        path = Path(stored_path)
        if not path.exists():
            return None
        return {"path": path, "item": item, "display_title": item.title}
