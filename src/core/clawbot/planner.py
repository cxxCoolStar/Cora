from __future__ import annotations

import json
from dataclasses import dataclass
import logging
from typing import Any

from core.llm.base import ModelClient
from core.schemas.message import Message
from core.tools.toolsets import resolve_toolsets

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ToolPlan:
    tool: str
    arguments: dict[str, Any]
    reason: str
    source: str = "llm"


class AgentPlanner:
    """Choose a constrained archive tool based on the user turn and session state."""

    DEFAULT_TOOLSETS = [
        "archive_capture",
        "archive_search",
        "archive_read",
        "archive_delivery",
        "archive_state",
    ]

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
        llm_content = content or "[file upload]"

        if self.model_client is None:
            raise RuntimeError("AgentPlanner requires a model client; heuristic tool planning has been removed.")
        llm_plan = self._llm_plan(content=llm_content, has_upload=has_upload, coarse_intent=coarse_intent, context=context or {})
        if llm_plan is None:
            raise RuntimeError("AgentPlanner did not receive a usable LLM plan.")
        return self._sanitize_plan(llm_plan)

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
        allowed_tools = ", ".join(resolve_toolsets(self.DEFAULT_TOOLSETS))
        working_set = context.get("working_set", [])[:5]
        prompt = (
            "You are choosing one tool for an archive assistant.\n"
            f"Allowed tools: {allowed_tools}.\n"
            "You must reason over the active conversation state, not just the latest utterance.\n"
            "Decision policy:\n"
            "- If the user asks what exists in the archive, use archive with action=overview or action=list_topics.\n"
            "- If the user asks to find or open earlier material, prefer archive with action=open.\n"
            "- If the user refers to earlier results using ordinal cues, title fragments, or phrases like this/that/previous, treat that as a reference to the current working_set or recent_items rather than a new search.\n"
            "- If the user asks to read, open, inspect, or see the full content, prefer archive with action=read.\n"
            "- If the user asks to summarize, outline, or extract key points, prefer archive with action=summarize.\n"
            "- If the user asks you to send, forward, deliver, or let them receive a previously saved file, prefer archive with action=deliver.\n"
            "- If a ranked reference and a title hint both appear, treat that as a high-confidence reference and do not ask for clarification.\n"
            "- If the user submits new standalone text or a standalone URL, prefer archive with action=save.\n"
            "- Otherwise, new material should usually be saved with archive action=save.\n"
            "- Use archive_state with action=clarify_reference only when the target truly cannot be resolved from the current working_set or recent_items.\n"
            "- Do not invent item IDs.\n"
            "- Return strict JSON with keys: tool, reason, and optionally action, query, text, target_rank, target_title_hint, reference_strategy, mode, style, reference_text, caption.\n"
            "- Valid reference_strategy values are: working_set_selection, recent_item, direct_item_id, ambiguous_reference, auto.\n"
        )
        state_json = json.dumps(
            {
                "has_upload": has_upload,
                "coarse_intent": coarse_intent,
                "primary_focus": context.get("primary_focus"),
                "last_action": context.get("last_action"),
                "working_set": [
                    {
                        "rank": snapshot.get("rank"),
                        "item_id": snapshot.get("item_id"),
                        "title": snapshot.get("title"),
                        "summary": snapshot.get("summary"),
                    }
                    for snapshot in working_set
                ],
                "recent_items": [
                    {
                        "item_id": snapshot.get("item_id"),
                        "title": snapshot.get("title"),
                        "summary": snapshot.get("summary"),
                    }
                    for snapshot in (context.get("recent_items") or [])[:5]
                    if isinstance(snapshot, dict)
                ],
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
        logger.info("planner llm_raw_output content=%s output=%s", content[:120], raw[:1000])
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
        arguments = self._build_llm_arguments(payload=payload, content=content)
        reason = str(payload.get("reason") or "LLM selected an archive tool.").strip()
        logger.info(
            "planner llm_decision content=%s tool=%s reason=%s arguments=%s",
            content[:120],
            tool,
            reason[:240],
            json.dumps(arguments, ensure_ascii=False),
        )
        return ToolPlan(tool=tool, arguments=arguments, reason=reason, source="llm")

    @staticmethod
    def _build_llm_arguments(*, payload: dict[str, Any], content: str) -> dict[str, Any]:
        arguments = payload.get("arguments") or {}
        if not isinstance(arguments, dict):
            arguments = {}

        tool = str(payload.get("tool") or "").strip()
        action = str(payload.get("action") or arguments.get("action") or "").strip()
        if tool == "archive":
            if action == "save":
                return {
                    "action": "save",
                    "text": str(payload.get("text") or arguments.get("text") or content).strip(),
                }
            if action == "open":
                query = str(payload.get("query") or arguments.get("query") or content).strip()
                top_k = payload.get("top_k") or arguments.get("top_k") or 3
                return {"action": "open", "query": query, "top_k": top_k}
            if action in {"read", "summarize", "deliver"}:
                target = arguments.get("target")
                if not isinstance(target, dict):
                    reference_strategy = str(payload.get("reference_strategy") or "").strip()
                    if reference_strategy == "recent_item":
                        target = {"type": "recent_item", "value": 1}
                    elif reference_strategy == "direct_item_id":
                        target = {"type": "item_id", "value": str(payload.get("target_item_id") or "").strip()}
                    elif payload.get("target_rank") is not None:
                        target = {"type": "working_set_rank", "value": payload.get("target_rank")}
                    else:
                        target = {"type": "auto", "value": ""}
                built: dict[str, Any] = {"action": action, "target": target}
                if action == "read":
                    built["mode"] = str(payload.get("mode") or arguments.get("mode") or "summary").strip()
                elif action == "summarize":
                    built["style"] = str(payload.get("style") or arguments.get("style") or "brief").strip()
                elif action == "deliver":
                    caption = str(payload.get("caption") or arguments.get("caption") or "").strip()
                    if caption:
                        built["caption"] = caption
                if payload.get("target_title_hint"):
                    built["target_title_hint"] = str(payload.get("target_title_hint"))
                return built
            if action in {"overview", "list_topics"}:
                return {"action": action}

        if tool == "archive_state":
            if action == "clarify_reference":
                return {
                    "action": "clarify_reference",
                    "reference_text": str(payload.get("reference_text") or arguments.get("reference_text") or content).strip(),
                }
            if action == "clarify_capture_intent":
                return {
                    "action": "clarify_capture_intent",
                    "question": str(payload.get("question") or arguments.get("question") or "").strip(),
                }
            if action == "resolve_pending":
                built = {
                    "action": "resolve_pending",
                    "resolution": str(payload.get("resolution") or arguments.get("resolution") or "").strip(),
                }
                if payload.get("note") or arguments.get("note"):
                    built["note"] = str(payload.get("note") or arguments.get("note") or "")
                target = arguments.get("target")
                if isinstance(target, dict):
                    built["target"] = target
                if payload.get("mode") or arguments.get("mode"):
                    built["mode"] = str(payload.get("mode") or arguments.get("mode") or "")
                return built

        return arguments

    def _sanitize_plan(self, plan: ToolPlan) -> ToolPlan:
        normalized_tool = plan.tool
        allowed = set(resolve_toolsets(self.DEFAULT_TOOLSETS))
        if normalized_tool not in allowed:
            raise ValueError(f"Planner selected unsupported tool: {normalized_tool}")

        if normalized_tool == "archive":
            action = str(plan.arguments.get("action") or "").strip()
            if action in {"overview", "list_topics"}:
                return ToolPlan(tool="archive", arguments={"action": action}, reason=plan.reason, source=plan.source)
            if action == "open":
                query = str(plan.arguments.get("query") or "").strip()
                if not query:
                    raise ValueError("Planner selected archive.open without a query.")
                top_k = int(plan.arguments.get("top_k") or 3)
                top_k = max(1, min(5, top_k))
                return ToolPlan(tool="archive", arguments={"action": "open", "query": query, "top_k": top_k}, reason=plan.reason, source=plan.source)
            if action in {"read", "summarize", "deliver"}:
                target = plan.arguments.get("target")
                if not isinstance(target, dict):
                    raise ValueError(f"Planner selected archive.{action} without a target object.")
                target_type = str(target.get("type") or "").strip()
                target_value = target.get("value")
                if target_type == "focus_item":
                    target_type = "recent_item"
                    target_value = 1
                if target_type not in {"item_id", "working_set_rank", "recent_item", "auto"}:
                    raise ValueError(f"Planner selected archive.{action} with invalid target type: {target_type}")
                args: dict[str, Any] = {"action": action, "target": {"type": target_type, "value": target_value}}
                title_hint = str(plan.arguments.get("target_title_hint") or "").strip()
                if title_hint:
                    args["target_title_hint"] = title_hint
                if action == "read":
                    mode = str(plan.arguments.get("mode") or "summary").strip()
                    if mode not in {"summary", "full_text", "key_points"}:
                        mode = "summary"
                    args["mode"] = mode
                elif action == "summarize":
                    style = str(plan.arguments.get("style") or "brief").strip()
                    if style not in {"brief", "structured", "interview_notes"}:
                        style = "brief"
                    args["style"] = style
                else:
                    caption = str(plan.arguments.get("caption") or "").strip()
                    if caption:
                        args["caption"] = caption
                return ToolPlan(tool="archive", arguments=args, reason=plan.reason, source=plan.source)
            text = str(plan.arguments.get("text") or "").strip()
            if action == "save" and text:
                return ToolPlan(tool="archive", arguments={"action": "save", "text": text}, reason=plan.reason, source=plan.source)
            raise ValueError(f"Planner selected archive with unsupported action: {action}")

        if normalized_tool == "archive_state":
            action = str(plan.arguments.get("action") or "").strip()
            if action == "clarify_reference":
                return ToolPlan(
                    tool="archive_state",
                    arguments={"action": "clarify_reference", "reference_text": str(plan.arguments.get("reference_text") or "").strip()},
                    reason=plan.reason,
                    source=plan.source,
                )
            if action == "clarify_capture_intent":
                return ToolPlan(
                    tool="archive_state",
                    arguments={"action": "clarify_capture_intent", "question": str(plan.arguments.get("question") or "").strip()},
                    reason=plan.reason,
                    source=plan.source,
                )
            if action == "resolve_pending":
                args = {
                    "action": "resolve_pending",
                    "resolution": str(plan.arguments.get("resolution") or "").strip(),
                }
                if "note" in plan.arguments:
                    args["note"] = str(plan.arguments.get("note") or "")
                if isinstance(plan.arguments.get("target"), dict):
                    args["target"] = dict(plan.arguments["target"])
                if plan.arguments.get("mode"):
                    args["mode"] = str(plan.arguments["mode"])
                return ToolPlan(tool="archive_state", arguments=args, reason=plan.reason, source=plan.source)
            raise ValueError(f"Planner selected archive_state with unsupported action: {action}")

        raise ValueError(f"Planner selected unsupported tool: {normalized_tool}")
