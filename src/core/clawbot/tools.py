from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import UploadFile

from core.clawbot.planner import ToolPlan
from core.ingestion.service import IngestedItemResult, IngestionService
from core.retrieval.service import RetrievalResult, RetrievalService
from core.storage.models import ItemRecord
from core.storage.repositories import ClarificationRepository, ItemRepository


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
        retrieval_service: RetrievalService,
        item_repository: ItemRepository,
        clarification_repository: ClarificationRepository,
    ) -> None:
        self.ingestion_service = ingestion_service
        self.retrieval_service = retrieval_service
        self.item_repository = item_repository
        self.clarification_repository = clarification_repository

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
        if plan.tool == "save_file":
            saved = await self.ingestion_service.ingest(
                session_id=session_id,
                source_message_id=source_message_id,
                text=None,
                upload=upload,
            )
            return ToolExecutionResult(reply=saved.reply, action="capture", item_id=saved.item_id)

        if plan.tool == "save_text_or_link":
            saved = await self.ingestion_service.ingest(
                session_id=session_id,
                source_message_id=source_message_id,
                text=str(plan.arguments.get("text") or text or ""),
                upload=None,
            )
            return ToolExecutionResult(reply=saved.reply, action="capture", item_id=saved.item_id)

        if plan.tool == "search_items":
            candidates = self.retrieval_service.search_candidates(
                session_id=session_id,
                query=str(plan.arguments.get("query") or text or ""),
                top_k=int(plan.arguments.get("top_k") or 3),
            )
            if not candidates:
                return ToolExecutionResult(
                    reply="我没有找到相关的已保存资料。你可以先发送内容给我保存，再来查询。",
                    action="retrieve",
                    metadata={"context": {"working_set": [], "focus_item_id": None, "last_action": "search_items"}},
                )
            reply = self._format_search_reply(candidates)
            working_set = [self._item_snapshot(result.item, rank=index + 1) for index, result in enumerate(candidates)]
            focus_item_id = candidates[0].item.id
            return ToolExecutionResult(
                reply=reply,
                action="retrieve",
                item_id=focus_item_id,
                metadata={
                    "context": {
                        "working_set": working_set,
                        "focus_item_id": focus_item_id,
                        "last_action": "search_items",
                    }
                },
            )

        if plan.tool == "get_item":
            item = self._resolve_target_item(session_id=session_id, plan=plan, context=context)
            mode = str(plan.arguments.get("mode") or "summary")
            reply = self._format_item_reply(item=item, mode=mode)
            return ToolExecutionResult(
                reply=reply,
                action="retrieve",
                item_id=item.id,
                metadata={
                    "context": {
                        "working_set": context.get("working_set", []),
                        "focus_item_id": item.id,
                        "last_action": "get_item",
                    }
                },
            )

        if plan.tool == "summarize_item":
            item = self._resolve_target_item(session_id=session_id, plan=plan, context=context)
            reply = self._format_summary_reply(item=item)
            return ToolExecutionResult(
                reply=reply,
                action="organize",
                item_id=item.id,
                metadata={
                    "context": {
                        "working_set": context.get("working_set", []),
                        "focus_item_id": item.id,
                        "last_action": "summarize_item",
                    }
                },
            )

        if plan.tool == "clarify_reference":
            candidates = context.get("working_set", [])
            labels = [snapshot.get("title", f"候选 {index + 1}") for index, snapshot in enumerate(candidates[:3])]
            question = "你想看哪一条资料？" if labels else "你想让我展开哪一条资料？"
            if labels:
                question += " " + "；".join(f"{index + 1}. {label}" for index, label in enumerate(labels))
            self.clarification_repository.create(
                session_id=session_id,
                source_message_id=source_message_id,
                question=question,
                candidate_intents=["reference_resolution"],
                pending_payload={
                    "type": "reference_resolution",
                    "reference_text": plan.arguments.get("reference_text") or text or "",
                    "working_set": candidates[:5],
                },
            )
            return ToolExecutionResult(reply=question, action="clarify", needs_clarification=True)

        return ToolExecutionResult(reply="我暂时还不能处理这个请求。", action="chat")

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

    def _format_search_reply(self, candidates: list[RetrievalResult]) -> str:
        best = candidates[0]
        normalized_text = (best.item.normalized_text or "").strip()
        if len(candidates) == 1 and normalized_text and len(normalized_text) <= self.FULL_TEXT_REPLY_THRESHOLD:
            reply = (
                f"我找到了一条相关资料：`{best.item.title}`。\n"
                f"内容较短，直接给你全文：\n{normalized_text}"
            )
            if best.item.locator_hint:
                reply += f"\n定位提示：{best.item.locator_hint}"
            return reply

        lines = [f"我找到了 {len(candidates)} 条相关资料，最相关的是：`{best.item.title}`。", f"摘要：{best.item.summary}"]
        for index, candidate in enumerate(candidates, start=1):
            lines.append(f"{index}. {candidate.item.title} - {candidate.item.summary}")
        lines.append("你可以继续说“第一个给我全文”或者“第二个展开讲讲”。")
        if best.item.locator_hint:
            lines.append(f"定位提示：{best.item.locator_hint}")
        return "\n".join(lines)

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
