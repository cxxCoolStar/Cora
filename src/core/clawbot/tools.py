from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import UploadFile

from core.archivefs.service import ArchiveLookupResult, ArchiveSkillScriptRunner
from core.clawbot.planner import ToolPlan
from core.ingestion.service import IngestionService
from core.storage.models import ClarificationStateRecord, ItemRecord
from core.storage.repositories import (
    ChannelSessionMapRepository,
    ClarificationRepository,
    ItemRepository,
)
from core.tools import ToolInvocation, register_builtin_tools, registry
from core.topics.service import TopicOrganizerService

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ToolExecutionResult:
    reply: str
    action: str
    item_id: str | None = None
    needs_clarification: bool = False
    metadata: dict[str, Any] | None = None


class ArchiveToolExecutor:
    FULL_TEXT_REPLY_THRESHOLD = 420

    def __init__(
        self,
        *,
        ingestion_service: IngestionService,
        item_repository: ItemRepository,
        clarification_repository: ClarificationRepository,
        topic_organizer: TopicOrganizerService | None = None,
        archive_runner: ArchiveSkillScriptRunner | None = None,
        gateway_service: Any | None = None,
        session_map_repository: ChannelSessionMapRepository | None = None,
        channel_name: str = "wechat",
    ) -> None:
        self.ingestion_service = ingestion_service
        self.item_repository = item_repository
        self.clarification_repository = clarification_repository
        self.topic_organizer = topic_organizer
        self.archive_runner = archive_runner
        self.gateway_service = gateway_service
        self.session_map_repository = session_map_repository
        self.channel_name = channel_name
        register_builtin_tools()

    def can_send_files_to_user(self) -> bool:
        return self.gateway_service is not None and self.session_map_repository is not None

    @staticmethod
    def _merge_recent_items(*snapshots: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        for snapshot in snapshots:
            item_id = str(snapshot.get("item_id") or "").strip()
            if not item_id or item_id in seen:
                continue
            seen.add(item_id)
            merged.append(dict(snapshot))
            if len(merged) >= limit:
                break
        return merged

    def _build_context(
        self,
        *,
        invocation: ToolInvocation,
        item: ItemRecord | None,
        last_action: str,
        working_set: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        existing_recent = [
            snapshot
            for snapshot in (invocation.context.get("recent_items") or [])
            if isinstance(snapshot, dict)
        ]
        selected_snapshot = self._item_snapshot(item, rank=1) if item is not None else {}
        normalized_working_set = [
            snapshot for snapshot in (working_set if working_set is not None else invocation.context.get("working_set", []))
            if isinstance(snapshot, dict)
        ]
        recent_items = self._merge_recent_items(
            selected_snapshot,
            *normalized_working_set,
            *existing_recent,
        )
        return {
            "working_set": normalized_working_set,
            "recent_items": recent_items,
            "primary_focus": dict(selected_snapshot) if selected_snapshot else None,
            "recent_events": invocation.context.get("recent_events", []),
            "last_action": last_action,
        }

    async def execute(
        self,
        *,
        session_id: str,
        source_message_id: str,
        plan: ToolPlan,
        text: str | None,
        upload: UploadFile | None,
        context: dict[str, Any],
    ) -> ToolExecutionResult:
        logger.info(
            "tool execute_start session_id=%s tool=%s text=%s has_upload=%s",
            session_id,
            plan.tool,
            (text or "")[:160],
            bool(upload and (upload.filename or "").strip()),
        )
        invocation = ToolInvocation(
            session_id=session_id,
            source_message_id=source_message_id,
            plan=plan,
            text=text,
            upload=upload,
            context=context,
        )
        try:
            return await registry.dispatch(self, name=plan.tool, invocation=invocation)
        except KeyError:
            return ToolExecutionResult(reply="我暂时还不能处理这个请求。", action="chat")

    async def _tool_archive(self, invocation: ToolInvocation) -> ToolExecutionResult:
        action = str(invocation.plan.arguments.get("action") or "").strip()
        if action == "save":
            if invocation.upload is not None:
                return await self._tool_save_file(invocation)
            return await self._tool_save_content(invocation)
        if action == "overview":
            return self._tool_overview_knowledge_base(invocation)
        if action == "list_topics":
            return self._tool_list_topics(invocation)
        if action == "open":
            return self._tool_open_topic(invocation)
        if action == "read":
            return self._tool_read_item(invocation)
        if action == "summarize":
            return self._tool_summarize_item(invocation)
        if action == "deliver":
            return await self._tool_send_file_to_user(invocation)
        return ToolExecutionResult(reply="我暂时还不能处理这个 archive 动作。", action="chat")

    async def _tool_archive_state(self, invocation: ToolInvocation) -> ToolExecutionResult:
        action = str(invocation.plan.arguments.get("action") or "").strip()
        if action == "clarify_reference":
            return self._tool_clarify_reference(invocation)
        if action == "clarify_capture_intent":
            return self._tool_clarify_capture_intent(invocation)
        if action == "resolve_pending":
            return await self._tool_resolve_pending(invocation)
        return ToolExecutionResult(reply="我暂时还不能处理这个 archive_state 动作。", action="chat")

    async def _tool_save_file(self, invocation: ToolInvocation) -> ToolExecutionResult:
        if invocation.upload is None:
            return ToolExecutionResult(
                reply="没有可保存的文件。如果你的意图是保存文本内容，请使用 save_content 工具。",
                action="chat",
            )
        if not (invocation.text or "").strip():
            question = self._build_upload_clarification_question(upload=invocation.upload)
            pending_payload = {
                "type": "input_interpretation",
                "pending_input_type": "upload",
                "media_kind": self._detect_media_kind(upload=invocation.upload) or "file",
                "original_text": "",
                "clarification_question": question,
                "source_event_id": str(invocation.context.get("current_source_event_id") or "") or None,
            }
            pending_payload.update(await self._persist_pending_upload(upload=invocation.upload))
            self.clarification_repository.create(
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
        saved = await self.ingestion_service.ingest(
            session_id=invocation.session_id,
            source_message_id=invocation.source_message_id,
            source_event_id=str(invocation.context.get("current_source_event_id") or "") or None,
            text=None,
            upload=invocation.upload,
        )
        item = self.item_repository.get_any(item_id=saved.item_id)
        context = self._build_context(
            invocation=invocation,
            item=item,
            last_action="save_file",
            working_set=[self._item_snapshot(item, rank=1)],
        )
        return ToolExecutionResult(
            reply=saved.reply,
            action="capture",
            item_id=saved.item_id,
            metadata={"context": context},
        )

    async def _tool_save_content(self, invocation: ToolInvocation) -> ToolExecutionResult:
        saved = await self.ingestion_service.ingest(
            session_id=invocation.session_id,
            source_message_id=invocation.source_message_id,
            source_event_id=str(invocation.context.get("current_source_event_id") or "") or None,
            text=str(invocation.plan.arguments.get("text") or invocation.text or ""),
            upload=None,
        )
        item = self.item_repository.get_any(item_id=saved.item_id)
        context = self._build_context(
            invocation=invocation,
            item=item,
            last_action="save_content",
            working_set=[self._item_snapshot(item, rank=1)],
        )
        return ToolExecutionResult(
            reply=saved.reply,
            action="capture",
            item_id=saved.item_id,
            metadata={"context": context},
        )

    def _tool_overview_knowledge_base(self, invocation: ToolInvocation) -> ToolExecutionResult:
        if self.topic_organizer is None:
            return ToolExecutionResult(reply="当前还没有可用的知识库索引。", action="retrieve")
        self.topic_organizer.ensure_topic_index()
        topics = self.topic_organizer.topic_repository.list_all()
        items = self.item_repository.list_all(current_only=True)[:8]
        if not topics and not items:
            return ToolExecutionResult(reply="当前知识库还是空的。你可以先发文本、链接或文件给我。", action="retrieve")
        lines = [f"当前知识库里共有 {len(topics)} 个 topic，{len(self.item_repository.list_all(current_only=True))} 条当前资料。"]
        if topics:
            lines.append("主要 topic：")
            for index, topic in enumerate(topics[:5], start=1):
                lines.append(f"{index}. {topic.name} - {topic.summary or '暂无摘要'}")
        if items:
            lines.append("最近资料：")
            for index, item in enumerate(items[:5], start=1):
                lines.append(f"{index}. {item.title} - {item.summary}")
        return ToolExecutionResult(reply="\n".join(lines), action="retrieve")

    def _tool_list_topics(self, invocation: ToolInvocation) -> ToolExecutionResult:
        if self.topic_organizer is None:
            return ToolExecutionResult(reply="当前还没有可用的 topic 索引。", action="retrieve")
        self.topic_organizer.ensure_topic_index()
        topics = self.topic_organizer.topic_repository.list_all()
        if not topics:
            return ToolExecutionResult(reply="当前还没有任何 topic。", action="retrieve")
        lines = [f"当前共有 {len(topics)} 个 topic："]
        for index, topic in enumerate(topics[:12], start=1):
            lines.append(f"{index}. {topic.name} - {topic.summary or '暂无摘要'}")
        return ToolExecutionResult(reply="\n".join(lines), action="retrieve")

    def _tool_open_topic(self, invocation: ToolInvocation) -> ToolExecutionResult:
        query = str(invocation.plan.arguments.get("query") or invocation.text or "")
        archive_lookup = self._search_archive_assets(query=query)
        if archive_lookup is not None and archive_lookup.count > 0:
            reply, working_set = self._format_archive_lookup_reply(archive_lookup)
            focus_item_id = next(
                (str(snapshot.get("item_id") or "").strip() for snapshot in working_set if snapshot.get("item_id")),
                None,
            )
            selected = self.item_repository.get_any(item_id=focus_item_id) if focus_item_id else None
            context = self._build_context(
                invocation=invocation,
                item=selected,
                last_action="open_topic",
                working_set=working_set,
            )
            return ToolExecutionResult(
                reply=reply,
                action="retrieve",
                item_id=focus_item_id,
                metadata={"context": context},
            )
        if self.topic_organizer is None:
            return ToolExecutionResult(reply="当前还没有可用的 topic/wiki 索引。", action="retrieve")
        topic_matches = self.topic_organizer.search_topics(
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
            single_item = self._single_short_item_from_topic_matches(topic_matches)
            if single_item is not None:
                logger.info(
                    "tool open_topic topic_single_item session_id=%s item_id=%s title=%s",
                    invocation.session_id,
                    single_item.id,
                    single_item.title[:120],
                )
                reply = self._format_item_reply(item=single_item, mode="full_text")
                context = self._build_context(
                    invocation=invocation,
                    item=single_item,
                    last_action="open_topic",
                    working_set=[self._item_snapshot(single_item, rank=1)],
                )
                return ToolExecutionResult(
                    reply=reply,
                    action="retrieve",
                    item_id=single_item.id,
                    metadata={"context": context},
                )
            reply, working_set = self._format_topic_reply(topic_matches)
            focus_item_id = working_set[0]["item_id"] if working_set else None
            selected = self.item_repository.get_any(item_id=focus_item_id) if focus_item_id else None
            context = self._build_context(
                invocation=invocation,
                item=selected,
                last_action="open_topic",
                working_set=working_set,
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
            metadata={"context": self._build_context(invocation=invocation, item=None, last_action="open_topic", working_set=[])},
        )

    def _tool_read_item(self, invocation: ToolInvocation) -> ToolExecutionResult:
        item = self._resolve_target_item(session_id=invocation.session_id, plan=invocation.plan, context=invocation.context, user_text=invocation.text, purpose="read")
        mode = str(invocation.plan.arguments.get("mode") or "summary")
        reply = self._format_item_reply(item=item, mode=mode)
        context = self._build_context(
            invocation=invocation,
            item=item,
            last_action="read_item",
            working_set=invocation.context.get("working_set", []),
        )
        return ToolExecutionResult(
            reply=reply,
            action="retrieve",
            item_id=item.id,
            metadata={"context": context},
        )

    def _tool_summarize_item(self, invocation: ToolInvocation) -> ToolExecutionResult:
        item = self._resolve_target_item(session_id=invocation.session_id, plan=invocation.plan, context=invocation.context, user_text=invocation.text, purpose="summarize")
        reply = self._format_summary_reply(item=item)
        context = self._build_context(
            invocation=invocation,
            item=item,
            last_action="summarize_item",
            working_set=invocation.context.get("working_set", []),
        )
        return ToolExecutionResult(
            reply=reply,
            action="organize",
            item_id=item.id,
            metadata={"context": context},
        )

    def _tool_clarify_reference(self, invocation: ToolInvocation) -> ToolExecutionResult:
        candidates = invocation.context.get("working_set", [])
        labels = [snapshot.get("title", f"候选 {index + 1}") for index, snapshot in enumerate(candidates[:3])]
        question = "你想看哪一条资料？" if labels else "你想让我展开哪一条资料？"
        if labels:
            question += " " + "；".join(f"{index + 1}. {label}" for index, label in enumerate(labels))
        self.clarification_repository.create(
            session_id=invocation.session_id,
            source_message_id=invocation.source_message_id,
            question=question,
            candidate_intents=["reference_resolution"],
            pending_payload={
                "type": "reference_resolution",
                "reference_text": invocation.plan.arguments.get("reference_text") or invocation.text or "",
                "working_set": candidates[:5],
            },
        )
        return ToolExecutionResult(reply=question, action="clarify", needs_clarification=True)

    def _tool_clarify_capture_intent(self, invocation: ToolInvocation) -> ToolExecutionResult:
        question = str(invocation.plan.arguments.get("question") or "").strip() or "这段内容你是想让我先保存，还是先帮你总结一下？"
        self.clarification_repository.create(
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

    async def _tool_resolve_pending(self, invocation: ToolInvocation) -> ToolExecutionResult:
        pending = self.clarification_repository.get_latest_pending(session_id=invocation.session_id)
        if pending is None:
            return ToolExecutionResult(reply="当前没有待处理的确认事项。", action="chat")

        pending_payload = pending.pending_payload_json or {}
        pending_type = str(pending_payload.get("type") or "").strip()
        resolution = str(invocation.plan.arguments.get("resolution") or "").strip()
        note = str(invocation.plan.arguments.get("note") or invocation.text or "").strip()

        if resolution == "cancel":
            self.clarification_repository.resolve(clarification_id=pending.id, status="cancelled")
            return ToolExecutionResult(reply="好，我先不处理这条待确认内容。", action="chat")

        if pending_type == "input_interpretation":
            if resolution != "save":
                return ToolExecutionResult(reply=pending.question, action="clarify", needs_clarification=True)
            return await self._resolve_pending_input_interpretation(
                invocation=invocation,
                pending=pending,
                pending_payload=pending_payload,
                note=note,
            )

        if pending_type == "capture_intent":
            if resolution == "summarize":
                self.clarification_repository.resolve(clarification_id=pending.id, status="resolved")
                pending_text = str(pending_payload.get("text") or "")
                reply = f"Here is a quick summary of the earlier content: {self.ingestion_service.preview_summary(pending_text)}"
                context = self._build_context(
                    invocation=invocation,
                    item=None,
                    last_action="summarize_item",
                    working_set=invocation.context.get("working_set", []),
                )
                return ToolExecutionResult(reply=reply, action="organize", metadata={"context": context})
            if resolution == "save":
                pending_text = str(pending_payload.get("text") or "")
                saved_item = await self.ingestion_service.ingest(
                    session_id=invocation.session_id,
                    source_message_id=pending.source_message_id,
                    source_event_id=str(pending_payload.get("source_event_id") or invocation.context.get("current_source_event_id") or "") or None,
                    text=pending_text,
                    upload=None,
                )
                self.clarification_repository.resolve(clarification_id=pending.id, status="resolved")
                item = self.item_repository.get_any(item_id=saved_item.item_id)
                context = self._build_context(
                    invocation=invocation,
                    item=item,
                    last_action="save_content",
                    working_set=[self._item_snapshot(item, rank=1)],
                )
                reply = f"{saved_item.reply} I used your clarification to save the earlier content."
                return ToolExecutionResult(reply=reply, action="capture", item_id=saved_item.item_id, metadata={"context": context})
            return ToolExecutionResult(reply=pending.question, action="clarify", needs_clarification=True)

        if pending_type == "reference_resolution":
            if resolution != "select":
                return ToolExecutionResult(reply=pending.question, action="clarify", needs_clarification=True)
            item = self._resolve_pending_selected_item(invocation=invocation, pending_payload=pending_payload)
            if item is None:
                return ToolExecutionResult(reply=pending.question, action="clarify", needs_clarification=True)
            self.clarification_repository.resolve(clarification_id=pending.id, status="resolved")
            mode = str(invocation.plan.arguments.get("mode") or "full_text")
            reply = self._format_item_reply(item=item, mode=mode)
            context = self._build_context(
                invocation=invocation,
                item=item,
                last_action="read_item",
                working_set=pending_payload.get("working_set") or invocation.context.get("working_set", []),
            )
            return ToolExecutionResult(reply=reply, action="retrieve", item_id=item.id, metadata={"context": context})

        return ToolExecutionResult(reply=pending.question, action="clarify", needs_clarification=True)

    async def _persist_pending_upload(self, *, upload: UploadFile) -> dict[str, str]:
        target_dir = self.ingestion_service.storage_dir / "pending"
        target_dir.mkdir(parents=True, exist_ok=True)
        filename = (upload.filename or "unnamed.bin").strip() or "unnamed.bin"
        suffix = Path(filename).suffix
        target = target_dir / f"{uuid4()}{suffix}"
        data = await upload.read()
        target.write_bytes(data)
        return {"upload_path": str(target), "upload_filename": filename}

    def _build_upload_clarification_question(self, *, upload: UploadFile) -> str:
        media_kind = self._detect_media_kind(upload=upload)
        if media_kind == "image":
            return "这张图片你希望我怎么处理？我可以先保存，也可以按你的说明备注后再保存。"
        return "这份文件你希望我怎么处理？我可以先保存，也可以按你的说明一起记录。"

    async def _resolve_pending_input_interpretation(
        self,
        *,
        invocation: ToolInvocation,
        pending: ClarificationStateRecord,
        pending_payload: dict[str, Any],
        note: str,
    ) -> ToolExecutionResult:
        upload_path = str(pending_payload.get("upload_path") or "").strip()
        upload_filename = str(pending_payload.get("upload_filename") or "").strip()
        if upload_path and upload_filename:
            saved_item = await self.ingestion_service.ingest_saved_upload(
                session_id=invocation.session_id,
                source_message_id=pending.source_message_id,
                source_event_id=str(pending_payload.get("source_event_id") or invocation.context.get("current_source_event_id") or "") or None,
                file_path=Path(upload_path),
                filename=upload_filename,
                user_note=note,
            )
            reply = saved_item.reply
        else:
            original_text = str(pending_payload.get("original_text") or "").strip()
            saved_item = await self.ingestion_service.ingest(
                session_id=invocation.session_id,
                source_message_id=pending.source_message_id,
                source_event_id=str(pending_payload.get("source_event_id") or invocation.context.get("current_source_event_id") or "") or None,
                text=original_text,
                upload=None,
            )
            reply = f"{saved_item.reply} I used your clarification to handle the earlier content."
        self.clarification_repository.resolve(clarification_id=pending.id, status="resolved")
        item = self.item_repository.get_any(item_id=saved_item.item_id)
        context = self._build_context(
            invocation=invocation,
            item=item,
            last_action="save_file" if upload_path and upload_filename else "save_content",
            working_set=[self._item_snapshot(item, rank=1)],
        )
        return ToolExecutionResult(reply=reply, action="capture", item_id=saved_item.item_id, metadata={"context": context})

    async def _tool_send_file_to_user(self, invocation: ToolInvocation) -> ToolExecutionResult:
        """Send a file from the wiki to the user via WeChat."""
        if self.gateway_service is None:
            return ToolExecutionResult(reply="当前通道暂不支持把文件发送给用户。", action="chat")

        try:
            item = self._resolve_target_item(
                session_id=invocation.session_id,
                plan=invocation.plan,
                context=invocation.context,
                user_text=invocation.text,
                purpose="send_file",
            )
        except KeyError as e:
            title_hint = str(invocation.plan.arguments.get("target_title_hint") or "").strip()
            if not title_hint:
                return ToolExecutionResult(reply=f"我没定位到要发送的资料：{e}", action="chat")
            try:
                item = self._resolve_item_by_title_hint(
                    session_id=invocation.session_id,
                    title_hint=title_hint,
                    context=invocation.context,
                )
            except KeyError:
                return ToolExecutionResult(reply=f"我没定位到要发送的资料：{e}", action="chat")

        metadata = item.metadata_json or {}
        stored_path = metadata.get("stored_file_path")
        if not stored_path and self.archive_runner is not None:
            archive_record_id = str(metadata.get("archive_record_id") or "").strip()
            archive_relative_path = str(metadata.get("archive_relative_path") or "").strip()
            lookup = None
            if archive_record_id:
                lookup = self.archive_runner.find_assets(record_id=archive_record_id, limit=1)
            elif archive_relative_path:
                lookup = self.archive_runner.find_assets(query=archive_relative_path, limit=1)
            if lookup and lookup.results:
                stored_path = lookup.results[0].get("resolved_path")
        if not stored_path:
            return ToolExecutionResult(
                reply=f"资料 `{item.title}` 没有可发送的原始文件路径，暂时无法回传给你。",
                action="chat",
            )

        path = Path(stored_path)
        if not path.exists():
            return ToolExecutionResult(
                reply=f"资料 `{item.title}` 对应的文件已经找不到了，暂时无法发送。",
                action="chat",
            )

        caption = str(invocation.plan.arguments.get("caption") or "").strip()
        if self.session_map_repository is None:
            return ToolExecutionResult(reply="当前会话没有可用的用户映射，暂时无法发送文件。", action="chat")

        user_id = self.session_map_repository.get_external_user_id(
            channel=self.channel_name,
            session_id=invocation.session_id,
        )
        if not user_id:
            return ToolExecutionResult(
                reply="我没找到这个会话对应的微信用户，暂时无法把文件发回去。",
                action="chat",
            )

        try:
            result = await self.gateway_service.send_file_to_user(
                user_id=user_id,
                file_path=str(path),
                caption=caption or f"这是你要的图片：{item.title}",
            )
            if result.get("ret") in (0, None) and result.get("errcode") in (0, None):
                context = self._build_context(
                    invocation=invocation,
                    item=item,
                    last_action="send_file_to_user",
                    working_set=invocation.context.get("working_set", []),
                )
                return ToolExecutionResult(
                    reply=f"已经把 `{item.title}` 发给你了，请查收。",
                    action="retrieve",
                    item_id=item.id,
                    metadata={"context": context},
                )
            error_msg = result.get("errmsg") or result.get("msg") or "未知错误"
            return ToolExecutionResult(reply=f"发送文件失败：{error_msg}", action="chat")
        except Exception as exc:
            logger.exception("Failed to send file to user")
            return ToolExecutionResult(reply=f"发送文件失败：{exc}", action="chat")

    def _resolve_pending_selected_item(self, *, invocation: ToolInvocation, pending_payload: dict[str, Any]) -> ItemRecord | None:
        target = invocation.plan.arguments.get("target")
        if isinstance(target, dict):
            target_type = str(target.get("type") or "").strip()
            target_value = target.get("value")
            if target_type == "item_id" and target_value:
                return self.item_repository.get_any(item_id=str(target_value))
            if target_type == "working_set_rank":
                working_set = pending_payload.get("working_set") or []
                try:
                    rank = int(target_value or 0)
                except (TypeError, ValueError):
                    rank = 0
                if 1 <= rank <= len(working_set):
                    item_id = str((working_set[rank - 1] or {}).get("item_id") or "").strip()
                    if item_id:
                        return self.item_repository.get_any(item_id=item_id)
        content = (invocation.text or "").strip()
        working_set = pending_payload.get("working_set") or []
        rank = self._extract_rank_from_text(content)
        if rank is not None and 1 <= rank <= len(working_set):
            item_id = str((working_set[rank - 1] or {}).get("item_id") or "").strip()
            if item_id:
                return self.item_repository.get_any(item_id=item_id)
        lowered = content.lower()
        for snapshot in working_set:
            title = str(snapshot.get("title") or "")
            item_id = str(snapshot.get("item_id") or "").strip()
            if title and item_id and (title in content or title.lower() in lowered):
                return self.item_repository.get_any(item_id=item_id)
        return None

    def _resolve_target_item(
        self,
        *,
        session_id: str,
        plan: ToolPlan,
        context: dict[str, Any],
        user_text: str | None,
        purpose: str,
    ) -> ItemRecord:
        target = plan.arguments.get("target") or {}
        target_type = str(target.get("type") or "auto").strip()
        target_value = target.get("value")
        if target_type == "item_id":
            return self.item_repository.get_any(item_id=str(target_value))
        if target_type == "working_set_rank":
            working_set = context.get("working_set") or []
            rank = int(target_value or 1)
            if 1 <= rank <= len(working_set):
                item_id = str((working_set[rank - 1] or {}).get("item_id") or "").strip()
                if item_id:
                    return self.item_repository.get_any(item_id=item_id)
            raise KeyError(f"Working-set item not found for rank {rank}")
        if target_type == "recent_item":
            recent_items = context.get("recent_items") or []
            rank = int(target_value or 1)
            if 1 <= rank <= len(recent_items):
                item_id = str((recent_items[rank - 1] or {}).get("item_id") or "").strip()
                if item_id:
                    return self.item_repository.get_any(item_id=item_id)
            raise KeyError(f"Recent item not found for rank {rank}")
        if target_type == "focus_item":
            target_type = "auto"
        title_hint = str(plan.arguments.get("target_title_hint") or "").strip()
        return self._resolve_candidate_item(
            session_id=session_id,
            context=context,
            user_text=user_text,
            title_hint=title_hint,
            purpose=purpose,
        )

    def _resolve_item_by_title_hint(self, *, session_id: str, title_hint: str, context: dict[str, Any]) -> ItemRecord:
        normalized_hint = title_hint.strip().lower()
        if not normalized_hint:
            raise KeyError("Empty title hint.")

        working_set = context.get("working_set") or []
        for snapshot in working_set:
            title = str(snapshot.get("title") or "").strip()
            if title and normalized_hint in title.lower():
                item_id = str(snapshot.get("item_id") or "").strip()
                if item_id:
                    return self.item_repository.get_any(item_id=item_id)

        for snapshot in context.get("recent_items") or []:
            title = str(snapshot.get("title") or "").strip()
            item_id = str(snapshot.get("item_id") or "").strip()
            if title and item_id and normalized_hint in title.lower():
                return self.item_repository.get_any(item_id=item_id)

        primary_focus = context.get("primary_focus") or {}
        primary_focus_id = str(primary_focus.get("item_id") or "").strip()
        primary_focus_title = str(primary_focus.get("title") or "").strip()
        if primary_focus_id and primary_focus_title and normalized_hint in primary_focus_title.lower():
            return self.item_repository.get_any(item_id=primary_focus_id)

        item = self.item_repository.search_latest_by_text(session_id=session_id, query=title_hint)
        if item is None:
            raise KeyError(f"No item matched title hint: {title_hint}")
        return item

    def _resolve_candidate_item(
        self,
        *,
        session_id: str,
        context: dict[str, Any],
        user_text: str | None,
        title_hint: str,
        purpose: str,
    ) -> ItemRecord:
        query = " ".join(part.strip() for part in [title_hint, user_text or ""] if part and part.strip())
        candidates: list[tuple[ItemRecord, int]] = []
        seen: set[str] = set()
        preferred_types = self._preferred_item_types(user_text=user_text, purpose=purpose)

        def add_candidate(item: ItemRecord, *, score: int) -> None:
            if item.id in seen:
                return
            seen.add(item.id)
            candidates.append((item, score + self._candidate_match_score(item=item, query=query, preferred_types=preferred_types)))

        for snapshot in context.get("working_set") or []:
            item_id = str((snapshot or {}).get("item_id") or "").strip()
            if item_id:
                add_candidate(self.item_repository.get_any(item_id=item_id), score=70)

        for snapshot in context.get("recent_items") or []:
            item_id = str((snapshot or {}).get("item_id") or "").strip()
            if item_id:
                add_candidate(self.item_repository.get_any(item_id=item_id), score=50)

        primary_focus = context.get("primary_focus") or {}
        primary_focus_id = str(primary_focus.get("item_id") or "").strip()
        if primary_focus_id:
            add_candidate(self.item_repository.get_any(item_id=primary_focus_id), score=20)

        if query.strip():
            searched = self.item_repository.search_latest_by_text(session_id=session_id, query=query.strip())
            if searched is not None:
                add_candidate(searched, score=40)

        if not candidates:
            raise KeyError("No candidate item is available for this follow-up.")

        candidates.sort(key=lambda pair: pair[1], reverse=True)
        top_item, top_score = candidates[0]
        if top_score < 30:
            raise KeyError("Could not confidently resolve the requested item.")
        return top_item

    @staticmethod
    def _preferred_item_types(*, user_text: str | None, purpose: str) -> set[str]:
        lowered = (user_text or "").lower()
        if purpose == "send_file":
            if any(token in lowered for token in ["照片", "图片", "头像", "image", "photo", "pic"]):
                return {"image"}
            return {"document", "image", "file_upload"}
        if any(token in lowered for token in ["照片", "图片", "头像", "image", "photo", "pic"]):
            return {"image"}
        if any(token in lowered for token in ["文档", "文件", "简历", "document", "file", "pdf", "doc"]):
            return {"document", "file_upload"}
        return set()

    @staticmethod
    def _candidate_match_score(*, item: ItemRecord, query: str, preferred_types: set[str]) -> int:
        lowered_query = query.lower().strip()
        score = 0
        if preferred_types and item.item_type in preferred_types:
            score += 25
        if not lowered_query:
            return score
        haystacks = [
            item.title.lower(),
            item.summary.lower(),
            item.normalized_text.lower(),
            (item.locator_hint or "").lower(),
        ]
        metadata = item.metadata_json or {}
        original_name = str(metadata.get("original_file_name") or "").lower()
        user_note = str(metadata.get("user_note") or "").lower()
        haystacks.extend([original_name, user_note])
        compact_query = re.sub(r"\s+", "", lowered_query)
        for haystack in haystacks:
            if not haystack:
                continue
            compact_haystack = re.sub(r"\s+", "", haystack)
            if compact_query and compact_query in compact_haystack:
                score += 35
            for token in [token for token in re.split(r"\s+", lowered_query) if token]:
                if token in haystack:
                    score += 10
        return score

    def _format_item_reply(self, *, item: ItemRecord, mode: str) -> str:
        normalized_text = (item.normalized_text or "").strip()
        if mode == "full_text" and normalized_text:
            reply = f"这是 `{item.title}` 的全文：\n{normalized_text}"
        elif mode == "key_points":
            reply = f"`{item.title}` 的重点是：{item.summary}"
        else:
            if normalized_text and len(normalized_text) <= self.FULL_TEXT_REPLY_THRESHOLD:
                reply = f"`{item.title}` 内容不长，我直接给你全文：\n{normalized_text}"
            else:
                reply = f"`{item.title}` 的摘要是：{item.summary}"
        if item.locator_hint:
            reply += f"\n定位提示：{item.locator_hint}"
        return reply

    def _format_topic_reply(self, topic_matches: list[tuple[object, list[ItemRecord]]]) -> tuple[str, list[dict[str, Any]]]:
        lines: list[str] = []
        working_set: list[dict[str, Any]] = []
        first_topic, first_items = topic_matches[0]
        lines.append(f"我先按 topic 找到了最相关的主题：`{getattr(first_topic, 'name', '未命名主题')}`。")
        if getattr(first_topic, "summary", ""):
            lines.append(f"主题摘要：{getattr(first_topic, 'summary')}")
        rank = 1
        for topic, items in topic_matches:
            if not items:
                continue
            lines.append(f"主题 `{getattr(topic, 'name', '')}` 下的相关文件：")
            for item in items[:3]:
                lines.append(f"{rank}. {item.title} - {item.summary}")
                working_set.append(self._item_snapshot(item, rank=rank))
                rank += 1
        lines.append("你可以继续说“第一个给我全文”或者直接说文件名。")
        return "\n".join(lines), working_set

    def _single_short_item_from_topic_matches(self, topic_matches: list[tuple[object, list[ItemRecord]]]) -> ItemRecord | None:
        items: list[ItemRecord] = []
        for _, topic_items in topic_matches:
            items.extend(topic_items)
        unique: list[ItemRecord] = []
        seen: set[str] = set()
        for item in items:
            if item.id in seen:
                continue
            seen.add(item.id)
            unique.append(item)
        if len(unique) != 1:
            return None
        item = unique[0]
        normalized_text = (item.normalized_text or "").strip()
        if normalized_text and len(normalized_text) <= self.FULL_TEXT_REPLY_THRESHOLD:
            return item
        return None

    def _format_summary_reply(self, *, item: ItemRecord) -> str:
        normalized_text = (item.normalized_text or "").strip()
        if normalized_text and len(normalized_text) <= self.FULL_TEXT_REPLY_THRESHOLD:
            return f"`{item.title}` 内容不长，我直接把全文发你：\n{normalized_text}"
        reply = f"你说的是 `{item.title}`。\n我先给你一个摘要：{item.summary}"
        if item.locator_hint and not normalized_text:
            reply += f"\n定位提示：{item.locator_hint}"
        return reply

    def _search_archive_assets(self, *, query: str) -> ArchiveLookupResult | None:
        if self.archive_runner is None:
            return None
        lowered = (query or "").lower()
        if not lowered.strip():
            return None
        if not any(token in lowered for token in ("照片", "图片", "图像", "photo", "image", "jpg", "jpeg", "png")):
            return None
        return self.archive_runner.find_assets(query=query, limit=5)

    def _format_archive_lookup_reply(self, lookup: ArchiveLookupResult) -> tuple[str, list[dict[str, Any]]]:
        lines = [f"当前匹配到 {lookup.count} 条归档图片：", ""]
        working_set: list[dict[str, Any]] = []
        for index, result in enumerate(lookup.results, start=1):
            filename = str(result.get("filename") or "")
            topic = str(result.get("topic") or "")
            summary = str(result.get("summary") or "")
            description = str(result.get("description") or "")
            item = self._resolve_archive_result_to_item(result)
            lines.append(f"{index}. {filename} | topic={topic} | {summary or description or '无描述'}")
            snapshot = {
                "rank": index,
                "title": filename or f"归档图片 {index}",
                "summary": summary or description,
                "archive_record_id": str(result.get("id") or ""),
                "archive_topic": topic,
            }
            if item is not None:
                snapshot["item_id"] = item.id
                snapshot["item_type"] = item.item_type
            working_set.append(snapshot)
        return "\n".join(lines), working_set

    def _resolve_archive_result_to_item(self, result: dict[str, Any]) -> ItemRecord | None:
        record_id = str(result.get("id") or "").strip()
        resolved_path = str(result.get("resolved_path") or "").strip()
        for item in self.item_repository.list_all(current_only=True):
            metadata = item.metadata_json or {}
            if record_id and str(metadata.get("archive_record_id") or "").strip() == record_id:
                return item
            if resolved_path and str(metadata.get("stored_file_path") or "").strip() == resolved_path:
                return item
        return None

    @staticmethod
    def _item_snapshot(item: ItemRecord, *, rank: int) -> dict[str, Any]:
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

    @staticmethod
    def _extract_rank_from_text(text: str) -> int | None:
        mappings = {"第一个": 1, "第二个": 2, "第三个": 3, "1": 1, "2": 2, "3": 3}
        for phrase, rank in mappings.items():
            if phrase == text.strip() or phrase in text:
                return rank
        return None

    @staticmethod
    def looks_like_link(text: str) -> bool:
        value = text.strip()
        parsed = urlparse(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)

    @staticmethod
    def _detect_media_kind(*, upload: UploadFile | None) -> str | None:
        if upload is None or not (upload.filename or "").strip():
            return None
        suffix = Path(upload.filename or "").suffix.lower()
        if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
            return "image"
        return "file"
