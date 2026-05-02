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
        "- Read: read or summarize an already selected item from the current working set or recent saved/opened items.\n"
        "- Deliver: send a previously saved file back to the user when the channel supports file delivery.\n"
        "- Clarify: if the user intent is ambiguous, ask a focused clarification question through a tool.\n"
        "Rules:\n"
        "- If the user is providing new content:\n"
        "  - For plain text or a standalone link, call save_content.\n"
        "  - For a file upload, call save_file (only when an actual file is attached).\n"
        "- If the user sends a long new passage and it is unclear whether they want it saved or summarized first, call clarify_capture_intent.\n"
        "- If the user asks what exists in the knowledge base, call overview_knowledge_base or list_topics.\n"
        "- If the user wants previously archived material, call open_topic first.\n"
        "- If the user is referring to an item already present in the working set or recent saved/opened items, call read_item or summarize_item.\n"
        "- If the user asks you to send, forward, deliver, or let them receive a previously saved file, call send_file_to_user instead of only summarizing it.\n"
        "- Use clarify_reference only when the intended working-set item is still ambiguous after reading the current context.\n"
        "- After tool results arrive, answer concisely and stay grounded in the tool payload.\n"
        "- Never invent saved content, topic names, item ids, or file locations.\n"
    )


def _build_state_block(context: dict[str, Any], pending_payload: dict[str, Any] | None) -> str:
    state = {
        "primary_focus": context.get("primary_focus"),
        "last_action": context.get("last_action"),
        "working_set": context.get("working_set", [])[:5],
        "recent_items": context.get("recent_items", [])[:5],
        "recent_events": context.get("recent_events", [])[:5],
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
            "primary_focus": (context or {}).get("primary_focus"),
            "last_action": (context or {}).get("last_action"),
            "working_set": ((context or {}).get("working_set") or [])[:5],
            "recent_items": ((context or {}).get("recent_items") or [])[:5],
            "recent_events": ((context or {}).get("recent_events") or [])[:5],
        },
    }
    return json.dumps(payload, ensure_ascii=False)
