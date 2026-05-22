from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from core.agent.plan_executor import (
    PlanExecutor,
    build_worker_user_text,
    format_plan_execution_reply,
    worker_run_budget,
)
from core.agent.plan_store import InMemoryPlanStore, StoredValidatedPlan
from core.agent.run_records import AgentRunRecord
from core.schemas.plan import PlanSpec, TaskSpec, plan_spec_from_dict


def _sample_plan() -> PlanSpec:
    return plan_spec_from_dict(
        {
            "plan_id": "plan-exec-1",
            "session_id": "session-1",
            "goal": "List tasks",
            "policy_profile": "coding_full",
            "tasks": [
                {
                    "task_id": "task-1",
                    "title": "List scheduled tasks",
                    "tool_names": ["scheduled_tasks"],
                    "instruction": "List all scheduled tasks.",
                }
            ],
        }
    )


def test_build_worker_user_text_includes_scope() -> None:
    task = _sample_plan().tasks[0]
    text = build_worker_user_text(task=task)
    assert "[Worker task task-1]" in text
    assert "Tool scope: scheduled_tasks" in text
    assert "List all scheduled tasks." in text


def test_worker_run_budget_narrows_tools() -> None:
    plan = _sample_plan()
    budget = worker_run_budget(plan=plan, task=plan.tasks[0])
    assert budget.allowed_tool_names == ["scheduled_tasks"]
    assert budget.policy_profile == "coding_full"


def test_format_plan_execution_reply_lists_tasks() -> None:
    from core.schemas.plan import PlanRunSpec, TaskResultSpec

    plan = _sample_plan()
    plan_run = PlanRunSpec(
        plan_id=plan.plan_id,
        status="completed",
        task_results=[
            TaskResultSpec(
                task_id="task-1",
                run_id="run-worker-1",
                status="completed",
                summary="Done.",
                tool_trace_count=1,
            )
        ],
    )
    reply = format_plan_execution_reply(plan=plan, plan_run=plan_run)
    assert "completed successfully" in reply
    assert "task-1 [completed]: Done." in reply


def test_plan_executor_runs_tasks_sequentially() -> None:
    plan = _sample_plan()
    turn_result = MagicMock(
        reply="Listed reminders.",
        status="completed",
        disposition="respond",
        tool_trace=[MagicMock(action="tool", tool_name="scheduled_tasks", content="ok")],
    )
    runner = MagicMock()
    runner.run_turn = AsyncMock(return_value=turn_result)
    repo = MagicMock()
    repo.list_by_session.return_value = [
        AgentRunRecord(
            run_id="run-worker-1",
            session_id="session-1",
            source_message_id="msg:task-1",
            harness_id="default-single-agent",
            status="completed",
            agent_role="worker",
            input_metadata={"task_id": "task-1", "agent_role": "worker"},
        )
    ]
    executor = PlanExecutor(turn_runner=runner, run_record_repository=repo)
    result = asyncio.run(
        executor.execute(
            session_id="session-1",
            plan=plan,
            planner_run_id="run-planner-1",
            source_message_id="msg-exec",
            context_snapshot=MagicMock(),
        )
    )
    assert result.status == "completed"
    assert result.plan_run.status == "completed"
    assert len(result.plan_run.task_results) == 1
    assert result.plan_run.task_results[0].status == "completed"
    runner.run_turn.assert_awaited_once()


def test_in_memory_plan_store_round_trip() -> None:
    plan = _sample_plan()
    store = InMemoryPlanStore()
    store.save(
        stored=StoredValidatedPlan(
            session_id="session-1",
            plan=plan,
            planner_run_id="run-planner-1",
        )
    )
    loaded = store.get_latest(session_id="session-1")
    assert loaded is not None
    assert loaded.plan.plan_id == "plan-exec-1"
    assert loaded.planner_run_id == "run-planner-1"
