from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

from core.agent.plan_execution_state import StoredPlanExecution

ExecutePlanMode = Literal["start", "resume", "restart"]


@dataclass(frozen=True, slots=True)
class ExecutePlanCommand:
    mode: ExecutePlanMode = "start"


def parse_execute_plan_command(text: str | None) -> ExecutePlanCommand:
    normalized = str(text or "").strip().lower()
    if normalized in {"/execute restart", "/execute fresh", "/execute reset"}:
        return ExecutePlanCommand(mode="restart")
    if normalized in {"/execute resume", "/execute continue"}:
        return ExecutePlanCommand(mode="resume")
    return ExecutePlanCommand(mode="start")


def should_auto_resume_failed_checkpoint(
    *,
    command: ExecutePlanCommand,
    checkpoint: StoredPlanExecution | None,
) -> bool:
    if command.mode != "start":
        return False
    if checkpoint is None:
        return False
    if str(checkpoint.pending_hitl_id or "").strip():
        return False
    return str(checkpoint.pause_reason or "").strip().lower() in {"failed", "timeout"}


def checkpoint_resume_task_index(*, checkpoint: StoredPlanExecution) -> int:
    return max(0, int(checkpoint.task_index))


def checkpoint_resume_task_results(
    *,
    checkpoint: StoredPlanExecution,
) -> list:
    from core.schemas.plan import TaskResultSpec

    results: list[TaskResultSpec] = list(checkpoint.task_results)
    if results and str(results[-1].status or "").strip() == "failed":
        return results[:-1]
    return results


def new_checkpoint_id() -> str:
    return f"ckpt-{uuid4().hex[:12]}"


__all__ = [
    "ExecutePlanCommand",
    "ExecutePlanMode",
    "checkpoint_resume_task_index",
    "checkpoint_resume_task_results",
    "new_checkpoint_id",
    "parse_execute_plan_command",
    "should_auto_resume_failed_checkpoint",
]
