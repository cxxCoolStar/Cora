from __future__ import annotations

import json
from typing import Any

from core.schemas.message import Message


def _build_tool_loop_system_prompt() -> str:
    return (
        "You are Cora, a personal wiki assistant for one user.\n"
        "Operate as a tool-using agent. When an action is needed, prefer tool calls over unsupported free-form claims.\n"
        "Available responsibilities:\n"
        "- Capture: save new notes, links, and uploads into the personal wiki.\n"
        "- Browse: explain what is in the knowledge base, list topics, and open the most relevant topic.\n"
        "- Read: read or summarize an already selected item from the current working set or focus item.\n"
        "- Clarify: if the user intent is ambiguous, ask a focused clarification question through a tool.\n"
        "Rules:\n"
        "- If the user is providing new content, call save_text, save_link, or save_file.\n"
        "- If the user sends a long new passage and it is unclear whether they want it saved or summarized first, call clarify_capture_intent.\n"
        "- If the user asks what exists in the knowledge base, call overview_knowledge_base or list_topics.\n"
        "- If the user wants previously archived material, call open_topic first.\n"
        "- If the user is referring to an item already present in the working set or focus item, call read_item or summarize_item.\n"
        "- Use clarify_reference only when the intended working-set item is still ambiguous after reading the current context.\n"
        "- After tool results arrive, answer concisely and stay grounded in the tool payload.\n"
        "- Never invent saved content, topic names, item ids, or file locations.\n"
    )


def _build_state_block(context: dict[str, Any], pending_payload: dict[str, Any] | None) -> str:
    state = {
        "focus_item_id": context.get("focus_item_id"),
        "focus_item_title": context.get("focus_item_title"),
        "focus_item_summary": context.get("focus_item_summary"),
        "last_action": context.get("last_action"),
        "working_set": context.get("working_set", [])[:5],
        "pending_clarification": pending_payload or {},
    }
    return "Conversation state:\n" + json.dumps(state, ensure_ascii=False, indent=2)


def build_tool_loop_messages(
    *,
    session_id: str,
    user_text: str,
    context: dict[str, Any],
    pending_payload: dict[str, Any] | None,
    history: list[Message],
    tool_messages: list[Message],
) -> list[Message]:
    messages: list[Message] = [
        Message.system(
            session_id=session_id,
            content=_build_tool_loop_system_prompt() + "\n" + _build_state_block(context, pending_payload),
        )
    ]
    messages.extend(history)
    messages.append(Message.user(session_id=session_id, content=user_text))
    messages.extend(tool_messages)
    return messages


def format_tool_result_payload(
    *,
    tool_name: str,
    action: str,
    item_id: str | None,
    needs_clarification: bool,
    reply: str,
    context: dict[str, Any] | None,
) -> str:
    payload = {
        "tool_name": tool_name,
        "action": action,
        "item_id": item_id,
        "needs_clarification": needs_clarification,
        "reply": reply,
        "context": {
            "focus_item_id": (context or {}).get("focus_item_id"),
            "last_action": (context or {}).get("last_action"),
            "working_set": ((context or {}).get("working_set") or [])[:5],
        },
    }
    return json.dumps(payload, ensure_ascii=False)
