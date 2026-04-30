from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any
from urllib.parse import urlparse

from fastapi import UploadFile

from core.clawbot.planner import ToolPlan
from core.ingestion.service import IngestionService
from core.storage.models import ItemRecord
from core.storage.repositories import ClarificationRepository, ItemRepository
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
    ) -> None:
        self.ingestion_service = ingestion_service
        self.item_repository = item_repository
        self.clarification_repository = clarification_repository
        self.topic_organizer = topic_organizer
        register_builtin_tools()

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

    async def _tool_save_file(self, invocation: ToolInvocation) -> ToolExecutionResult:
        saved = await self.ingestion_service.ingest(
            session_id=invocation.session_id,
            source_message_id=invocation.source_message_id,
            text=None,
            upload=invocation.upload,
        )
        return ToolExecutionResult(reply=saved.reply, action="capture", item_id=saved.item_id)

    async def _tool_save_text(self, invocation: ToolInvocation) -> ToolExecutionResult:
        saved = await self.ingestion_service.ingest(
            session_id=invocation.session_id,
            source_message_id=invocation.source_message_id,
            text=str(invocation.plan.arguments.get("text") or invocation.text or ""),
            upload=None,
        )
        return ToolExecutionResult(reply=saved.reply, action="capture", item_id=saved.item_id)

    async def _tool_save_link(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return await self._tool_save_text(invocation)

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
        if self.topic_organizer is None:
            return ToolExecutionResult(reply="当前还没有可用的 topic/wiki 索引。", action="retrieve")
        topic_matches = self.topic_organizer.search_topics(
            session_id=invocation.session_id,
            query=str(invocation.plan.arguments.get("query") or invocation.text or ""),
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
                return ToolExecutionResult(
                    reply=reply,
                    action="retrieve",
                    item_id=single_item.id,
                    metadata={
                        "context": {
                            "working_set": [self._item_snapshot(single_item, rank=1)],
                            "focus_item_id": single_item.id,
                            "last_action": "open_topic",
                        }
                    },
                )
            reply, working_set = self._format_topic_reply(topic_matches)
            focus_item_id = working_set[0]["item_id"] if working_set else None
            return ToolExecutionResult(
                reply=reply,
                action="retrieve",
                item_id=focus_item_id,
                metadata={
                    "context": {
                        "working_set": working_set,
                        "focus_item_id": focus_item_id,
                        "last_action": "open_topic",
                    }
                },
            )
        logger.info(
            "tool open_topic miss session_id=%s query=%s",
            invocation.session_id,
            str(invocation.plan.arguments.get("query") or invocation.text or "")[:160],
        )
        return ToolExecutionResult(
            reply="我没有在当前的 topic/wiki 索引里找到相关资料。请先确认这份内容已经被归档，或者让我重新整理知识库。",
            action="retrieve",
            metadata={
                "context": {
                    "working_set": [],
                    "focus_item_id": None,
                    "last_action": "open_topic",
                }
            },
        )

    def _tool_read_item(self, invocation: ToolInvocation) -> ToolExecutionResult:
        item = self._resolve_target_item(session_id=invocation.session_id, plan=invocation.plan, context=invocation.context)
        mode = str(invocation.plan.arguments.get("mode") or "summary")
        reply = self._format_item_reply(item=item, mode=mode)
        return ToolExecutionResult(
            reply=reply,
            action="retrieve",
            item_id=item.id,
            metadata={
                "context": {
                    "working_set": invocation.context.get("working_set", []),
                    "focus_item_id": item.id,
                    "last_action": "read_item",
                }
            },
        )

    def _tool_summarize_item(self, invocation: ToolInvocation) -> ToolExecutionResult:
        item = self._resolve_target_item(session_id=invocation.session_id, plan=invocation.plan, context=invocation.context)
        reply = self._format_summary_reply(item=item)
        return ToolExecutionResult(
            reply=reply,
            action="organize",
            item_id=item.id,
            metadata={
                "context": {
                    "working_set": invocation.context.get("working_set", []),
                    "focus_item_id": item.id,
                    "last_action": "summarize_item",
                }
            },
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

    def try_resolve_reference_clarification(self, *, text: str, pending_payload: dict[str, Any]) -> ItemRecord | None:
        working_set = pending_payload.get("working_set") or []
        if not isinstance(working_set, list):
            return None
        content = text.strip()
        rank = self._extract_rank_from_text(content)
        if rank is not None and 1 <= rank <= len(working_set):
            item_id = str((working_set[rank - 1] or {}).get("item_id") or "").strip()
            if item_id:
                return self.item_repository.get(item_id=item_id, session_id=str((working_set[rank - 1] or {}).get("session_id") or pending_payload.get("session_id") or ""))
        lowered = content.lower()
        for snapshot in working_set:
            title = str(snapshot.get("title") or "")
            if title and (title in content or title.lower() in lowered):
                item_id = str(snapshot.get("item_id") or "").strip()
                session_id = str(snapshot.get("session_id") or pending_payload.get("session_id") or "").strip()
                if item_id and session_id:
                    return self.item_repository.get(item_id=item_id, session_id=session_id)
        return None

    def _resolve_target_item(self, *, session_id: str, plan: ToolPlan, context: dict[str, Any]) -> ItemRecord:
        target = plan.arguments.get("target") or {}
        target_type = str(target.get("type") or "focus_item").strip()
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
        focus_item_id = str(context.get("focus_item_id") or "").strip()
        if not focus_item_id:
            raise KeyError("No focus item is available for this follow-up.")
        return self.item_repository.get_any(item_id=focus_item_id)

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
            return f"这份资料 `{item.title}` 内容不长，我直接把全文发你：\n{normalized_text}"
        reply = f"这份资料是：`{item.title}`。\n我先给你一个更清晰的摘要：{item.summary}"
        if item.locator_hint:
            reply += f"\n定位提示：{item.locator_hint}"
        return reply

    @staticmethod
    def _item_snapshot(item: ItemRecord, *, rank: int) -> dict[str, Any]:
        return {
            "item_id": item.id,
            "session_id": item.session_id,
            "title": item.title,
            "summary": item.summary,
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
