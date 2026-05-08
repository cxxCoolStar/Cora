from __future__ import annotations

import json
from dataclasses import dataclass
import logging

from core.llm.base import ModelClient
from core.schemas.message import Message

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class LLMIntentResult:
    intent: str
    confidence: str
    reason: str
    should_clarify: bool
    clarification_question: str | None = None


class LLMIntentClassifier:
    """Uses the configured chat model to classify ambiguous user turns."""

    def __init__(self, model_client: ModelClient) -> None:
        self.model_client = model_client

    def classify(self, *, text: str) -> LLMIntentResult | None:
        return self.classify_with_context(text=text, context=None)

    def classify_with_context(self, *, text: str, context: dict | None) -> LLMIntentResult | None:
        prompt = (
            "You are classifying the intent of a message sent to a personal archive assistant.\n"
            "Choose exactly one intent from: capture, retrieve, organize, chat.\n"
            "You must use the full conversation state, not just surface keywords.\n"
            "If the user is referring to a current working set item or focus item, classify based on the action they want next rather than treating it as new content.\n"
            "For example, '第一个给我看全文/这里面写了什么/展开讲讲' should usually be retrieve because the user wants to open or read an existing archived item.\n"
            "Requests to summarize or reorganize an already selected item should be organize.\n"
            "If the message is ambiguous and should be clarified before action, set should_clarify to true.\n"
            "Examples:\n"
            "- A user asking to find something they saved before -> retrieve\n"
            "- A user sending content they want stored -> capture\n"
            "- A user asking for summary or organization -> organize\n"
            "- A greeting or casual conversation -> chat\n"
            "- If conversation context includes an active result and the user asks to read/open/see it, classify as retrieve\n"
            "Respond with strict JSON using keys: intent, confidence, reason, should_clarify, clarification_question.\n"
            "confidence must be one of: high, medium, low.\n"
            "clarification_question should be a short Chinese question when should_clarify is true, otherwise null."
        )
        context_block = ""
        if context:
            serialized_context = {
                "has_upload": context.get("has_upload"),
                "last_action": context.get("last_action"),
                "recent_events": [
                    {
                        "event_type": snapshot.get("event_type"),
                        "raw_text": snapshot.get("raw_text"),
                        "original_file_name": snapshot.get("original_file_name"),
                    }
                    for snapshot in (context.get("recent_events") or [])[:5]
                    if isinstance(snapshot, dict)
                ],
            }
            if any(serialized_context.values()):
                context_block = (
                    "\n\nConversation context (JSON):\n"
                    f"{json.dumps(serialized_context, ensure_ascii=False)}\n"
                )
        logger.info("intent llm classify_start text=%s context=%s", text[:160], (context_block or "(empty)")[:1000])
        response = self.model_client.generate(
            messages=[
                Message.system(session_id="intent-router", content=prompt + context_block),
                Message.user(session_id="intent-router", content=text),
            ],
            tools=[],
        )
        content = (response.assistant_text or "").strip()
        logger.info("intent llm classify_raw_output text=%s output=%s", text[:160], content[:1000])
        if not content:
            return None
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            fenced = content.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            try:
                payload = json.loads(fenced)
            except json.JSONDecodeError:
                logger.warning("intent llm classify_non_json text=%s output=%s", text[:160], content[:1000])
                return None
        intent = str(payload.get("intent") or "").strip()
        confidence = str(payload.get("confidence") or "").strip()
        reason = str(payload.get("reason") or "").strip()
        if intent not in {"capture", "retrieve", "organize", "chat"}:
            logger.warning("intent llm classify_invalid_intent text=%s payload=%s", text[:160], payload)
            return None
        if confidence not in {"high", "medium", "low"}:
            confidence = "low"
        should_clarify = bool(payload.get("should_clarify"))
        clarification_question = payload.get("clarification_question")
        if clarification_question is not None:
            clarification_question = str(clarification_question).strip() or None
        result = LLMIntentResult(
            intent=intent,
            confidence=confidence,
            reason=reason or "LLM classified this intent.",
            should_clarify=should_clarify,
            clarification_question=clarification_question,
        )
        logger.info(
            "intent llm classify_done text=%s intent=%s confidence=%s clarify=%s reason=%s",
            text[:160],
            result.intent,
            result.confidence,
            result.should_clarify,
            result.reason,
        )
        return result
