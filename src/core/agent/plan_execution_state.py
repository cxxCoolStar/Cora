from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.schemas.plan import PlanSpec, TaskResultSpec, plan_spec_from_dict


@dataclass(slots=True)
class StoredPlanExecution:
    session_id: str
    plan: PlanSpec
    planner_run_id: str
    source_message_id: str
    task_index: int
    task_results: list[TaskResultSpec] = field(default_factory=list)
    pending_hitl_id: str = ""
    pause_reason: str = ""
    checkpoint_id: str = ""
    run_metadata: dict[str, Any] = field(default_factory=dict)
    completed_operations_cache: dict[str, str] = field(default_factory=dict)
    """Global cache of completed operations: {idempotency_key: result_summary}"""

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "plan": self.plan.to_dict(),
            "planner_run_id": self.planner_run_id,
            "source_message_id": self.source_message_id,
            "task_index": self.task_index,
            "task_results": [result.to_dict() for result in self.task_results],
            "pending_hitl_id": self.pending_hitl_id,
            "pause_reason": self.pause_reason,
            "checkpoint_id": self.checkpoint_id,
            "run_metadata": dict(self.run_metadata),
            "completed_operations_cache": dict(self.completed_operations_cache),
        }


def stored_plan_execution_from_dict(payload: dict[str, Any]) -> StoredPlanExecution:
    raw_results = payload.get("task_results") or []
    task_results = []
    if isinstance(raw_results, list):
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            completed_ops = item.get("completed_operations") or []
            task_results.append(
                TaskResultSpec(
                    task_id=str(item.get("task_id") or ""),
                    run_id=str(item.get("run_id") or ""),
                    status=str(item.get("status") or "pending"),  # type: ignore[arg-type]
                    summary=str(item.get("summary") or ""),
                    tool_trace_count=int(item.get("tool_trace_count") or 0),
                    completed_operations=list(completed_ops) if isinstance(completed_ops, list) else [],
                )
            )
    
    raw_cache = payload.get("completed_operations_cache") or {}
    completed_operations_cache = dict(raw_cache) if isinstance(raw_cache, dict) else {}
    
    return StoredPlanExecution(
        session_id=str(payload.get("session_id") or ""),
        plan=plan_spec_from_dict(dict(payload.get("plan") or {})),
        planner_run_id=str(payload.get("planner_run_id") or ""),
        source_message_id=str(payload.get("source_message_id") or ""),
        task_index=int(payload.get("task_index") or 0),
        task_results=task_results,
        pending_hitl_id=str(payload.get("pending_hitl_id") or ""),
        pause_reason=str(payload.get("pause_reason") or ""),
        checkpoint_id=str(payload.get("checkpoint_id") or ""),
        run_metadata=dict(payload.get("run_metadata") or {}),
        completed_operations_cache=completed_operations_cache,
    )


__all__ = ["StoredPlanExecution", "stored_plan_execution_from_dict"]
