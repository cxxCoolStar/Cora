from __future__ import annotations

import json

from core.clawbot.intent_llm import LLMIntentClassifier
from core.llm.base import ModelClient
from core.schemas.message import Message
from core.schemas.model import ModelResponse
from core.schemas.tool import ToolSpec


class _CaptureModelClient(ModelClient):
    def __init__(self) -> None:
        self.calls: list[list[Message]] = []

    def generate(self, *, messages: list[Message], tools: list[ToolSpec]) -> ModelResponse:
        self.calls.append(list(messages))
        return ModelResponse(
            assistant_text=json.dumps(
                {
                    "intent": "retrieve",
                    "confidence": "medium",
                    "reason": "The user wants to access existing material.",
                    "should_clarify": False,
                    "clarification_question": None,
                }
            )
        )


def test_intent_classifier_uses_general_assistant_framing() -> None:
    model = _CaptureModelClient()
    classifier = LLMIntentClassifier(model)

    result = classifier.classify_with_context(
        text="Send back the export from earlier.",
        context={"last_action": "retrieve"},
    )

    assert result is not None
    assert result.intent == "retrieve"
    system_prompt = model.calls[0][0].content
    assert "general-purpose assistant" in system_prompt
    assert "personal archive assistant" not in system_prompt
