"""Plan execution replay and reporting."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from core.schemas.plan import PlanSpec, TaskSpec


@dataclass
class OperationReplay:
    """Replay information for a single operation."""
    
    timestamp: float  # Relative to plan start (seconds)
    tool_name: str
    arguments: dict[str, Any]
    status: str  # completed | failed | skipped
    duration_seconds: float
    idempotency_key: str | None = None
    skipped_reason: str | None = None  # "idempotency" | None
    error_message: str | None = None
    retry_info: dict[str, Any] | None = None  # {"retry_count": 2, "delay": 2.5}


@dataclass
class TaskReplay:
    """Replay information for a single task."""
    
    task_id: str
    title: str
    status: str  # completed | failed | pending
    retry_count: int
    start_time: float  # Relative to plan start (seconds)
    end_time: float
    duration_seconds: float
    operations: list[OperationReplay] = field(default_factory=list)
    error_category: str | None = None
    last_error: str | None = None


@dataclass
class PlanReplay:
    """Replay information for an entire plan execution."""
    
    plan_id: str
    goal: str
    status: str  # completed | failed | waiting_hitl
    total_time_seconds: float
    total_retries: int
    tasks: list[TaskReplay] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)


def build_plan_replay(
    *,
    plan: PlanSpec,
    plan_run_status: str,
    task_results: list[Any],  # list[TaskResultSpec]
    tool_trace: list[dict[str, Any]],
    total_retry_count: int,
    execution_start_time: float | None,
    execution_end_time: float | None,
) -> PlanReplay:
    """
    Build a replay report from plan execution data.
    
    Args:
        plan: The plan specification
        plan_run_status: Status of the plan run
        task_results: List of TaskResultSpec objects
        tool_trace: Complete tool trace
        total_retry_count: Total number of retries
        execution_start_time: Start timestamp
        execution_end_time: End timestamp
    
    Returns:
        PlanReplay object with complete execution history
    """
    # Calculate total time
    if execution_start_time and execution_end_time:
        total_time = execution_end_time - execution_start_time
    else:
        total_time = 0.0
    
    # Build task replays
    tasks_replay: list[TaskReplay] = []
    current_time = 0.0
    
    # Group tool trace by task
    trace_by_task = _group_trace_by_task(tool_trace)
    
    for task_result in task_results:
        task_spec = _find_task_spec(plan, task_result.task_id)
        task_title = task_spec.title if task_spec else task_result.task_id
        
        # Extract operations for this task
        task_trace = trace_by_task.get(task_result.task_id, [])
        operations = _extract_operations_from_trace(task_trace, current_time)
        
        # Calculate task duration
        task_duration = sum(op.duration_seconds for op in operations)
        if task_duration == 0 and operations:
            # Fallback: estimate from operation count
            task_duration = len(operations) * 0.5
        
        tasks_replay.append(TaskReplay(
            task_id=task_result.task_id,
            title=task_title,
            status=task_result.status,
            retry_count=task_result.retry_count,
            start_time=current_time,
            end_time=current_time + task_duration,
            duration_seconds=task_duration,
            operations=operations,
            error_category=task_result.error_category,
            last_error=task_result.last_error,
        ))
        
        current_time += task_duration
    
    return PlanReplay(
        plan_id=plan.plan_id,
        goal=plan.goal,
        status=plan_run_status,
        total_time_seconds=total_time if total_time > 0 else current_time,
        total_retries=total_retry_count,
        tasks=tasks_replay,
        events=tool_trace,
    )


def _group_trace_by_task(tool_trace: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group tool trace entries by task_id."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    current_task_id: str | None = None
    
    for entry in tool_trace:
        # Check if this is a task.retry event
        if entry.get("event") == "task.retry":
            task_id = entry.get("task_id", "")
            if task_id:
                grouped.setdefault(task_id, []).append(entry)
            continue
        
        # Regular tool trace entry
        metadata = entry.get("metadata", {})
        task_id = metadata.get("task_id")
        
        if task_id:
            current_task_id = task_id
            grouped.setdefault(task_id, []).append(entry)
        elif current_task_id:
            # Inherit task_id from previous entry
            grouped.setdefault(current_task_id, []).append(entry)
    
    return grouped


def _extract_operations_from_trace(
    task_trace: list[dict[str, Any]],
    start_time: float,
) -> list[OperationReplay]:
    """Extract operation replay information from task trace."""
    operations: list[OperationReplay] = []
    current_time = start_time
    
    for entry in task_trace:
        # Handle task.retry events
        if entry.get("event") == "task.retry":
            # This is a retry event, not an operation
            # We'll include retry info in the next operation
            continue
        
        tool_name = entry.get("tool_name", "unknown")
        arguments = entry.get("arguments", {})
        status = entry.get("status", "unknown")
        action = entry.get("action", "")
        metadata = entry.get("metadata", {})
        
        # Estimate duration (we don't have precise timing in trace)
        duration = 0.5  # Default estimate
        
        # Check if operation was skipped due to idempotency
        idempotency_key = metadata.get("idempotency_key")
        skipped = metadata.get("skipped", False)
        skipped_reason = None
        if skipped and idempotency_key:
            skipped_reason = "idempotency"
            status = "skipped"
        
        # Extract error message if failed
        error_message = None
        if status == "failed":
            error_message = entry.get("reply", "")
        
        operations.append(OperationReplay(
            timestamp=current_time,
            tool_name=tool_name,
            arguments=arguments,
            status=status,
            duration_seconds=duration,
            idempotency_key=idempotency_key,
            skipped_reason=skipped_reason,
            error_message=error_message,
            retry_info=None,  # TODO: Extract from task.retry events
        ))
        
        current_time += duration
    
    return operations


def _find_task_spec(plan: PlanSpec, task_id: str) -> TaskSpec | None:
    """Find task spec by task_id."""
    for task in plan.tasks:
        if task.task_id == task_id:
            return task
    return None


def generate_replay_report(
    *,
    replay: PlanReplay,
    format: str = "markdown",
) -> str:
    """
    Generate a replay report in the specified format.
    
    Args:
        replay: PlanReplay object
        format: Output format ("markdown" or "json")
    
    Returns:
        Formatted report string
    """
    if format == "json":
        return _format_replay_as_json(replay)
    else:
        return _format_replay_as_markdown(replay)


def _format_replay_as_markdown(replay: PlanReplay) -> str:
    """Format replay as Markdown report."""
    lines = [
        "# Plan Execution Report",
        "",
        f"**Plan ID**: {replay.plan_id}",
        f"**Goal**: {replay.goal}",
        f"**Status**: {replay.status}",
        f"**Total Time**: {replay.total_time_seconds:.1f}s",
        f"**Total Retries**: {replay.total_retries}",
        "",
    ]
    
    for task in replay.tasks:
        lines.append(f"## {task.task_id}: {task.title}")
        lines.append(f"- **Status**: {task.status}")
        lines.append(f"- **Retries**: {task.retry_count}")
        lines.append(f"- **Time**: {task.duration_seconds:.1f}s")
        
        if task.error_category:
            lines.append(f"- **Error Category**: {task.error_category}")
        if task.last_error:
            error_preview = task.last_error[:100]
            if len(task.last_error) > 100:
                error_preview += "..."
            lines.append(f"- **Error**: {error_preview}")
        
        lines.append("")
        
        if task.operations:
            lines.append("### Operations:")
            for op in task.operations:
                timestamp_str = f"[{op.timestamp:06.1f}]"
                
                # Format arguments (show first 2)
                args_items = list(op.arguments.items())[:2]
                args_str = ", ".join(f"{k}={repr(v)[:30]}" for k, v in args_items)
                if len(op.arguments) > 2:
                    args_str += ", ..."
                
                if op.status == "skipped":
                    lines.append(f"{timestamp_str} `{op.tool_name}({args_str})` → **skipped** ({op.skipped_reason})")
                    if op.idempotency_key:
                        lines.append(f"  - Idempotency key: `{op.idempotency_key}`")
                elif op.status == "failed":
                    lines.append(f"{timestamp_str} `{op.tool_name}({args_str})` → **failed**")
                    if op.error_message:
                        error_preview = op.error_message[:80]
                        if len(op.error_message) > 80:
                            error_preview += "..."
                        lines.append(f"  - Error: {error_preview}")
                    if op.retry_info:
                        lines.append(f"  - Retry after {op.retry_info.get('delay', 0):.1f}s")
                else:
                    lines.append(f"{timestamp_str} `{op.tool_name}({args_str})` → {op.status} ({op.duration_seconds:.1f}s)")
            lines.append("")
    
    return "\n".join(lines)


def _format_replay_as_json(replay: PlanReplay) -> str:
    """Format replay as JSON report."""
    data = {
        "plan_id": replay.plan_id,
        "goal": replay.goal,
        "status": replay.status,
        "total_time_seconds": replay.total_time_seconds,
        "total_retries": replay.total_retries,
        "tasks": [
            {
                "task_id": task.task_id,
                "title": task.title,
                "status": task.status,
                "retry_count": task.retry_count,
                "start_time": task.start_time,
                "end_time": task.end_time,
                "duration_seconds": task.duration_seconds,
                "error_category": task.error_category,
                "last_error": task.last_error,
                "operations": [
                    {
                        "timestamp": op.timestamp,
                        "tool_name": op.tool_name,
                        "arguments": op.arguments,
                        "status": op.status,
                        "duration_seconds": op.duration_seconds,
                        "idempotency_key": op.idempotency_key,
                        "skipped_reason": op.skipped_reason,
                        "error_message": op.error_message,
                        "retry_info": op.retry_info,
                    }
                    for op in task.operations
                ],
            }
            for task in replay.tasks
        ],
    }
    return json.dumps(data, indent=2, ensure_ascii=False)


__all__ = [
    "OperationReplay",
    "TaskReplay",
    "PlanReplay",
    "build_plan_replay",
    "generate_replay_report",
]
