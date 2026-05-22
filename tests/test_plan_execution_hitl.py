from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from core.agent.plan_execution_state import StoredPlanExecution
from core.agent.plan_executor import PlanExecutor, PlanExecutionResult
from core.agent.plan_store import InMemoryPlanStore
from core.schemas.plan import PlanSpec, TaskSpec, plan_spec_from_dict


def _hitl_plan() -> PlanSpec:
    return plan_spec_from_dict(
        {
            "plan_id": "plan-hitl-1",
            "session_id": "session-1",
            "goal": "List reminders",
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


def test_plan_store_execution_round_trip() -> None:
    store = InMemoryPlanStore()
    plan = _hitl_plan()
    store.save_execution(
        execution=StoredPlanExecution(
            session_id="session-1",
            plan=plan,
            planner_run_id="run-planner",
            source_message_id="msg-1",
            task_index=0,
            pending_hitl_id="hitl-1",
        )
    )
    loaded = store.get_execution(session_id="session-1")
    assert loaded is not None
    assert loaded.pending_hitl_id == "hitl-1"
    store.clear_execution(session_id="session-1")
    assert store.get_execution(session_id="session-1") is None


def test_plan_executor_resume_from_next_task_index() -> None:
    plan = plan_spec_from_dict(
        {
            "plan_id": "plan-2",
            "session_id": "session-1",
            "goal": "Two steps",
            "tasks": [
                {
                    "task_id": "task-1",
                    "title": "Done",
                    "tool_names": ["search_files"],
                    "instruction": "step one",
                },
                {
                    "task_id": "task-2",
                    "title": "Two",
                    "tool_names": ["search_files"],
                    "instruction": "step two",
                },
            ],
        }
    )
    turn_result = MagicMock(
        reply="step two ok",
        status="completed",
        disposition="respond",
        tool_trace=[],
    )
    runner = MagicMock()
    runner.run_turn = AsyncMock(return_value=turn_result)
    executor = PlanExecutor(turn_runner=runner)
    result = asyncio.run(
        executor.execute(
            session_id="session-1",
            plan=plan,
            planner_run_id="run-planner",
            source_message_id="msg-1",
            context_snapshot=MagicMock(),
            start_task_index=1,
            initial_task_results=[],
        )
    )
    assert result.status == "completed"
    assert len(result.plan_run.task_results) == 1
    runner.run_turn.assert_awaited_once()
