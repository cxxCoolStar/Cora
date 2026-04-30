from __future__ import annotations

from dataclasses import dataclass
import logging

from core.clawbot.intent_llm import LLMIntentClassifier

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class IntentDecision:
    intent: str
    confidence: str
    reason: str
    needs_clarification: bool = False
    source: str = "llm"


class IntentRouter:
    def __init__(self, llm_classifier: LLMIntentClassifier | None = None) -> None:
        self.llm_classifier = llm_classifier

    def decide(self, *, text: str | None, has_upload: bool, context: dict | None = None) -> IntentDecision:
        content = (text or "").strip()
        context = context or {}
        llm_text = content or ("[file upload]" if has_upload else "")
        logger.info(
            "intent router decide_start has_upload=%s text=%s context=%s",
            has_upload,
            llm_text[:160],
            {
                "focus_item_id": context.get("focus_item_id"),
                "focus_item_title": context.get("focus_item_title"),
                "last_action": context.get("last_action"),
                "working_set_size": len(context.get("working_set") or []),
            },
        )
        if self.llm_classifier is None:
            raise RuntimeError("LLMIntentClassifier is required; heuristic intent routing has been removed.")
        llm_decision = self._llm_decide(llm_text, context={**context, "has_upload": has_upload})
        if llm_decision is None:
            raise RuntimeError("LLM intent classification returned no usable result.")
        logger.info(
            "intent router decide_done intent=%s confidence=%s source=%s clarify=%s reason=%s",
            llm_decision.intent,
            llm_decision.confidence,
            llm_decision.source,
            llm_decision.needs_clarification,
            llm_decision.reason,
        )
        return llm_decision

    def interpret_clarification_reply(self, text: str) -> str | None:
        content = text.strip().lower()
        if any(token in content for token in ("保存", "存", "save", "收录", "记住")):
            return "capture"
        if any(token in content for token in ("总结", "整理", "summary", "summarize")):
            return "organize"
        if any(token in content for token in ("不用", "取消", "算了", "no")):
            return "cancel"
        return None

    def _llm_decide(self, content: str, *, context: dict | None) -> IntentDecision | None:
        if not content:
            return None
        classify_with_context = getattr(self.llm_classifier, "classify_with_context", None)
        if callable(classify_with_context):
            result = classify_with_context(text=content, context=context)
        else:
            result = self.llm_classifier.classify(text=content)
        if result is None:
            logger.warning("intent router llm_result_empty content=%s", content[:160])
            return None
        if result.should_clarify:
            return IntentDecision(
                intent="clarify",
                confidence=result.confidence,
                reason=result.reason,
                needs_clarification=True,
                source="llm",
            )
        return IntentDecision(
            intent=result.intent,
            confidence=result.confidence,
            reason=result.reason,
            needs_clarification=False,
            source="llm",
        )
