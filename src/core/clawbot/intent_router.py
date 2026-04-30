from __future__ import annotations

from dataclasses import dataclass

from core.clawbot.intent_llm import LLMIntentClassifier


@dataclass(slots=True)
class IntentDecision:
    intent: str
    confidence: str
    reason: str
    needs_clarification: bool = False
    source: str = "rule"


class IntentRouter:
    GREETINGS = {
        "hi",
        "hello",
        "hey",
        "你好",
        "您好",
        "在吗",
        "嗨",
        "哈喽",
    }
    FALLBACK_CAPTURE_HINTS = ("请保存", "帮我保存", "先保存", "存一下", "帮我记", "收录")
    FALLBACK_RETRIEVE_HINTS = (
        "找一下",
        "找出来",
        "找回",
        "查一下",
        "查找",
        "查询",
        "告诉我",
        "我之前保存的",
        "之前保存的",
        "知识库中有什么",
        "知识库里有什么",
        "有哪些主题",
        "topic列表",
        "主题列表",
    )
    FALLBACK_ORGANIZE_HINTS = ("总结", "整理", "分类", "提炼", "归纳")
    FOLLOW_UP_REFERENCE_HINTS = ("这里面", "上面那个", "上面的", "刚才那个", "第二个", "第一个", "第三个", "全文", "原文", "展开", "详细", "写了什么", "讲了什么")

    def __init__(self, llm_classifier: LLMIntentClassifier | None = None) -> None:
        self.llm_classifier = llm_classifier

    def decide(self, *, text: str | None, has_upload: bool, context: dict | None = None) -> IntentDecision:
        content = (text or "").strip()
        lowered = content.lower()
        context = context or {}

        if has_upload:
            return IntentDecision(intent="capture", confidence="high", reason="File upload detected.", source="rule")

        if self._is_greeting(content, lowered):
            return IntentDecision(intent="chat", confidence="high", reason="Greeting detected.", source="rule")

        if self._is_url_only(content):
            return IntentDecision(intent="capture", confidence="high", reason="Standalone URL detected.", source="rule")

        if self._looks_like_contextual_followup(content=content, context=context):
            return IntentDecision(
                intent="organize",
                confidence="high",
                reason="Detected a follow-up reference to the current conversation working set.",
                source="rule",
            )

        llm_decision = self._llm_decide(content, context=context)
        if llm_decision is not None:
            return llm_decision

        if any(hint in content for hint in self.FALLBACK_CAPTURE_HINTS):
            return IntentDecision(
                intent="capture",
                confidence="medium",
                reason="Fallback capture phrase detected while LLM is unavailable.",
                source="fallback",
            )

        if any(hint in content for hint in self.FALLBACK_RETRIEVE_HINTS):
            return IntentDecision(
                intent="retrieve",
                confidence="medium",
                reason="Fallback retrieval phrase detected while LLM is unavailable.",
                source="fallback",
            )

        if any(hint in content for hint in self.FALLBACK_ORGANIZE_HINTS):
            return IntentDecision(
                intent="organize",
                confidence="medium",
                reason="Fallback organization phrase detected while LLM is unavailable.",
                source="fallback",
            )

        if self._looks_like_long_material(content):
            return IntentDecision(
                intent="clarify",
                confidence="low",
                reason="LLM unavailable or inconclusive for long text.",
                needs_clarification=True,
                source="fallback",
            )

        return IntentDecision(intent="chat", confidence="medium", reason="LLM unavailable; default conversational fallback.", source="fallback")

    @staticmethod
    def _is_url_only(content: str) -> bool:
        return content.startswith(("http://", "https://")) and " " not in content and "\n" not in content

    def _is_greeting(self, content: str, lowered: str) -> bool:
        return content in self.GREETINGS or lowered in self.GREETINGS

    @staticmethod
    def _looks_like_long_material(content: str) -> bool:
        if len(content) >= 120:
            return True
        if "\n" in content and len(content) >= 40:
            return True
        return False

    def interpret_clarification_reply(self, text: str) -> str | None:
        content = text.strip().lower()
        if any(token in content for token in ("保存", "存", "save", "收录", "记住")):
            return "capture"
        if any(token in content for token in ("总结", "整理", "summary", "summarize")):
            return "organize"
        if any(token in content for token in ("不用", "取消", "算了", "no")):
            return "cancel"
        return None

    def _looks_like_contextual_followup(self, *, content: str, context: dict) -> bool:
        if not content:
            return False
        if not (context.get("focus_item_id") or context.get("working_set")):
            return False
        return any(token in content for token in self.FOLLOW_UP_REFERENCE_HINTS)

    def _llm_decide(self, content: str, *, context: dict | None) -> IntentDecision | None:
        if self.llm_classifier is None or not content:
            return None
        classify_with_context = getattr(self.llm_classifier, "classify_with_context", None)
        if callable(classify_with_context):
            result = classify_with_context(text=content, context=context)
        else:
            result = self.llm_classifier.classify(text=content)
        if result is None:
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
