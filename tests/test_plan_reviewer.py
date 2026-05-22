from __future__ import annotations

from core.agent.plan_reviewer import (
    build_reviewer_user_text,
    parse_review_verdict_from_text,
    resolve_plan_review_mode,
    should_review_task,
    verdict_from_turn_reply,
)
from core.schemas.plan import TaskSpec, plan_spec_from_dict


def _write_task(*, requires_review: bool = False) -> TaskSpec:
    return TaskSpec(
        task_id="task-write",
        title="Write",
        tool_names=["write_file"],
        instruction="Write marker.",
        requires_review=requires_review,
    )


def test_should_review_high_risk_tool_in_default_mode() -> None:
    assert should_review_task(task=_write_task(), review_mode="high_risk_only") is True
    assert (
        should_review_task(
            task=TaskSpec(
                task_id="t1",
                title="Search",
                tool_names=["search_files"],
                instruction="search",
            ),
            review_mode="high_risk_only",
        )
        is False
    )


def test_should_review_respects_off_mode() -> None:
    assert should_review_task(task=_write_task(), review_mode="off") is False


def test_should_review_requires_review_flag() -> None:
    task = TaskSpec(
        task_id="t-search",
        title="Search",
        tool_names=["search_files"],
        instruction="search",
        requires_review=True,
    )
    assert should_review_task(task=task, review_mode="off") is True


def test_parse_review_verdict_and_turn_reply() -> None:
    raw = '{"verdict": "abort", "reason": "unsafe output", "confidence": "high"}'
    verdict = parse_review_verdict_from_text(raw)
    assert verdict.verdict == "abort"
    assert verdict.reason == "unsafe output"
    from core.agent.plan_reviewer import _reviewer_reply

    parsed = verdict_from_turn_reply(_reviewer_reply(verdict))
    assert parsed is not None
    assert parsed.verdict == "abort"


def test_build_reviewer_user_text_includes_worker_summary() -> None:
    plan = plan_spec_from_dict(
        {
            "plan_id": "plan-1",
            "session_id": "session-1",
            "goal": "Write marker",
            "tasks": [_write_task().to_dict()],
        }
    )
    text = build_reviewer_user_text(
        plan=plan,
        task=plan.tasks[0],
        worker_summary="Wrote src/marker.txt",
        worker_run_id="run-worker-1",
    )
    assert "[Reviewer mode]" in text
    assert "Wrote src/marker.txt" in text
    assert "run-worker-1" in text


def test_resolve_plan_review_mode_normalizes_aliases() -> None:
    assert resolve_plan_review_mode(configured="off") == "off"
    assert resolve_plan_review_mode(configured="always") == "always"
