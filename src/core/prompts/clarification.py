from __future__ import annotations

import json
from typing import Any

from core.schemas.message import Message


def build_capture_clarification_router_messages(*, text: str) -> list[Message]:
    prompt = (
        "You are interpreting a user's reply to a clarification question.\n"
        "Choose exactly one action from: capture, organize, cancel, unresolved.\n"
        "Respond with strict JSON using keys: action, reason.\n"
        "- capture: the user wants the earlier content saved.\n"
        "- organize: the user wants the earlier content summarized or organized first.\n"
        "- cancel: the user wants to stop.\n"
        "- unresolved: the reply is still ambiguous.\n"
    )
    return [
        Message.system(session_id="clarification-router", content=prompt),
        Message.user(session_id="clarification-router", content=text),
    ]


def build_reference_resolution_messages(*, text: str, working_set: list[dict[str, Any]]) -> list[Message]:
    prompt = (
        "You are resolving a user's reply to a reference clarification.\n"
        "Choose exactly one action from: select, unresolved.\n"
        "If you can identify the intended item, return action=select and the rank of that item.\n"
        "Use ordinal cues, title fragments, and the current working set.\n"
        "Respond with strict JSON using keys: action, rank, reason.\n"
    )
    payload = {
        "user_reply": text,
        "working_set": [
            {
                "rank": snapshot.get("rank"),
                "title": snapshot.get("title"),
                "summary": snapshot.get("summary"),
            }
            for snapshot in working_set[:5]
            if isinstance(snapshot, dict)
        ],
    }
    return [
        Message.system(session_id="reference-router", content=prompt),
        Message.user(session_id="reference-router", content=json.dumps(payload, ensure_ascii=False)),
    ]
