from __future__ import annotations

from dataclasses import dataclass

from core.clawbot.intent_llm import LLMIntentClassifier


@dataclass(slots=True)
class IntentDecision:
    intent: str
    confidence: str
    reason: str
    needs_clarification: bool = False


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
    SAVE_HINTS = ("保存", "存一下", "记一下", "记住", "收录", "帮我存", "帮我记")
    RETRIEVE_HINTS = ("找", "之前", "发过", "保存过", "哪个", "那条", "那个")
    ORGANIZE_HINTS = ("总结", "整理", "分类", "提炼", "归纳")

    def __init__(self, llm_classifier: LLMIntentClassifier | None = None) -> None:
        self.llm_classifier = llm_classifier

    def decide(self, *, text: str | None, has_upload: bool) -> IntentDecision:
        content = (text or "").strip()
        lowered = content.lower()

        if has_upload:
            return IntentDecision(intent="capture", confidence="high", reason="File upload detected.")

        if self._is_greeting(content, lowered):
            return IntentDecision(intent="chat", confidence="high", reason="Greeting detected.")

        if self._is_url_only(content):
            return IntentDecision(intent="capture", confidence="high", reason="Standalone URL detected.")

        if any(hint in content for hint in self.RETRIEVE_HINTS):
            return IntentDecision(intent="retrieve", confidence="medium", reason="Retrieval phrase detected.")

        if any(hint in content for hint in self.ORGANIZE_HINTS):
            return IntentDecision(intent="organize", confidence="medium", reason="Organization phrase detected.")

        if any(hint in content for hint in self.SAVE_HINTS):
            return IntentDecision(intent="capture", confidence="medium", reason="Explicit save phrase detected.")

        if self._looks_like_long_material(content):
            llm_decision = self._llm_decide(content)
            if llm_decision is not None:
                return llm_decision
            return IntentDecision(
                intent="clarify",
                confidence="low",
                reason="Long text without explicit action.",
                needs_clarification=True,
            )

        llm_decision = self._llm_decide(content)
        if llm_decision is not None:
            return llm_decision

        return IntentDecision(intent="chat", confidence="medium", reason="Default conversational text.")

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

    def _llm_decide(self, content: str) -> IntentDecision | None:
        if self.llm_classifier is None or not content:
            return None
        result = self.llm_classifier.classify(text=content)
        if result is None:
            return None
        if result.should_clarify:
            return IntentDecision(
                intent="clarify",
                confidence=result.confidence,
                reason=result.reason,
                needs_clarification=True,
            )
        return IntentDecision(
            intent=result.intent,
            confidence=result.confidence,
            reason=result.reason,
            needs_clarification=False,
        )
