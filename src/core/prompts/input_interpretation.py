from __future__ import annotations

import json
from typing import Any

from core.schemas.message import Message


def build_input_interpretation_messages(
    *,
    text: str | None,
    has_upload: bool,
    upload_filename: str | None,
    media_kind: str | None,
    context: dict[str, Any],
) -> list[Message]:
    prompt = (
        "You are deciding how Cora should handle the user's latest inbound content before any tool runs.\n"
        "Your job is only to decide whether Cora already has enough information to act, or whether it should ask one concise clarification question first.\n"
        "Be conservative about asking follow-up questions: ask only when proceeding now would likely mis-handle the user's intent.\n"
        "Respond with strict JSON using keys: needs_clarification, clarification_question, intent, content_role, reason.\n"
        "Valid intent values: save, update_existing, retrieve, send, summarize, extract, chat, unclear.\n"
        "Valid content_role values: new_material, existing_item_reference, existing_item_annotation, instruction_only, unknown.\n"
        "Rules:\n"
        "- If the user message is a clear instruction or request, do not ask a clarification question.\n"
        "- If the user sends a long standalone text passage and it is unclear whether they want it saved or acted on, ask whether Cora should save it or handle it as an instruction.\n"
        "- If the user sends a file or image without enough surrounding explanation, ask what they want Cora to do with it.\n"
        "- If the user sends a file or image together with a clear description like '这是我的简历' or '这是我女朋友', that is enough to save it without clarification.\n"
        "- If the user is obviously referring to a current focus item or working-set item, prefer update_existing or send instead of save.\n"
        "- The clarification question must be the smallest useful next question and should not mention internal tool names.\n"
    )
    payload = {
        "text": text or "",
        "has_upload": has_upload,
        "upload_filename": upload_filename or "",
        "media_kind": media_kind or "none",
        "focus_item_id": context.get("focus_item_id"),
        "focus_item_title": context.get("focus_item_title"),
        "last_action": context.get("last_action"),
        "working_set": [
            {
                "rank": snapshot.get("rank"),
                "title": snapshot.get("title"),
                "summary": snapshot.get("summary"),
            }
            for snapshot in (context.get("working_set") or [])[:3]
            if isinstance(snapshot, dict)
        ],
    }
    return [
        Message.system(session_id="input-interpreter", content=prompt),
        Message.user(session_id="input-interpreter", content=json.dumps(payload, ensure_ascii=False)),
    ]


def build_input_followup_router_messages(*, text: str, pending_payload: dict[str, Any]) -> list[Message]:
    prompt = (
        "You are interpreting a user's reply to Cora's clarification about newly received content.\n"
        "Respond with strict JSON using keys: action, note, reason.\n"
        "Valid action values: capture, cancel, unresolved.\n"
        "- capture: the user wants Cora to keep/process the pending content, even if the reply is descriptive rather than an explicit 'save'.\n"
        "- cancel: the user does not want to continue with the pending content.\n"
        "- unresolved: the reply is still too ambiguous.\n"
        "- If the reply describes what the pending file/image/text is, keep that description in note.\n"
        "- If the reply only says to save it, note may be empty.\n"
    )
    payload = {
        "user_reply": text,
        "pending_type": pending_payload.get("pending_input_type"),
        "media_kind": pending_payload.get("media_kind"),
        "original_text": pending_payload.get("original_text", ""),
        "upload_filename": pending_payload.get("upload_filename", ""),
        "clarification_question": pending_payload.get("clarification_question", ""),
    }
    return [
        Message.system(session_id="input-followup-router", content=prompt),
        Message.user(session_id="input-followup-router", content=json.dumps(payload, ensure_ascii=False)),
    ]
