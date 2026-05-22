from __future__ import annotations

from core.agent.plan_execute import (
    checkpoint_resume_task_index,
    checkpoint_resume_task_results,
    parse_execute_plan_command,
    should_auto_resume_failed_checkpoint,
)
from core.agent.plan_execution_state import StoredPlanExecution
from core.schemas.plan import PlanSpec, TaskResultSpec, TaskSpec


def _checkpoint(*, task_index: int, results: list[TaskResultSpec], pause_reason: str = "failed") -> StoredPlanExecution:
    plan = PlanSpec(
        plan_id="plan-test",
        session_id="session-test",
        goal="test",
        policy_profile="coding_full",
        tasks=[
            TaskSpec(task_id="task-1", title="One", tool_names=["search_files"], instruction="a"),
            TaskSpec(task_id="task-2", title="Two", tool_names=["search_files"], instruction="b"),
            TaskSpec(task_id="task-3", title="Three", tool_names=["search_files"], instruction="c"),
        ],
    )
    return StoredPlanExecution(
        session_id="session-test",
        plan=plan,
        planner_run_id="run-planner",
        source_message_id="msg-1",
        task_index=task_index,
        task_results=results,
        pause_reason=pause_reason,
        checkpoint_id="ckpt-test",
    )


def test_parse_execute_plan_command_modes() -> None:
    assert parse_execute_plan_command("/execute").mode == "start"
    assert parse_execute_plan_command("/execute resume").mode == "resume"
    assert parse_execute_plan_command("/execute restart").mode == "restart"


def test_should_auto_resume_failed_checkpoint() -> None:
    checkpoint = _checkpoint(
        task_index=1,
        results=[
            TaskResultSpec(task_id="task-1", run_id="r1", status="completed", summary="ok"),
            TaskResultSpec(task_id="task-2", run_id="r2", status="failed", summary="no"),
        ],
    )
    assert should_auto_resume_failed_checkpoint(
        command=parse_execute_plan_command("/execute"),
        checkpoint=checkpoint,
    )
    assert not should_auto_resume_failed_checkpoint(
        command=parse_execute_plan_command("/execute resume"),
        checkpoint=checkpoint,
    )
    checkpoint.pause_reason = "hitl"
    checkpoint.pending_hitl_id = "hitl-1"
    assert not should_auto_resume_failed_checkpoint(
        command=parse_execute_plan_command("/execute"),
        checkpoint=checkpoint,
    )


def test_checkpoint_resume_drops_failed_tail_result() -> None:
    checkpoint = _checkpoint(
        task_index=1,
        results=[
            TaskResultSpec(task_id="task-1", run_id="r1", status="completed", summary="ok"),
            TaskResultSpec(task_id="task-2", run_id="r2", status="failed", summary="no"),
        ],
    )
    assert checkpoint_resume_task_index(checkpoint=checkpoint) == 1
    resumed = checkpoint_resume_task_results(checkpoint=checkpoint)
    assert [item.task_id for item in resumed] == ["task-1"]
    assert resumed[0].status == "completed"
