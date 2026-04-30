from __future__ import annotations

import json
from dataclasses import dataclass

from core.llm.base import ModelClient
from core.schemas.message import Message


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
            "The user may ask follow-up questions about the last retrieved item; this should be organize (explain/summarize the retrieved content), not capture.\n"
            "If the message is ambiguous and should be clarified before action, set should_clarify to true.\n"
            "Examples:\n"
            "- A user asking to find something they saved before -> retrieve\n"
            "- A user sending content they want stored -> capture\n"
            "- A user asking for summary or organization -> organize\n"
            "- A greeting or casual conversation -> chat\n"
            "- If conversation context includes a last_retrieved_item and the user asks '这里面写了什么/展开讲讲/详细一点', classify as organize\n"
            "Respond with strict JSON using keys: intent, confidence, reason, should_clarify, clarification_question.\n"
            "confidence must be one of: high, medium, low.\n"
            "clarification_question should be a short Chinese question when should_clarify is true, otherwise null."
        )
        context_block = ""
        if context:
            last_title = str(context.get("last_retrieved_item_title") or "").strip()
            last_summary = str(context.get("last_retrieved_item_summary") or "").strip()
            if last_title or last_summary:
                context_block = (
                    "\n\nConversation context (may be empty):\n"
                    f"- last_retrieved_item_title: {last_title or '(none)'}\n"
                    f"- last_retrieved_item_summary: {last_summary or '(none)'}\n"
                )
        response = self.model_client.generate(
            messages=[
                Message.system(session_id="intent-router", content=prompt + context_block),
                Message.user(session_id="intent-router", content=text),
            ],
            tools=[],
        )
        content = (response.assistant_text or "").strip()
        if not content:
            return None
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            fenced = content.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            try:
                payload = json.loads(fenced)
            except json.JSONDecodeError:
                return None
        intent = str(payload.get("intent") or "").strip()
        confidence = str(payload.get("confidence") or "").strip()
        reason = str(payload.get("reason") or "").strip()
        if intent not in {"capture", "retrieve", "organize", "chat"}:
            return None
        if confidence not in {"high", "medium", "low"}:
            confidence = "low"
        should_clarify = bool(payload.get("should_clarify"))
        clarification_question = payload.get("clarification_question")
        if clarification_question is not None:
            clarification_question = str(clarification_question).strip() or None
        return LLMIntentResult(
            intent=intent,
            confidence=confidence,
            reason=reason or "LLM classified this intent.",
            should_clarify=should_clarify,
            clarification_question=clarification_question,
        )
