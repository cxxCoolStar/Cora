"""Tests for idempotency key generation and mutating tool metadata."""

from __future__ import annotations

import pytest

from core.agent.idempotency import (
    MUTATING_TOOLS,
    generate_idempotency_key,
    is_tool_idempotent,
)
from core.agent.plan_execution_state import (
    StoredPlanExecution,
    stored_plan_execution_from_dict,
)
from core.schemas.plan import PlanSpec, TaskResultSpec, TaskSpec


def test_generate_idempotency_key_for_write_file() -> None:
    """write_file should generate idempotency key with file path."""
    key = generate_idempotency_key(
        run_id="run-abc123",
        task_id="task-2",
        tool_name="write_file",
        tool_arguments={"path": "config.py", "content": "new content"},
    )
    assert key == "run-abc123:task-2:write_file:config.py"


def test_generate_idempotency_key_for_delete_item() -> None:
    """delete_item should generate idempotency key with item_id."""
    key = generate_idempotency_key(
        run_id="run-xyz",
        task_id="task-1",
        tool_name="delete_item",
        tool_arguments={"item_id": "item-456"},
    )
    assert key == "run-xyz:task-1:delete_item:item-456"


def test_generate_idempotency_key_for_non_mutating_tool() -> None:
    """read_file should return None (not a mutating tool)."""
    key = generate_idempotency_key(
        run_id="run-abc",
        task_id="task-1",
        tool_name="read_file",
        tool_arguments={"path": "config.py"},
    )
    assert key is None


def test_generate_idempotency_key_missing_target() -> None:
    """Should return None if semantic target cannot be extracted."""
    key = generate_idempotency_key(
        run_id="run-abc",
        task_id="task-1",
        tool_name="write_file",
        tool_arguments={},  # Missing 'path'
    )
    assert key is None


def test_generate_idempotency_key_empty_target() -> None:
    """Should return None if semantic target is empty."""
    key = generate_idempotency_key(
        run_id="run-abc",
        task_id="task-1",
        tool_name="write_file",
        tool_arguments={"path": ""},
    )
    assert key is None


def test_is_tool_idempotent_for_write_file() -> None:
    """write_file is idempotent (overwrite)."""
    assert is_tool_idempotent("write_file") is True


def test_is_tool_idempotent_for_append_to_file() -> None:
    """append_to_file is NOT idempotent."""
    assert is_tool_idempotent("append_to_file") is False


def test_is_tool_idempotent_for_non_mutating_tool() -> None:
    """Non-mutating tools are considered idempotent."""
    assert is_tool_idempotent("read_file") is True
    assert is_tool_idempotent("search_files") is True


def test_mutating_tools_metadata_structure() -> None:
    """MUTATING_TOOLS should have required fields."""
    for tool_name, meta in MUTATING_TOOLS.items():
        assert "is_mutating" in meta
        assert "is_idempotent" in meta
        assert "semantic_target_extractor" in meta
        assert callable(meta["semantic_target_extractor"])


def test_task_result_spec_with_completed_operations() -> None:
    """TaskResultSpec should serialize completed_operations."""
    result = TaskResultSpec(
        task_id="task-1",
        run_id="run-abc",
        status="completed",
        summary="Task completed",
        tool_trace_count=3,
        completed_operations=[
            "run-abc:task-1:write_file:config.py",
            "run-abc:task-1:delete_item:item-123",
        ],
    )
    
    result_dict = result.to_dict()
    assert result_dict["completed_operations"] == [
        "run-abc:task-1:write_file:config.py",
        "run-abc:task-1:delete_item:item-123",
    ]


def test_stored_plan_execution_with_completed_operations_cache() -> None:
    """StoredPlanExecution should serialize completed_operations_cache."""
    plan = PlanSpec(
        plan_id="plan-test",
        session_id="session-test",
        goal="test goal",
        policy_profile="coding_full",
        tasks=[
            TaskSpec(
                task_id="task-1",
                title="Task 1",
                tool_names=["write_file"],
                instruction="Write a file",
            )
        ],
    )
    
    execution = StoredPlanExecution(
        session_id="session-test",
        plan=plan,
        planner_run_id="run-planner",
        source_message_id="msg-1",
        task_index=0,
        task_results=[
            TaskResultSpec(
                task_id="task-1",
                run_id="run-worker",
                status="completed",
                summary="Done",
                completed_operations=["run-worker:task-1:write_file:config.py"],
            )
        ],
        pause_reason="failed",
        checkpoint_id="ckpt-123",
        completed_operations_cache={
            "run-worker:task-1:write_file:config.py": "Modified config.py"
        },
    )
    
    execution_dict = execution.to_dict()
    assert execution_dict["completed_operations_cache"] == {
        "run-worker:task-1:write_file:config.py": "Modified config.py"
    }


def test_stored_plan_execution_from_dict_with_completed_operations() -> None:
    """stored_plan_execution_from_dict should deserialize completed_operations."""
    payload = {
        "session_id": "session-test",
        "plan": {
            "plan_id": "plan-test",
            "session_id": "session-test",
            "goal": "test",
            "tasks": [
                {
                    "task_id": "task-1",
                    "title": "Task 1",
                    "tool_names": ["write_file"],
                    "instruction": "Write",
                }
            ],
        },
        "planner_run_id": "run-planner",
        "source_message_id": "msg-1",
        "task_index": 0,
        "task_results": [
            {
                "task_id": "task-1",
                "run_id": "run-worker",
                "status": "completed",
                "summary": "Done",
                "tool_trace_count": 1,
                "completed_operations": ["run-worker:task-1:write_file:config.py"],
            }
        ],
        "pause_reason": "failed",
        "checkpoint_id": "ckpt-123",
        "completed_operations_cache": {
            "run-worker:task-1:write_file:config.py": "Modified config.py"
        },
    }
    
    execution = stored_plan_execution_from_dict(payload)
    
    assert len(execution.task_results) == 1
    assert execution.task_results[0].completed_operations == [
        "run-worker:task-1:write_file:config.py"
    ]
    assert execution.completed_operations_cache == {
        "run-worker:task-1:write_file:config.py": "Modified config.py"
    }


def test_stored_plan_execution_from_dict_handles_missing_completed_operations() -> None:
    """Should handle missing completed_operations gracefully (backward compatibility)."""
    payload = {
        "session_id": "session-test",
        "plan": {
            "plan_id": "plan-test",
            "session_id": "session-test",
            "goal": "test",
            "tasks": [
                {
                    "task_id": "task-1",
                    "title": "Task 1",
                    "tool_names": ["write_file"],
                    "instruction": "Write",
                }
            ],
        },
        "planner_run_id": "run-planner",
        "source_message_id": "msg-1",
        "task_index": 0,
        "task_results": [
            {
                "task_id": "task-1",
                "run_id": "run-worker",
                "status": "completed",
                "summary": "Done",
                "tool_trace_count": 1,
                # No completed_operations field
            }
        ],
        "pause_reason": "failed",
        "checkpoint_id": "ckpt-123",
        # No completed_operations_cache field
    }
    
    execution = stored_plan_execution_from_dict(payload)
    
    assert execution.task_results[0].completed_operations == []
    assert execution.completed_operations_cache == {}


def test_checkpoint_persists_and_resumes_with_completed_operations() -> None:
    """Checkpoint should persist completed_operations_cache and resume should inject them."""
    plan = PlanSpec(
        plan_id="plan-test",
        session_id="session-test",
        goal="test goal",
        policy_profile="coding_full",
        tasks=[
            TaskSpec(
                task_id="task-1",
                title="Task 1",
                tool_names=["write_file"],
                instruction="Write config.py",
            ),
            TaskSpec(
                task_id="task-2",
                title="Task 2",
                tool_names=["write_file"],
                instruction="Write data.txt",
            ),
        ],
    )
    
    # Simulate task-1 completed with write_file operation
    task1_result = TaskResultSpec(
        task_id="task-1",
        run_id="run-worker-1",
        status="completed",
        summary="Modified config.py",
        completed_operations=["run-worker-1:task-1:write_file:config.py"],
    )
    
    # Create checkpoint after task-1 fails
    checkpoint = StoredPlanExecution(
        session_id="session-test",
        plan=plan,
        planner_run_id="run-planner",
        source_message_id="msg-1",
        task_index=0,
        task_results=[task1_result],
        pause_reason="failed",
        checkpoint_id="ckpt-123",
        completed_operations_cache={
            "run-worker-1:task-1:write_file:config.py": "Modified config.py"
        },
    )
    
    # Verify serialization
    checkpoint_dict = checkpoint.to_dict()
    assert "completed_operations_cache" in checkpoint_dict
    assert checkpoint_dict["completed_operations_cache"] == {
        "run-worker-1:task-1:write_file:config.py": "Modified config.py"
    }
    
    # Verify deserialization
    restored = stored_plan_execution_from_dict(checkpoint_dict)
    assert restored.completed_operations_cache == {
        "run-worker-1:task-1:write_file:config.py": "Modified config.py"
    }
    assert restored.task_results[0].completed_operations == [
        "run-worker-1:task-1:write_file:config.py"
    ]
