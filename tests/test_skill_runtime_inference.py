from __future__ import annotations

from io import BytesIO

from fastapi import UploadFile

from core.clawbot.planner import ToolPlan
from core.clawbot.tools import RuntimeToolExecutor
from core.tools.registry import ToolInvocation


def _invocation(*, text: str | None = None, upload: UploadFile | None = None) -> ToolInvocation:
    return ToolInvocation(
        session_id="session-skill-runtime",
        source_message_id="message-skill-runtime",
        plan=ToolPlan(
            tool="skill_run",
            arguments={},
            reason="test",
            source="test",
        ),
        text=text,
        upload=upload,
        context={},
    )


def test_infer_skill_intent_uses_runtime_metadata_for_generic_delivery() -> None:
    intent = RuntimeToolExecutor._infer_skill_intent(
        object(),
        skill_name="research-core",
        input_payload={"query": "Send back the latest export"},
        invocation=_invocation(text="Send back the latest export"),
        runtime_metadata={
            "required_input_fields": ["intent"],
            "intent_phrases": {
                "deliver": ["send back"],
                "search": ["find", "search"],
            },
        },
    )

    assert intent == "deliver"


def test_infer_skill_intent_uses_save_when_skill_supports_save_and_upload_present() -> None:
    upload = UploadFile(filename="notes.txt", file=BytesIO(b"hello"))

    intent = RuntimeToolExecutor._infer_skill_intent(
        object(),
        skill_name="workspace-core",
        input_payload={},
        invocation=_invocation(upload=upload),
        runtime_metadata={
            "required_input_fields": ["intent"],
            "intent_phrases": {
                "save": ["save"],
            },
        },
    )

    assert intent == "save"


def test_infer_skill_intent_does_not_assume_search_without_search_capability() -> None:
    intent = RuntimeToolExecutor._infer_skill_intent(
        object(),
        skill_name="workspace-core",
        input_payload={"query": "Find the latest export"},
        invocation=_invocation(text="Find the latest export"),
        runtime_metadata={
            "required_input_fields": ["intent"],
            "intent_phrases": {
                "deliver": ["send back"],
            },
        },
    )

    assert intent is None
