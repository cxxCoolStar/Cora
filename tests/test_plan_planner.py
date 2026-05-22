from __future__ import annotations

import json

from core.agent.harness import new_run_input
from core.agent.plan_planner import (
    PLANNER_AGENT_ROLE,
    build_planner_user_text,
    finalize_planner_run,
    resolve_planner_validation_options,
)
from core.agent.loop import LoopResult
from core.schemas.harness import HarnessRunInput, HarnessTraceEventType, RunBudget
from unittest.mock import MagicMock
from core.tools import ToolManager


def _registered_tools() -> frozenset[str]:
    manager = ToolManager()
    return frozenset(spec.name for spec in manager.get_registered_specs(["file", "web", "skills", "session", "automation"]))


def _run_input(*, user_text: str) -> HarnessRunInput:
    return new_run_input(
        session_id="session-plan-test",
        source_message_id="msg-1",
        user_text=user_text,
        raw_text=user_text,
        upload=None,
        context_snapshot=MagicMock(),
        budget=RunBudget(policy_profile="planner_readonly", max_steps=4),
        metadata={"agent_role": PLANNER_AGENT_ROLE},
        agent_role=PLANNER_AGENT_ROLE,
    )


def test_build_planner_user_text_includes_session_and_json_shape() -> None:
    text = build_planner_user_text(user_text="/plan List files", session_id="session-abc")
    assert "session-abc" in text
    assert "[Planner mode]" in text
    assert '"tasks"' in text
    assert "List files" in text


def test_finalize_planner_run_accepts_valid_json() -> None:
    plan = {
        "plan_id": "plan-unit-1",
        "session_id": "session-plan-test",
        "goal": "List tasks",
        "tasks": [
            {
                "task_id": "task-1",
                "title": "List",
                "tool_names": ["scheduled_tasks"],
                "instruction": "List scheduled tasks.",
            }
        ],
    }
    loop_result = LoopResult(
        final_response=json.dumps(plan),
        assistant_response=json.dumps(plan),
        exit_reason="assistant_text",
        status="completed",
        disposition="respond",
        tool_trace=[],
        trace=[],
        artifacts=[],
        steps=1,
        runtime=MagicMock(),
    )
    events: list[HarnessTraceEventType] = []

    def emit(event_type, **kwargs):
        events.append(event_type)

    result = finalize_planner_run(
        emit=emit,
        run_input=_run_input(user_text="List tasks"),
        loop_result=loop_result,
        validation_options=resolve_planner_validation_options(registered_tool_names=_registered_tools()),
    )
    assert result.validation is not None
    assert result.validation.valid is True
    assert result.loop_result.exit_reason == "plan_validated"
    assert HarnessTraceEventType.PLAN_VALIDATION_COMPLETED in events


def test_finalize_planner_run_rejects_unknown_tool() -> None:
    plan = {
        "plan_id": "plan-unit-bad",
        "session_id": "session-plan-test",
        "goal": "Broken",
        "tasks": [
            {
                "task_id": "task-1",
                "title": "Bad",
                "tool_names": ["totally_unknown_tool_xyz"],
                "instruction": "Do bad things.",
            }
        ],
    }
    loop_result = LoopResult(
        final_response=json.dumps(plan),
        assistant_response=json.dumps(plan),
        exit_reason="assistant_text",
        status="completed",
        disposition="respond",
        tool_trace=[],
        trace=[],
        artifacts=[],
        steps=1,
        runtime=MagicMock(),
    )
    events: list[HarnessTraceEventType] = []

    def emit(event_type, **kwargs):
        events.append(event_type)

    result = finalize_planner_run(
        emit=emit,
        run_input=_run_input(user_text="Broken plan"),
        loop_result=loop_result,
        validation_options=resolve_planner_validation_options(registered_tool_names=_registered_tools()),
    )
    assert result.validation is not None
    assert result.validation.valid is False
    assert result.loop_result.exit_reason == "plan_validation_failed"
    assert result.loop_result.disposition == "clarify"
    assert HarnessTraceEventType.PLAN_VALIDATION_FAILED in events


def test_loop_result_to_turn_result_prefers_plan_summary_over_tool_trace() -> None:
    from core.agent.loop import LoopResult
    from core.agent.turn_runner import AgentTurnRunner

    policy = MagicMock()
    policy.normalize_disposition.side_effect = lambda *, disposition: disposition
    policy_resolver = MagicMock()
    policy_resolver.for_runtime.return_value = policy
    runner = AgentTurnRunner(
        orchestrator=MagicMock(),
        loop=MagicMock(),
        runtime_manager=MagicMock(),
        skill_loader=MagicMock(),
        history_loader=lambda **kwargs: [],
        delivery_available=lambda: False,
        media_kind_resolver=lambda **kwargs: None,
        tool_specs_resolver=lambda **kwargs: [],
        execution_policy_resolver=policy_resolver,
    )
    loop_result = LoopResult(
        final_response="Plan `plan-1` validated with 1 task(s).",
        assistant_response="search hit",
        exit_reason="plan_validated",
        status="completed",
        disposition="respond",
        tool_trace=[MagicMock()],
        trace=[],
        artifacts=[],
        steps=2,
        runtime=MagicMock(),
    )
    turn_result = runner.loop_result_to_turn_result(loop_result)
    assert "Plan `plan-1` validated" in turn_result.reply
    assert "search hit" not in turn_result.reply


def test_validation_options_include_registered_tools() -> None:
    options = resolve_planner_validation_options(registered_tool_names=_registered_tools())
    assert "scheduled_tasks" in options.registered_tool_names
    assert "write_file" in options.registered_tool_names
