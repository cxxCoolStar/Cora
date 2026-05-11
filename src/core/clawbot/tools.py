from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlparse

from fastapi import UploadFile

from core.archivefs.service import ArchiveLookupResult, ArchiveSkillScriptRunner
from core.clawbot.archive_domain import ArchiveDomainHandler
from core.clawbot.archive_operation_handlers import (
    ArchiveCaptureOperationHandler,
    ArchiveClarificationOperationHandler,
    ArchiveRetrieveOperationHandler,
)
from core.clawbot.planner import ToolPlan
from core.clawbot.tool_domains import FileToolHandler, SkillToolHandler, UserMemoryToolHandler
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


@dataclass(slots=True)
class AmbiguousItemReferenceError(Exception):
    query: str
    candidates: list[dict[str, Any]]


class RuntimeToolExecutor:
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
        user_memory_path: Path | None = None,
        file_tool_root: Path | None = None,
        skill_roots: list[Path] | None = None,
    ) -> None:
        self.ingestion_service = ingestion_service
        self.item_repository = item_repository
        self.clarification_repository = clarification_repository
        self.topic_organizer = topic_organizer
        self.archive_runner = archive_runner
        self.gateway_service = gateway_service
        self.session_map_repository = session_map_repository
        self.channel_name = channel_name
        self.archive_tools = ArchiveDomainHandler(host=self)
        self.archive_capture_ops = ArchiveCaptureOperationHandler(host=self)
        self.archive_clarification_ops = ArchiveClarificationOperationHandler(host=self)
        self.archive_retrieve_ops = ArchiveRetrieveOperationHandler(host=self)
        self.user_memory_tools = UserMemoryToolHandler.from_path(user_memory_path or Path("user-memory/USER.md"))
        self.file_tools = FileToolHandler.from_root(file_tool_root or Path("."))
        self.skill_tools = SkillToolHandler.from_roots(skill_roots)
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
        last_action: str,
    ) -> dict[str, Any]:
        context = {
            "recent_events": invocation.context.get("recent_events", []),
            "last_action": last_action,
        }
        current_source_event_id = str(invocation.context.get("current_source_event_id") or "").strip()
        if current_source_event_id:
            context["current_source_event_id"] = current_source_event_id
        return context

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
        return await self.archive_tools.execute_archive(invocation)

    async def _tool_archive_state(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return await self.archive_tools.execute_archive_state(invocation)

    def _tool_user_memory(self, invocation: ToolInvocation) -> ToolExecutionResult:
        result = self.user_memory_tools.execute(invocation)
        return ToolExecutionResult(reply=result.reply, action=result.action)

    def _tool_list_files(self, invocation: ToolInvocation) -> ToolExecutionResult:
        result = self.file_tools.list_files(invocation)
        return ToolExecutionResult(reply=result.reply, action=result.action)

    def _tool_search_files(self, invocation: ToolInvocation) -> ToolExecutionResult:
        result = self.file_tools.search_files(invocation)
        return ToolExecutionResult(reply=result.reply, action=result.action)

    def _tool_read_file(self, invocation: ToolInvocation) -> ToolExecutionResult:
        result = self.file_tools.read_file(invocation)
        return ToolExecutionResult(reply=result.reply, action=result.action)

    def _tool_skills_list(self, invocation: ToolInvocation) -> ToolExecutionResult:
        result = self.skill_tools.list_skills(invocation)
        return ToolExecutionResult(reply=result.reply, action=result.action)

    def _tool_skill_view(self, invocation: ToolInvocation) -> ToolExecutionResult:
        result = self.skill_tools.view_skill(invocation)
        return ToolExecutionResult(reply=result.reply, action=result.action)

    async def _tool_save_file(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return await self.archive_capture_ops.save_file(invocation)

    async def _tool_save_content(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return await self.archive_capture_ops.save_content(invocation)

    def _tool_overview_knowledge_base(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return self.archive_retrieve_ops.overview_knowledge_base(invocation)

    def _tool_list_topics(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return self.archive_retrieve_ops.list_topics(invocation)

    def _tool_open_topic(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return self.archive_retrieve_ops.open_topic(invocation)

    def _tool_read_item(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return self.archive_retrieve_ops.read_item(invocation)

    def _tool_summarize_item(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return self.archive_retrieve_ops.summarize_item(invocation)

    def _tool_delete_item(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return self.archive_retrieve_ops.delete_item(invocation)

    def _tool_clarify_reference(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return self.archive_clarification_ops.clarify_reference(invocation)

    def _create_reference_clarification(
        self,
        *,
        invocation: ToolInvocation,
        query: str,
        candidates: list[dict[str, Any]],
    ) -> ToolExecutionResult:
        return self.archive_clarification_ops.create_reference_clarification(
            invocation=invocation,
            query=query,
            candidates=candidates,
        )

    def _tool_clarify_capture_intent(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return self.archive_clarification_ops.clarify_capture_intent(invocation)

    async def _tool_resolve_pending(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return await self.archive_clarification_ops.resolve_pending(
            invocation,
            capture_handler=self.archive_capture_ops,
        )

    async def _persist_pending_upload(self, *, upload: UploadFile) -> dict[str, str]:
        return await self.archive_capture_ops.persist_pending_upload(upload=upload)

    async def persist_pending_upload_entry(self, *, upload: UploadFile, source_event_id: str | None) -> dict[str, str | None]:
        return await self.archive_capture_ops.persist_pending_upload_entry(upload=upload, source_event_id=source_event_id)

    def _build_upload_clarification_question(self, *, upload: UploadFile) -> str:
        return self.archive_capture_ops.build_upload_clarification_question(upload=upload)

    async def _resolve_pending_input_interpretation(
        self,
        *,
        invocation: ToolInvocation,
        pending: ClarificationStateRecord,
        pending_payload: dict[str, Any],
        note: str,
    ) -> ToolExecutionResult:
        return await self.archive_clarification_ops.resolve_pending_input_interpretation(
            invocation=invocation,
            pending=pending,
            pending_payload=pending_payload,
            note=note,
            capture_handler=self.archive_capture_ops,
        )

    async def _tool_send_file_to_user(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return await self.archive_retrieve_ops.send_file_to_user(invocation)

    def _resolve_delivery_targets(
        self,
        *,
        session_id: str,
        item: ItemRecord,
        user_text: str | None,
        limit: int = 9,
    ) -> list[dict[str, Any]]:
        return self.archive_retrieve_ops.resolve_delivery_targets(
            session_id=session_id,
            item=item,
            user_text=user_text,
            limit=limit,
        )

    def _resolve_direct_delivery_target(self, *, item: ItemRecord) -> dict[str, Any] | None:
        return self.archive_retrieve_ops.resolve_direct_delivery_target(item=item)

    def _resolve_pending_selected_item(self, *, invocation: ToolInvocation, pending_payload: dict[str, Any]) -> ItemRecord | None:
        return self.archive_clarification_ops.resolve_pending_selected_item(
            invocation=invocation,
            pending_payload=pending_payload,
        )

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
        title_hint = str(plan.arguments.get("target_title_hint") or "").strip()
        return self._resolve_candidate_item(
            session_id=session_id,
            user_text=user_text,
            title_hint=title_hint,
            purpose=purpose,
        )

    def _resolve_candidate_item(
        self,
        *,
        session_id: str,
        user_text: str | None,
        title_hint: str,
        purpose: str,
    ) -> ItemRecord:
        query = self._build_reference_query(user_text=user_text, title_hint=title_hint)
        if not query:
            raise KeyError("请告诉我要操作的文件名或更具体的描述。")
        preferred_types = self._preferred_item_types(user_text=user_text, purpose=purpose)
        candidates = self._find_candidate_items(
            session_id=session_id,
            query=query,
            preferred_types=preferred_types,
            limit=5,
        )
        if not candidates:
            raise KeyError("我没有找到匹配的资料，请换个文件名或描述试试。")
        top_item, top_score = candidates[0]
        if top_score < 30:
            raise KeyError("我没法可靠定位到这份资料，请说得更具体一些。")
        if len(candidates) > 1:
            second_score = candidates[1][1]
            if second_score >= max(30, top_score - 10):
                raise AmbiguousItemReferenceError(
                    query=query,
                    candidates=[
                        {"item_id": item.id, "title": item.title, "summary": item.summary}
                        for item, _score in candidates[:3]
                    ],
                )
        return top_item

    @staticmethod
    def _build_reference_query(*, user_text: str | None, title_hint: str) -> str:
        if title_hint.strip():
            return title_hint.strip()
        text = (user_text or "").strip()
        if not text:
            return ""
        cleaned = text
        for phrase in [
            "帮我找一下",
            "帮我找",
            "帮我查一下",
            "帮我查",
            "请帮我",
            "把",
            "发给我",
            "发送给我",
            "给我看",
            "给我发",
            "打开",
            "查看",
            "看看",
            "读取",
            "删除",
            "删掉",
            "移除",
            "总结",
            "概括",
        ]:
            cleaned = cleaned.replace(phrase, " ")
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ，。！？,.")
        generic_only = {
            "",
            "这个",
            "那个",
            "它",
            "这份",
            "那份",
            "这个文件",
            "那个文件",
            "这里面",
            "这里面写了什么",
            "上一个",
            "上一条",
            "刚才那个",
        }
        if cleaned in generic_only:
            return ""
        return cleaned

    def _find_candidate_items(
        self,
        *,
        session_id: str,
        query: str,
        preferred_types: set[str],
        limit: int,
    ) -> list[tuple[ItemRecord, int]]:
        scored: list[tuple[ItemRecord, int]] = []
        for item in self.item_repository.list_by_session(session_id=session_id, current_only=True):
            score = self._candidate_match_score(item=item, query=query, preferred_types=preferred_types)
            if score > 0:
                scored.append((item, score))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:limit]

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
        lines.append("如果你想继续查看其中一条，请直接说文件名。")
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

    def _resolve_archive_result_to_item(self, result: dict[str, Any], session_id: str | None = None) -> ItemRecord | None:
        record_id = str(result.get("id") or "").strip()
        resolved_path = str(result.get("resolved_path") or "").strip()
        for item in self.item_repository.list_all(current_only=True):
            if session_id is not None and item.session_id != session_id:
                continue
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
