from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4


PlanRunStatus = Literal["planning", "executing", "waiting_hitl", "completed", "failed"]
TaskResultStatus = Literal["completed", "failed", "skipped", "pending"]


def _non_empty_str(value: object, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    names: list[str] = []
    for item in value:
        name = str(item or "").strip()
        if name and name not in names:
            names.append(name)
    return names


@dataclass(slots=True)
class PlanSubtaskSpec:
    instruction: str
    tool_names: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "instruction": self.instruction,
            "tool_names": list(self.tool_names),
        }


@dataclass(slots=True)
class TaskSpec:
    task_id: str
    title: str
    tool_names: list[str]
    instruction: str
    requires_review: bool = False
    parallel_subagents: list[PlanSubtaskSpec] = field(default_factory=list)

    def uses_parallel_subagents(self) -> bool:
        return bool(self.parallel_subagents)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "task_id": self.task_id,
            "title": self.title,
            "tool_names": list(self.tool_names),
            "instruction": self.instruction,
            "requires_review": self.requires_review,
        }
        if self.parallel_subagents:
            payload["parallel_subagents"] = [item.to_dict() for item in self.parallel_subagents]
        return payload


@dataclass(slots=True)
class PlanSpec:
    plan_id: str
    session_id: str
    goal: str
    tasks: list[TaskSpec]
    policy_profile: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "session_id": self.session_id,
            "goal": self.goal,
            "tasks": [task.to_dict() for task in self.tasks],
            "policy_profile": self.policy_profile,
        }


@dataclass(slots=True)
class TaskResultSpec:
    task_id: str
    run_id: str
    status: TaskResultStatus = "pending"
    summary: str = ""
    tool_trace_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "run_id": self.run_id,
            "status": self.status,
            "summary": self.summary,
            "tool_trace_count": self.tool_trace_count,
        }


@dataclass(slots=True)
class PlanRunSpec:
    plan_id: str
    status: PlanRunStatus = "planning"
    task_results: list[TaskResultSpec] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "status": self.status,
            "task_results": [result.to_dict() for result in self.task_results],
        }


def new_plan_id() -> str:
    return f"plan-{uuid4().hex}"


def plan_subtask_spec_from_dict(payload: dict[str, Any]) -> PlanSubtaskSpec:
    return PlanSubtaskSpec(
        instruction=_non_empty_str(payload.get("instruction"), field_name="subtask.instruction"),
        tool_names=_string_list(payload.get("tool_names")),
    )


def task_spec_from_dict(payload: dict[str, Any]) -> TaskSpec:
    raw_parallel = payload.get("parallel_subagents")
    parallel_subagents: list[PlanSubtaskSpec] = []
    if isinstance(raw_parallel, list):
        parallel_subagents = [
            plan_subtask_spec_from_dict(item)
            for item in raw_parallel
            if isinstance(item, dict)
        ]
    return TaskSpec(
        task_id=_non_empty_str(payload.get("task_id"), field_name="task.task_id"),
        title=_non_empty_str(payload.get("title"), field_name="task.title"),
        tool_names=_string_list(payload.get("tool_names")),
        instruction=_non_empty_str(payload.get("instruction"), field_name="task.instruction"),
        requires_review=bool(payload.get("requires_review")),
        parallel_subagents=parallel_subagents,
    )


def plan_spec_from_dict(payload: dict[str, Any]) -> PlanSpec:
    if not isinstance(payload, dict):
        raise ValueError("plan payload must be an object")
    raw_tasks = payload.get("tasks")
    if not isinstance(raw_tasks, list):
        raise ValueError("plan.tasks must be a list")
    tasks = [task_spec_from_dict(item) for item in raw_tasks if isinstance(item, dict)]
    return PlanSpec(
        plan_id=_non_empty_str(payload.get("plan_id"), field_name="plan_id"),
        session_id=_non_empty_str(payload.get("session_id"), field_name="session_id"),
        goal=_non_empty_str(payload.get("goal"), field_name="goal"),
        tasks=tasks,
        policy_profile=str(payload.get("policy_profile") or "").strip() or None,
    )


__all__ = [
    "PlanRunSpec",
    "PlanRunStatus",
    "PlanSpec",
    "PlanSubtaskSpec",
    "TaskResultSpec",
    "TaskResultStatus",
    "TaskSpec",
    "new_plan_id",
    "plan_spec_from_dict",
    "plan_subtask_spec_from_dict",
    "task_spec_from_dict",
]
