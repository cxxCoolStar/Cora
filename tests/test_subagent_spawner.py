from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from core.agent.run_records import InMemoryAgentRunRecordRepository
from core.agent.subagent_spawner import SubagentSpawner, effective_max_child_runs
from core.agent.harness import new_run_input
from core.schemas.harness import RunBudget, HarnessTraceEventType
from core.schemas.subagent import SPAWN_ORCHESTRATOR_AGENT_ROLE, SpawnWorkerRequest
from core.agent.runtime_state import RuntimeContextSnapshot


def test_effective_max_child_runs() -> None:
    assert effective_max_child_runs(budget=RunBudget(max_child_runs=2), default=4) == 2
    assert effective_max_child_runs(budget=RunBudget(), default=4) == 4


def test_spawn_worker_denies_when_child_run_limit_reached() -> None:
    run_record_repository = InMemoryAgentRunRecordRepository()
    parent_session_id = "session-parent"
    parent_run_id = "spawn-parent-test"
    run_input = new_run_input(
        session_id=parent_session_id,
        source_message_id="msg-1",
        user_text="delegate",
        raw_text="delegate",
        upload=None,
        context_snapshot=RuntimeContextSnapshot(),
        agent_role=SPAWN_ORCHESTRATOR_AGENT_ROLE,
    )
    run_input.run_id = parent_run_id
    run_input.trace_id = parent_run_id
    run_record_repository.create_started(
        run_input=run_input,
        harness_id="test-harness",
        input_metadata={"spawn_orchestrator": True},
    )
    child_run_input = new_run_input(
        session_id="session-child",
        source_message_id="msg-child",
        user_text="child",
        raw_text="child",
        upload=None,
        context_snapshot=RuntimeContextSnapshot(),
        parent_run_id=parent_run_id,
        agent_role="subagent",
    )
    run_record_repository.create_started(run_input=child_run_input, harness_id="test-harness")
    run_record_repository.mark_completed(
        run_id=child_run_input.run_id,
        status="completed",
        outcome="assistant_text",
        steps=1,
        trace_events=[],
    )

    turn_runner = MagicMock()
    session_repository = MagicMock()
    spawner = SubagentSpawner(
        turn_runner=turn_runner,
        session_repository=session_repository,
        run_record_repository=run_record_repository,
        harness_id="test-harness",
        default_max_child_runs=4,
    )
    result = asyncio.run(
        spawner.spawn_worker(
            request=SpawnWorkerRequest(
                parent_session_id=parent_session_id,
                source_message_id="msg-2",
                instruction="second spawn",
                allowed_tool_names=["search_files"],
                parent_run_id=parent_run_id,
            ),
            parent_context_snapshot=RuntimeContextSnapshot(),
            run_budget=RunBudget(max_child_runs=1),
        )
    )

    assert result.denied is True
    assert "child run limit exceeded" in result.reply
    assert str(HarnessTraceEventType.SUBAGENT_SPAWN_DENIED) in result.parent_trace_events
    turn_runner.run_turn.assert_not_called()


def test_spawn_worker_runs_child_turn() -> None:
    run_record_repository = InMemoryAgentRunRecordRepository()
    turn_result = MagicMock(
        reply="Found hello_agent in src/example.py",
        status="completed",
        disposition="respond",
        tool_trace=[],
    )
    turn_runner = MagicMock()
    turn_runner.run_turn = AsyncMock(return_value=turn_result)
    child_session = MagicMock(id="session-child-1")
    session_repository = MagicMock()
    session_repository.create.return_value = child_session

    spawner = SubagentSpawner(
        turn_runner=turn_runner,
        session_repository=session_repository,
        run_record_repository=run_record_repository,
        harness_id="test-harness",
    )
    result = asyncio.run(
        spawner.spawn_worker(
            request=SpawnWorkerRequest(
                parent_session_id="session-parent",
                source_message_id="msg-1",
                instruction="Find hello_agent in src.",
                allowed_tool_names=["search_files"],
            ),
            parent_context_snapshot=RuntimeContextSnapshot(),
            run_budget=RunBudget(allowed_tool_names=["search_files"], max_child_runs=2),
        )
    )

    assert result.denied is False
    assert result.child_session_id == "session-child-1"
    assert "Subagent completed" in result.reply
    assert "status=completed" in result.reply
    assert result.child_result is not None
    assert "subagent.spawned" in result.parent_trace_events
    assert "subagent.completed" in result.parent_trace_events
    turn_runner.run_turn.assert_awaited_once()
