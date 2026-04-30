from __future__ import annotations

import json
from dataclasses import dataclass
import re
from typing import Any

from core.llm.base import ModelClient
from core.schemas.message import Message


@dataclass(slots=True)
class ToolPlan:
    tool: str
    arguments: dict[str, Any]
    reason: str
    source: str = "heuristic"


class AgentPlanner:
    """Choose a constrained archive tool based on the user turn and session state."""

    REFERENCE_CUES = (
        "这里面",
        "这里",
        "上面那个",
        "上面的",
        "刚才那个",
        "那个文件",
        "这个文件",
        "这个文档",
        "那个文档",
        "全文",
        "原文",
        "完整内容",
        "详细",
        "展开",
        "展开讲讲",
        "写了什么",
    )
    SUMMARY_CUES = ("总结", "概括", "提炼", "重点", "写了什么", "讲了什么", "说了什么")
    FULL_TEXT_CUES = ("全文", "原文", "完整", "全部内容", "具体内容")

    def __init__(self, model_client: ModelClient | None = None) -> None:
        self.model_client = model_client

    def plan(
        self,
        *,
        text: str | None,
        has_upload: bool,
        coarse_intent: str,
        context: dict[str, Any] | None,
    ) -> ToolPlan | None:
        content = (text or "").strip()
        if not content and not has_upload:
            return None

        heuristic = self._heuristic_plan(content=content, has_upload=has_upload, coarse_intent=coarse_intent, context=context or {})
        llm_plan = self._llm_plan(content=content, has_upload=has_upload, coarse_intent=coarse_intent, context=context or {})
        if llm_plan is not None:
            return self._sanitize_plan(llm_plan, fallback=heuristic)
        return heuristic

    def _heuristic_plan(
        self,
        *,
        content: str,
        has_upload: bool,
        coarse_intent: str,
        context: dict[str, Any],
    ) -> ToolPlan:
        if has_upload:
            return ToolPlan(tool="save_file", arguments={}, reason="File upload detected.")

        working_set = context.get("working_set") or []
        focus_item_id = context.get("focus_item_id")
        rank = self._extract_rank(content)
        if rank is not None and working_set:
            mode = "full_text" if self._wants_full_text(content) else "summary"
            tool = "get_item" if mode == "full_text" else "summarize_item"
            return ToolPlan(
                tool=tool,
                arguments={"target": {"type": "working_set_rank", "value": rank}, "mode": mode if tool == "get_item" else None, "style": "brief" if tool == "summarize_item" else None},
                reason="The user referenced a ranked search result.",
            )

        if self._looks_like_reference_followup(content):
            if focus_item_id:
                if self._wants_full_text(content):
                    return ToolPlan(
                        tool="get_item",
                        arguments={"target": {"type": "focus_item", "value": ""}, "mode": "full_text"},
                        reason="Follow-up question about the current focus item.",
                    )
                return ToolPlan(
                    tool="summarize_item",
                    arguments={"target": {"type": "focus_item", "value": ""}, "style": "brief"},
                    reason="Follow-up question about the current focus item.",
                )
            if len(working_set) > 1:
                return ToolPlan(
                    tool="clarify_reference",
                    arguments={"reference_text": content},
                    reason="The user referenced prior results, but multiple candidates are active.",
                )

        if coarse_intent == "retrieve":
            return ToolPlan(
                tool="search_items",
                arguments={"query": content, "top_k": 3},
                reason="The user is asking to find previously saved material.",
            )

        if coarse_intent == "organize" and focus_item_id:
            return ToolPlan(
                tool="summarize_item",
                arguments={"target": {"type": "focus_item", "value": ""}, "style": "brief"},
                reason="The user wants to summarize the current focus item.",
            )

        return ToolPlan(
            tool="save_text_or_link",
            arguments={"text": content, "force_type": "auto"},
            reason="Default archive action for incoming content.",
        )

    def _llm_plan(
        self,
        *,
        content: str,
        has_upload: bool,
        coarse_intent: str,
        context: dict[str, Any],
    ) -> ToolPlan | None:
        if self.model_client is None or not content:
            return None
        prompt = (
            "You are choosing one archive tool for a personal archive assistant.\n"
            "Allowed tools: save_text_or_link, save_file, search_items, get_item, summarize_item, clarify_reference.\n"
            "Rules:\n"
            "- If the user asks to find earlier material, use search_items.\n"
            "- If the user asks about a current file/result ('this', 'that', 'the second one', 'full text'), prefer get_item or summarize_item.\n"
            "- If the target is ambiguous and there are multiple active candidates, use clarify_reference.\n"
            "- Do not invent item IDs.\n"
            "- Return strict JSON with keys: tool, arguments, reason.\n"
        )
        state_json = json.dumps(
            {
                "has_upload": has_upload,
                "coarse_intent": coarse_intent,
                "focus_item_id": context.get("focus_item_id"),
                "working_set": context.get("working_set", [])[:5],
            },
            ensure_ascii=False,
        )
        response = self.model_client.generate(
            messages=[
                Message.system(session_id="agent-planner", content=prompt + "\nState:\n" + state_json),
                Message.user(session_id="agent-planner", content=content),
            ],
            tools=[],
        )
        raw = (response.assistant_text or "").strip()
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            fenced = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            try:
                payload = json.loads(fenced)
            except json.JSONDecodeError:
                return None
        tool = str(payload.get("tool") or "").strip()
        arguments = payload.get("arguments") or {}
        if not isinstance(arguments, dict):
            return None
        reason = str(payload.get("reason") or "LLM selected an archive tool.").strip()
        return ToolPlan(tool=tool, arguments=arguments, reason=reason, source="llm")

    def _sanitize_plan(self, plan: ToolPlan, *, fallback: ToolPlan) -> ToolPlan:
        allowed = {"save_text_or_link", "save_file", "search_items", "get_item", "summarize_item", "clarify_reference"}
        if plan.tool not in allowed:
            return fallback
        if plan.tool == "save_file":
            return ToolPlan(tool="save_file", arguments={}, reason=plan.reason or fallback.reason, source=plan.source)
        if plan.tool == "search_items":
            query = str(plan.arguments.get("query") or "").strip()
            if not query:
                return fallback
            top_k = int(plan.arguments.get("top_k") or 3)
            top_k = max(1, min(5, top_k))
            return ToolPlan(tool="search_items", arguments={"query": query, "top_k": top_k}, reason=plan.reason, source=plan.source)
        if plan.tool in {"get_item", "summarize_item"}:
            target = plan.arguments.get("target")
            if not isinstance(target, dict):
                return fallback
            target_type = str(target.get("type") or "").strip()
            target_value = target.get("value")
            if target_type not in {"item_id", "focus_item", "working_set_rank"}:
                return fallback
            args: dict[str, Any] = {"target": {"type": target_type, "value": target_value}}
            if plan.tool == "get_item":
                mode = str(plan.arguments.get("mode") or "summary").strip()
                if mode not in {"summary", "full_text", "key_points"}:
                    mode = "summary"
                args["mode"] = mode
            else:
                style = str(plan.arguments.get("style") or "brief").strip()
                if style not in {"brief", "structured", "interview_notes"}:
                    style = "brief"
                args["style"] = style
            return ToolPlan(tool=plan.tool, arguments=args, reason=plan.reason, source=plan.source)
        if plan.tool == "clarify_reference":
            return ToolPlan(
                tool="clarify_reference",
                arguments={"reference_text": str(plan.arguments.get("reference_text") or "").strip()},
                reason=plan.reason,
                source=plan.source,
            )
        text = str(plan.arguments.get("text") or "").strip()
        if not text:
            return fallback
        force_type = str(plan.arguments.get("force_type") or "auto").strip()
        if force_type not in {"auto", "text", "link"}:
            force_type = "auto"
        return ToolPlan(
            tool="save_text_or_link",
            arguments={"text": text, "force_type": force_type},
            reason=plan.reason,
            source=plan.source,
        )

    @classmethod
    def _extract_rank(cls, content: str) -> int | None:
        mapping = {
            "第一个": 1,
            "第二个": 2,
            "第三个": 3,
            "第1个": 1,
            "第2个": 2,
            "第3个": 3,
            "1号": 1,
            "2号": 2,
            "3号": 3,
        }
        for phrase, rank in mapping.items():
            if phrase in content:
                return rank
        match = re.search(r"第\s*([1-9])\s*个", content)
        if match:
            return int(match.group(1))
        return None

    @classmethod
    def _looks_like_reference_followup(cls, content: str) -> bool:
        return any(token in content for token in cls.REFERENCE_CUES)

    @classmethod
    def _wants_full_text(cls, content: str) -> bool:
        return any(token in content for token in cls.FULL_TEXT_CUES)

