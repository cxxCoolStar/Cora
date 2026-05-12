from __future__ import annotations

from core.agent.skill_protocol import SkillExecutionResult, UNSET


def test_skill_execution_result_parses_new_effects_and_state_delta() -> None:
    payload = {
        "message": "done",
        "action": "retrieve",
        "effects": [
            {"kind": "ingest_text", "payload": {"text": "hello"}},
            {"kind": "deliver_file", "payload": {"file_path": "C:/tmp/resume.pdf", "title": "resume.pdf"}},
        ],
        "artifacts": [{"kind": "item", "ref": "item-1", "payload": {"title": "resume.pdf"}}],
        "state_delta": {
            "last_action": "capture",
            "skill_state": {"archive-core": {"last_query": "resume"}},
        },
        "pending_state_delta": {"status": "resolved"},
    }

    result = SkillExecutionResult.from_payload(payload)

    assert [effect.kind for effect in result.effects] == ["ingest_text", "deliver_file"]
    assert result.state_delta.last_action == "capture"
    assert result.state_delta.skill_state["archive-core"]["last_query"] == "resume"
    assert result.pending_state_delta.status == "resolved"


def test_skill_execution_result_parses_pending_state_delta_request() -> None:
    result = SkillExecutionResult.from_payload(
        {
            "message": "clarify",
            "action": "clarify",
            "pending_state_delta": {
                "request": {
                    "kind": "item_selection",
                    "question": "Which one?",
                    "choices": ["first", "second"],
                    "payload": {"type": "item_selection"},
                }
            },
        }
    )

    assert result.pending_state_delta.request is not UNSET
    assert result.pending_state_delta.request is not None
    assert result.pending_state_delta.request.kind == "item_selection"
    assert result.pending_state_delta.request.question == "Which one?"
    assert result.pending_state_delta.request.choices == ["first", "second"]
