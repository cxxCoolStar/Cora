from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlparse

from fastapi import UploadFile

from core.agent.runtime_state import ToolStateDelta
from core.archivefs.service import ArchiveLookupResult, ArchiveSkillScriptRunner
from core.clawbot.archive_domain import ArchiveDomainHandler
from core.clawbot.archive_operation_handlers import (
    ArchiveCaptureOperationHandler,
    ArchiveClarificationOperationHandler,
    ArchiveRetrieveOperationHandler,
)
from core.clawbot.planner import ToolPlan
from core.ingestion.service import IngestionService
from core.storage.models import ClarificationStateRecord, ItemRecord
from core.storage.repositories import (
    ChannelSessionMapRepository,
    ClarificationRepository,
    ItemRepository,
)
from core.tools import ToolInvocation
from core.topics.service import TopicOrganizerService


@dataclass(slots=True)
class AmbiguousItemReferenceError(Exception):
    query: str
    candidates: list[dict[str, Any]]


@dataclass(slots=True)
class ToolExecutionResult:
    reply: str
    action: str
    status: str = "completed"
    disposition: str = "continue"
    item_id: str | None = None
    needs_clarification: bool = False
    artifacts: list[dict[str, Any]] | None = None
    metadata: dict[str, Any] | None = None
    state_update: ToolStateDelta | None = None


@dataclass(slots=True)
class ArchiveToolRuntimeHost:
    ingestion_service: IngestionService
    item_repository: ItemRepository
    clarification_repository: ClarificationRepository
    topic_organizer: TopicOrganizerService | None = None
    archive_runner: ArchiveSkillScriptRunner | None = None
    gateway_service: Any | None = None
    session_map_repository: ChannelSessionMapRepository | None = None
    channel_name: str = "wechat"
    archive_tools: ArchiveDomainHandler | None = None
    archive_capture_ops: ArchiveCaptureOperationHandler | None = None
    archive_clarification_ops: ArchiveClarificationOperationHandler | None = None
    archive_retrieve_ops: ArchiveRetrieveOperationHandler | None = None

    FULL_TEXT_REPLY_THRESHOLD = 420

    def __post_init__(self) -> None:
        self.archive_tools = ArchiveDomainHandler(host=self)
        self.archive_capture_ops = ArchiveCaptureOperationHandler(host=self)
        self.archive_clarification_ops = ArchiveClarificationOperationHandler(host=self)
        self.archive_retrieve_ops = ArchiveRetrieveOperationHandler(host=self)

    def can_send_files_to_user(self) -> bool:
        return self.gateway_service is not None and self.session_map_repository is not None

    def _build_state_update(self, *, invocation: ToolInvocation, last_action: str) -> ToolStateDelta:
        return self.build_state_update(invocation=invocation, last_action=last_action)

    @staticmethod
    def _detect_media_kind(*, upload: UploadFile | None) -> str | None:
        return ArchiveToolRuntimeHost.detect_media_kind(upload=upload)

    @staticmethod
    def merge_recent_items(*snapshots: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
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

    def build_state_update(
        self,
        *,
        invocation: ToolInvocation,
        last_action: str,
    ) -> ToolStateDelta:
        current_source_event_id = str(invocation.context.get("current_source_event_id") or "").strip()
        return ToolStateDelta(
            last_action=last_action,
            current_source_event_id=current_source_event_id or None,
        )

    async def execute_archive(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return await self.archive_tools.execute_archive(invocation)

    async def execute_archive_state(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return await self.archive_tools.execute_archive_state(invocation)

    async def save_file(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return await self.archive_capture_ops.save_file(invocation)

    async def _tool_save_file(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return await self.save_file(invocation)

    async def save_content(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return await self.archive_capture_ops.save_content(invocation)

    async def _tool_save_content(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return await self.save_content(invocation)

    def overview_knowledge_base(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return self.archive_retrieve_ops.overview_knowledge_base(invocation)

    def _tool_overview_knowledge_base(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return self.overview_knowledge_base(invocation)

    def list_topics(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return self.archive_retrieve_ops.list_topics(invocation)

    def _tool_list_topics(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return self.list_topics(invocation)

    def open_topic(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return self.archive_retrieve_ops.open_topic(invocation)

    def _tool_open_topic(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return self.open_topic(invocation)

    def read_item(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return self.archive_retrieve_ops.read_item(invocation)

    def _tool_read_item(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return self.read_item(invocation)

    def summarize_item(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return self.archive_retrieve_ops.summarize_item(invocation)

    def _tool_summarize_item(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return self.summarize_item(invocation)

    def delete_item(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return self.archive_retrieve_ops.delete_item(invocation)

    def _tool_delete_item(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return self.delete_item(invocation)

    async def send_file_to_user(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return await self.archive_retrieve_ops.send_file_to_user(invocation)

    async def _tool_send_file_to_user(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return await self.send_file_to_user(invocation)

    def clarify_reference(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return self.archive_clarification_ops.clarify_reference(invocation)

    def _tool_clarify_reference(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return self.clarify_reference(invocation)

    def create_reference_clarification(
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

    def clarify_capture_intent(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return self.archive_clarification_ops.clarify_capture_intent(invocation)

    def _tool_clarify_capture_intent(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return self.clarify_capture_intent(invocation)

    async def resolve_pending(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return await self.archive_clarification_ops.resolve_pending(
            invocation,
            capture_handler=self.archive_capture_ops,
        )

    async def _tool_resolve_pending(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return await self.resolve_pending(invocation)

    async def persist_pending_upload(self, *, upload: UploadFile) -> dict[str, str]:
        return await self.archive_capture_ops.persist_pending_upload(upload=upload)

    async def persist_pending_upload_entry(self, *, upload: UploadFile, source_event_id: str | None) -> dict[str, str | None]:
        return await self.archive_capture_ops.persist_pending_upload_entry(upload=upload, source_event_id=source_event_id)

    def build_upload_clarification_question(self, *, upload: UploadFile) -> str:
        return self.archive_capture_ops.build_upload_clarification_question(upload=upload)

    async def resolve_pending_input_interpretation(
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

    def resolve_pending_selected_item(self, *, invocation: ToolInvocation, pending_payload: dict[str, Any]) -> ItemRecord | None:
        return self.archive_clarification_ops.resolve_pending_selected_item(
            invocation=invocation,
            pending_payload=pending_payload,
        )

    def _resolve_pending_selected_item(self, *, invocation: ToolInvocation, pending_payload: dict[str, Any]) -> ItemRecord | None:
        return self.resolve_pending_selected_item(invocation=invocation, pending_payload=pending_payload)

    def resolve_delivery_targets(
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

    def _resolve_delivery_targets(
        self,
        *,
        session_id: str,
        item: ItemRecord,
        user_text: str | None,
        limit: int = 9,
    ) -> list[dict[str, Any]]:
        return self.resolve_delivery_targets(session_id=session_id, item=item, user_text=user_text, limit=limit)

    def resolve_direct_delivery_target(self, *, item: ItemRecord) -> dict[str, Any] | None:
        return self.archive_retrieve_ops.resolve_direct_delivery_target(item=item)

    def _resolve_direct_delivery_target(self, *, item: ItemRecord) -> dict[str, Any] | None:
        return self.resolve_direct_delivery_target(item=item)

    def resolve_target_item(
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
        return self.resolve_candidate_item(
            session_id=session_id,
            user_text=user_text,
            title_hint=title_hint,
            purpose=purpose,
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
        return self.resolve_target_item(
            session_id=session_id,
            plan=plan,
            context=context,
            user_text=user_text,
            purpose=purpose,
        )

    def resolve_candidate_item(
        self,
        *,
        session_id: str,
        user_text: str | None,
        title_hint: str,
        purpose: str,
    ) -> ItemRecord:
        query = self.build_reference_query(user_text=user_text, title_hint=title_hint)
        if not query:
            raise KeyError("请告诉我要操作的文件名或更具体的描述。")
        preferred_types = self.preferred_item_types(user_text=user_text, purpose=purpose)
        candidates = self.find_candidate_items(
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

    def _create_reference_clarification(
        self,
        *,
        invocation: ToolInvocation,
        query: str,
        candidates: list[dict[str, Any]],
    ) -> ToolExecutionResult:
        return self.create_reference_clarification(
            invocation=invocation,
            query=query,
            candidates=candidates,
        )

    @staticmethod
    def build_reference_query(*, user_text: str | None, title_hint: str) -> str:
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

    def find_candidate_items(
        self,
        *,
        session_id: str,
        query: str,
        preferred_types: set[str],
        limit: int,
    ) -> list[tuple[ItemRecord, int]]:
        scored: list[tuple[ItemRecord, int]] = []
        for item in self.item_repository.list_by_session(session_id=session_id, current_only=True):
            score = self.candidate_match_score(item=item, query=query, preferred_types=preferred_types)
            if score > 0:
                scored.append((item, score))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:limit]

    @staticmethod
    def preferred_item_types(*, user_text: str | None, purpose: str) -> set[str]:
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
    def candidate_match_score(*, item: ItemRecord, query: str, preferred_types: set[str]) -> int:
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

    def format_item_reply(self, *, item: ItemRecord, mode: str) -> str:
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

    def _format_item_reply(self, *, item: ItemRecord, mode: str) -> str:
        return self.format_item_reply(item=item, mode=mode)

    def format_topic_reply(self, topic_matches: list[tuple[object, list[ItemRecord]]]) -> tuple[str, list[dict[str, Any]]]:
        lines: list[str] = []
        working_set: list[dict[str, Any]] = []
        first_topic, _ = topic_matches[0]
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
                working_set.append(self.item_snapshot(item, rank=rank))
                rank += 1
        lines.append("如果你想继续查看其中一条，请直接说文件名。")
        return "\n".join(lines), working_set

    def _format_topic_reply(self, topic_matches: list[tuple[object, list[ItemRecord]]]) -> tuple[str, list[dict[str, Any]]]:
        return self.format_topic_reply(topic_matches)

    def single_short_item_from_topic_matches(self, topic_matches: list[tuple[object, list[ItemRecord]]]) -> ItemRecord | None:
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

    def _single_short_item_from_topic_matches(self, topic_matches: list[tuple[object, list[ItemRecord]]]) -> ItemRecord | None:
        return self.single_short_item_from_topic_matches(topic_matches)

    def format_summary_reply(self, *, item: ItemRecord) -> str:
        normalized_text = (item.normalized_text or "").strip()
        if normalized_text and len(normalized_text) <= self.FULL_TEXT_REPLY_THRESHOLD:
            return f"`{item.title}` 内容不长，我直接把全文发你：\n{normalized_text}"
        reply = f"你说的是 `{item.title}`。\n我先给你一个摘要：{item.summary}"
        if item.locator_hint and not normalized_text:
            reply += f"\n定位提示：{item.locator_hint}"
        return reply

    def _format_summary_reply(self, *, item: ItemRecord) -> str:
        return self.format_summary_reply(item=item)

    def search_archive_assets(self, *, query: str) -> ArchiveLookupResult | None:
        if self.archive_runner is None:
            return None
        lowered = (query or "").lower()
        if not lowered.strip():
            return None
        if not any(token in lowered for token in ("照片", "图片", "图像", "photo", "image", "jpg", "jpeg", "png")):
            return None
        return self.archive_runner.find_assets(query=query, limit=5)

    def format_archive_lookup_reply(self, lookup: ArchiveLookupResult) -> tuple[str, list[dict[str, Any]]]:
        lines = [f"当前匹配到 {lookup.count} 条归档图片：", ""]
        working_set: list[dict[str, Any]] = []
        for index, result in enumerate(lookup.results, start=1):
            filename = str(result.get("filename") or "")
            topic = str(result.get("topic") or "")
            summary = str(result.get("summary") or "")
            description = str(result.get("description") or "")
            item = self.resolve_archive_result_to_item(result)
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

    def resolve_archive_result_to_item(self, result: dict[str, Any], session_id: str | None = None) -> ItemRecord | None:
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

    def _resolve_archive_result_to_item(self, result: dict[str, Any], session_id: str | None = None) -> ItemRecord | None:
        return self.resolve_archive_result_to_item(result, session_id=session_id)

    @staticmethod
    def item_snapshot(item: ItemRecord, *, rank: int) -> dict[str, Any]:
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
    def extract_rank_from_text(text: str) -> int | None:
        mappings = {"第一个": 1, "第二个": 2, "第三个": 3, "1": 1, "2": 2, "3": 3}
        for phrase, rank in mappings.items():
            if phrase == text.strip() or phrase in text:
                return rank
        return None

    def _extract_rank_from_text(self, text: str) -> int | None:
        return self.extract_rank_from_text(text)

    @staticmethod
    def looks_like_link(text: str) -> bool:
        value = text.strip()
        parsed = urlparse(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)

    @staticmethod
    def detect_media_kind(*, upload: UploadFile | None) -> str | None:
        if upload is None or not (upload.filename or "").strip():
            return None
        suffix = Path(upload.filename or "").suffix.lower()
        if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
            return "image"
        return "file"
