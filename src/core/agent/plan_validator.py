from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.agent.policy_profiles import HARNESS_POLICY_PROFILES
from core.schemas.plan import PlanSpec, plan_spec_from_dict


DEFAULT_MAX_TASKS = 32
MAX_PARALLEL_SUBAGENTS_PER_TASK = 8

WECHAT_FORBIDDEN_PLAN_TOOLS = frozenset(
    {
        "shell_exec",
        "browser_navigate",
        "browser_click",
        "browser_type",
        "browser_back",
    }
)


@dataclass(frozen=True, slots=True)
class PlanValidationIssue:
    code: str
    message: str
    task_id: str | None = None
    field: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "code": self.code,
            "message": self.message,
            "task_id": self.task_id,
            "field": self.field,
        }


@dataclass(slots=True)
class PlanValidationResult:
    valid: bool
    plan: PlanSpec | None = None
    issues: list[PlanValidationIssue] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "plan": self.plan.to_dict() if self.plan is not None else None,
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(frozen=True, slots=True)
class PlanValidationOptions:
    registered_tool_names: frozenset[str]
    forbidden_tool_names: frozenset[str] = field(default_factory=frozenset)
    platform: str | None = None
    max_tasks: int = DEFAULT_MAX_TASKS


class PlanValidator:
    def validate(
        self,
        payload: PlanSpec | dict[str, Any],
        *,
        options: PlanValidationOptions,
    ) -> PlanValidationResult:
        issues: list[PlanValidationIssue] = []
        try:
            plan = payload if isinstance(payload, PlanSpec) else plan_spec_from_dict(payload)
        except ValueError as exc:
            return PlanValidationResult(
                valid=False,
                issues=[
                    PlanValidationIssue(
                        code="invalid_plan_shape",
                        message=str(exc),
                    )
                ],
            )

        forbidden = set(options.forbidden_tool_names)
        if (options.platform or "").strip().lower() in {"wechat", "weixin"}:
            forbidden.update(WECHAT_FORBIDDEN_PLAN_TOOLS)

        if not plan.tasks:
            issues.append(
                PlanValidationIssue(
                    code="empty_tasks",
                    message="Plan must include at least one task.",
                    field="tasks",
                )
            )

        if len(plan.tasks) > max(1, int(options.max_tasks)):
            issues.append(
                PlanValidationIssue(
                    code="too_many_tasks",
                    message=f"Plan exceeds max task count ({options.max_tasks}).",
                    field="tasks",
                )
            )

        if plan.policy_profile and plan.policy_profile not in HARNESS_POLICY_PROFILES:
            issues.append(
                PlanValidationIssue(
                    code="invalid_policy_profile",
                    message=f"Unknown policy_profile: {plan.policy_profile}",
                    field="policy_profile",
                )
            )

        seen_task_ids: set[str] = set()
        for task in plan.tasks:
            if task.task_id in seen_task_ids:
                issues.append(
                    PlanValidationIssue(
                        code="duplicate_task_id",
                        message=f"Duplicate task_id: {task.task_id}",
                        task_id=task.task_id,
                        field="task_id",
                    )
                )
            seen_task_ids.add(task.task_id)

            if task.uses_parallel_subagents():
                if len(task.parallel_subagents) > MAX_PARALLEL_SUBAGENTS_PER_TASK:
                    issues.append(
                        PlanValidationIssue(
                            code="too_many_parallel_subagents",
                            message=(
                                f"Task exceeds max parallel subagents ({MAX_PARALLEL_SUBAGENTS_PER_TASK})."
                            ),
                            task_id=task.task_id,
                            field="parallel_subagents",
                        )
                    )
                for index, subtask in enumerate(task.parallel_subagents, start=1):
                    if not subtask.tool_names:
                        issues.append(
                            PlanValidationIssue(
                                code="empty_tool_names",
                                message="Each parallel subagent must list at least one tool.",
                                task_id=task.task_id,
                                field=f"parallel_subagents[{index - 1}].tool_names",
                            )
                        )
                    for tool_name in subtask.tool_names:
                        if tool_name not in options.registered_tool_names:
                            issues.append(
                                PlanValidationIssue(
                                    code="unknown_tool",
                                    message=f"Tool `{tool_name}` is not registered.",
                                    task_id=task.task_id,
                                    field=f"parallel_subagents[{index - 1}].tool_names",
                                )
                            )
                        if tool_name in forbidden:
                            issues.append(
                                PlanValidationIssue(
                                    code="forbidden_tool",
                                    message=f"Tool `{tool_name}` is not allowed in this plan context.",
                                    task_id=task.task_id,
                                    field=f"parallel_subagents[{index - 1}].tool_names",
                                )
                            )
            elif not task.tool_names:
                issues.append(
                    PlanValidationIssue(
                        code="empty_tool_names",
                        message="Each worker task must list at least one tool.",
                        task_id=task.task_id,
                        field="tool_names",
                    )
                )

            for tool_name in task.tool_names:
                if tool_name not in options.registered_tool_names:
                    issues.append(
                        PlanValidationIssue(
                            code="unknown_tool",
                            message=f"Tool `{tool_name}` is not registered.",
                            task_id=task.task_id,
                            field="tool_names",
                        )
                    )
                if tool_name in forbidden:
                    issues.append(
                        PlanValidationIssue(
                            code="forbidden_tool",
                            message=f"Tool `{tool_name}` is not allowed in this plan context.",
                            task_id=task.task_id,
                            field="tool_names",
                        )
                    )

        if issues:
            return PlanValidationResult(valid=False, plan=plan, issues=issues)
        return PlanValidationResult(valid=True, plan=plan, issues=[])


def validation_options_from_tool_names(
    *,
    registered_tool_names: list[str] | frozenset[str],
    platform: str | None = None,
    forbidden_tool_names: list[str] | frozenset[str] | None = None,
    max_tasks: int = DEFAULT_MAX_TASKS,
) -> PlanValidationOptions:
    return PlanValidationOptions(
        registered_tool_names=frozenset(registered_tool_names),
        forbidden_tool_names=frozenset(forbidden_tool_names or ()),
        platform=platform,
        max_tasks=max_tasks,
    )


__all__ = [
    "DEFAULT_MAX_TASKS",
    "MAX_PARALLEL_SUBAGENTS_PER_TASK",
    "PlanValidationIssue",
    "PlanValidationOptions",
    "PlanValidationResult",
    "PlanValidator",
    "WECHAT_FORBIDDEN_PLAN_TOOLS",
    "validation_options_from_tool_names",
]
