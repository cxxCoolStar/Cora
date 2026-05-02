from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlparse

from fastapi import UploadFile

from core.clawbot.planner import ToolPlan
from core.ingestion.service import IngestionService
from core.storage.models import ItemRecord
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
        gateway_service: Any | None = None,
        session_map_repository: ChannelSessionMapRepository | None = None,
        channel_name: str = "wechat",
    ) -> None:
        self.ingestion_service = ingestion_service
        self.item_repository = item_repository
        self.clarification_repository = clarification_repository
        self.topic_organizer = topic_organizer
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

    async def _tool_save_file(self, invocation: ToolInvocation) -> ToolExecutionResult:
        if invocation.upload is None:
            return ToolExecutionResult(
                reply="没有可保存的文件。如果你的意图是保存文本内容，请使用 save_content 工具。",
                action="chat",
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

    async def _tool_send_file_to_user(self, invocation: ToolInvocation) -> ToolExecutionResult:
        """Send a file from the wiki to the user via WeChat."""
        if self.gateway_service is None:
            return ToolExecutionResult(reply="??????????", action="chat")

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
                return ToolExecutionResult(reply=f"??????????: {e}", action="chat")
            try:
                item = self._resolve_item_by_title_hint(
                    session_id=invocation.session_id,
                    title_hint=title_hint,
                    context=invocation.context,
                )
            except KeyError:
                return ToolExecutionResult(reply=f"??????????: {e}", action="chat")

        metadata = item.metadata_json or {}
        stored_path = metadata.get("stored_file_path")
        if not stored_path:
            return ToolExecutionResult(
                reply=f"?? `{item.title}` ???????????????????????????",
                action="chat",
            )

        path = Path(stored_path)
        if not path.exists():
            return ToolExecutionResult(
                reply=f"?? `{item.title}` ???????????????????",
                action="chat",
            )

        caption = str(invocation.plan.arguments.get("caption") or "").strip()
        if self.session_map_repository is None:
            return ToolExecutionResult(reply="???????????????", action="chat")

        user_id = self.session_map_repository.get_external_user_id(
            channel=self.channel_name,
            session_id=invocation.session_id,
        )
        if not user_id:
            return ToolExecutionResult(
                reply="?????????????????????????????",
                action="chat",
            )

        try:
            result = await self.gateway_service.send_file_to_user(
                user_id=user_id,
                file_path=str(path),
                caption=caption or f"?????????{item.title}",
            )
            if result.get("ret") in (0, None) and result.get("errcode") in (0, None):
                context = self._build_context(
                    invocation=invocation,
                    item=item,
                    last_action="send_file_to_user",
                    working_set=invocation.context.get("working_set", []),
                )
                return ToolExecutionResult(
                    reply=f"?? `{item.title}` ??????",
                    action="retrieve",
                    item_id=item.id,
                    metadata={"context": context},
                )
            error_msg = result.get("errmsg") or result.get("msg") or "????"
            return ToolExecutionResult(reply=f"???????{error_msg}", action="chat")
        except Exception as exc:
            logger.exception("Failed to send file to user")
            return ToolExecutionResult(reply=f"???????{exc}", action="chat")

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
