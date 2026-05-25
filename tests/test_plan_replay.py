"""Tests for plan execution replay."""

from __future__ import annotations

import json

from core.agent.plan_replay import (
    OperationReplay,
    PlanReplay,
    TaskReplay,
    build_plan_replay,
    generate_replay_report,
)
from core.schemas.plan import PlanSpec, TaskResultSpec, TaskSpec


def test_operation_replay_creation() -> None:
    """OperationReplay should store operation details."""
    op = OperationReplay(
        timestamp=1.5,
        tool_name="write_file",
        arguments={"path": "config.py", "content": "data"},
        status="completed",
        duration_seconds=0.8,
        idempotency_key="run-123:task-1:write_file:config.py",
    )
    
    assert op.timestamp == 1.5
    assert op.tool_name == "write_file"
    assert op.status == "completed"
    assert op.idempotency_key == "run-123:task-1:write_file:config.py"


def test_task_replay_creation() -> None:
    """TaskReplay should store task execution details."""
    task = TaskReplay(
        task_id="task-1",
        title="Process data",
        status="completed",
        retry_count=2,
        start_time=0.0,
        end_time=5.0,
        duration_seconds=5.0,
        operations=[],
    )
    
    assert task.task_id == "task-1"
    assert task.retry_count == 2
    assert task.duration_seconds == 5.0


def test_plan_replay_creation() -> None:
    """PlanReplay should store complete plan execution."""
    replay = PlanReplay(
        plan_id="plan-123",
        goal="Test goal",
        status="completed",
        total_time_seconds=10.5,
        total_retries=3,
        tasks=[],
        events=[],
    )
    
    assert replay.plan_id == "plan-123"
    assert replay.total_retries == 3
    assert replay.total_time_seconds == 10.5


def test_build_plan_replay_basic() -> None:
    """build_plan_replay should construct replay from execution data."""
    plan = PlanSpec(
        plan_id="plan-test",
        session_id="session-1",
        goal="Test plan",
        tasks=[
            TaskSpec(
                task_id="task-1",
                title="First task",
                tool_names=["search_files"],
                instruction="Search files",
            ),
        ],
    )
    
    task_results = [
        TaskResultSpec(
            task_id="task-1",
            run_id="run-1",
            status="completed",
            summary="Done",
            retry_count=0,
        ),
    ]
    
    tool_trace = [
        {
            "tool_name": "search_files",
            "arguments": {"path": "src"},
            "status": "completed",
            "metadata": {"task_id": "task-1"},
        },
    ]
    
    replay = build_plan_replay(
        plan=plan,
        plan_run_status="completed",
        task_results=task_results,
        tool_trace=tool_trace,
        total_retry_count=0,
        execution_start_time=1000.0,
        execution_end_time=1005.0,
    )
    
    assert replay.plan_id == "plan-test"
    assert replay.status == "completed"
    assert replay.total_time_seconds == 5.0
    assert len(replay.tasks) == 1
    assert replay.tasks[0].task_id == "task-1"


def test_build_plan_replay_with_retries() -> None:
    """build_plan_replay should include retry information."""
    plan = PlanSpec(
        plan_id="plan-test",
        session_id="session-1",
        goal="Test plan",
        tasks=[
            TaskSpec(
                task_id="task-1",
                title="Retry task",
                tool_names=["call_api"],
                instruction="Call API",
            ),
        ],
    )
    
    task_results = [
        TaskResultSpec(
            task_id="task-1",
            run_id="run-1",
            status="completed",
            summary="Done after retries",
            retry_count=2,
            error_category="timeout",
        ),
    ]
    
    tool_trace = [
        {
            "event": "task.retry",
            "task_id": "task-1",
            "retry_count": 1,
            "delay_seconds": 1.5,
            "error_category": "timeout",
        },
        {
            "tool_name": "call_api",
            "arguments": {},
            "status": "completed",
            "metadata": {"task_id": "task-1"},
        },
    ]
    
    replay = build_plan_replay(
        plan=plan,
        plan_run_status="completed",
        task_results=task_results,
        tool_trace=tool_trace,
        total_retry_count=2,
        execution_start_time=None,
        execution_end_time=None,
    )
    
    assert replay.total_retries == 2
    assert replay.tasks[0].retry_count == 2
    assert replay.tasks[0].error_category == "timeout"


def test_build_plan_replay_with_skipped_operations() -> None:
    """build_plan_replay should identify skipped operations."""
    plan = PlanSpec(
        plan_id="plan-test",
        session_id="session-1",
        goal="Test plan",
        tasks=[
            TaskSpec(
                task_id="task-1",
                title="Resume task",
                tool_names=["write_file"],
                instruction="Write file",
            ),
        ],
    )
    
    task_results = [
        TaskResultSpec(
            task_id="task-1",
            run_id="run-1",
            status="completed",
            summary="Done",
            retry_count=0,
        ),
    ]
    
    tool_trace = [
        {
            "tool_name": "write_file",
            "arguments": {"path": "config.py"},
            "status": "completed",
            "metadata": {
                "task_id": "task-1",
                "idempotency_key": "run-1:task-1:write_file:config.py",
                "skipped": True,
            },
        },
    ]
    
    replay = build_plan_replay(
        plan=plan,
        plan_run_status="completed",
        task_results=task_results,
        tool_trace=tool_trace,
        total_retry_count=0,
        execution_start_time=None,
        execution_end_time=None,
    )
    
    assert len(replay.tasks[0].operations) == 1
    op = replay.tasks[0].operations[0]
    assert op.status == "skipped"
    assert op.skipped_reason == "idempotency"
    assert op.idempotency_key == "run-1:task-1:write_file:config.py"


def test_generate_replay_report_markdown() -> None:
    """generate_replay_report should produce readable Markdown."""
    replay = PlanReplay(
        plan_id="plan-123",
        goal="Test goal",
        status="completed",
        total_time_seconds=5.5,
        total_retries=1,
        tasks=[
            TaskReplay(
                task_id="task-1",
                title="First task",
                status="completed",
                retry_count=1,
                start_time=0.0,
                end_time=5.5,
                duration_seconds=5.5,
                operations=[
                    OperationReplay(
                        timestamp=0.0,
                        tool_name="search_files",
                        arguments={"path": "src"},
                        status="completed",
                        duration_seconds=1.0,
                    ),
                    OperationReplay(
                        timestamp=1.0,
                        tool_name="write_file",
                        arguments={"path": "config.py"},
                        status="skipped",
                        duration_seconds=0.0,
                        idempotency_key="run-1:task-1:write_file:config.py",
                        skipped_reason="idempotency",
                    ),
                ],
            ),
        ],
        events=[],
    )
    
    report = generate_replay_report(replay=replay, format="markdown")
    
    assert "# Plan Execution Report" in report
    assert "**Plan ID**: plan-123" in report
    assert "**Total Retries**: 1" in report
    assert "## task-1: First task" in report
    assert "search_files" in report
    assert "write_file" in report
    assert "skipped" in report
    assert "idempotency" in report


def test_generate_replay_report_json() -> None:
    """generate_replay_report should produce valid JSON."""
    replay = PlanReplay(
        plan_id="plan-123",
        goal="Test goal",
        status="completed",
        total_time_seconds=5.5,
        total_retries=0,
        tasks=[
            TaskReplay(
                task_id="task-1",
                title="First task",
                status="completed",
                retry_count=0,
                start_time=0.0,
                end_time=5.5,
                duration_seconds=5.5,
                operations=[],
            ),
        ],
        events=[],
    )
    
    report = generate_replay_report(replay=replay, format="json")
    
    # Should be valid JSON
    data = json.loads(report)
    assert data["plan_id"] == "plan-123"
    assert data["status"] == "completed"
    assert data["total_retries"] == 0
    assert len(data["tasks"]) == 1
    assert data["tasks"][0]["task_id"] == "task-1"


def test_format_replay_shows_error_details() -> None:
    """Replay report should show error details for failed tasks."""
    replay = PlanReplay(
        plan_id="plan-123",
        goal="Test goal",
        status="failed",
        total_time_seconds=2.0,
        total_retries=0,
        tasks=[
            TaskReplay(
                task_id="task-1",
                title="Failed task",
                status="failed",
                retry_count=0,
                start_time=0.0,
                end_time=2.0,
                duration_seconds=2.0,
                error_category="permission_denied",
                last_error="Permission denied: cannot write to protected file",
                operations=[],
            ),
        ],
        events=[],
    )
    
    report = generate_replay_report(replay=replay, format="markdown")
    
    assert "**Error Category**: permission_denied" in report
    assert "Permission denied" in report
