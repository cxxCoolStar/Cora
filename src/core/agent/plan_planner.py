from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.agent.plan_parser import parse_plan_json_from_text
from core.agent.plan_validator import (
    PlanValidationOptions,
    PlanValidationResult,
    PlanValidator,
    validation_options_from_tool_names,
)
from core.schemas.harness import HarnessRunInput, HarnessTraceEventType, RunBudget
from core.schemas.plan import PlanSpec, new_plan_id
from core.agent.loop import LoopResult


PLANNER_AGENT_ROLE = "planner"
PLANNER_POLICY_PROFILE = "planner_readonly"


def build_planner_user_text(*, user_text: str, session_id: str) -> str:
    goal = str(user_text or "").strip()
    if goal.startswith("/plan"):
        goal = goal[5:].strip() or goal
    return (
        f"{goal}\n\n"
        "[Planner mode] Respond with a single JSON object only (no markdown prose). "
        "Use this shape:\n"
        "{\n"
        f'  "plan_id": "{new_plan_id()}",\n'
        f'  "session_id": "{session_id}",\n'
        '  "goal": "<short goal>",\n'
        '  "policy_profile": "coding_full",\n'
        '  "tasks": [\n'
        "    {\n"
        '      "task_id": "task-1",\n'
        '      "title": "<step title>",\n'
        '      "tool_names": ["<tool>"],\n'
        '      "instruction": "<what the worker should do>"\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "Use only tools appropriate for each worker step. Do not execute worker tools yourself."
    )


def planner_run_budget(*, max_steps: int | None = 4) -> RunBudget:
    return RunBudget(
        policy_profile=PLANNER_POLICY_PROFILE,
        max_steps=max_steps,
        max_tool_calls=8,
    )


def resolve_planner_validation_options(
    *,
    registered_tool_names: frozenset[str] | list[str],
    platform: str | None = None,
) -> PlanValidationOptions:
    return validation_options_from_tool_names(
        registered_tool_names=registered_tool_names,
        platform=platform,
    )


@dataclass(slots=True)
class PlannerFinalizeResult:
    loop_result: LoopResult
    validation: PlanValidationResult | None
    parse_error: str | None = None


def finalize_planner_run(
    *,
    emit,
    run_input: HarnessRunInput,
    loop_result: LoopResult,
    validation_options: PlanValidationOptions,
) -> PlannerFinalizeResult:
    response_text = str(
        loop_result.final_response
        or loop_result.assistant_response
        or ""
    ).strip()
    try:
        payload = parse_plan_json_from_text(response_text)
    except ValueError as exc:
        emit(
            HarnessTraceEventType.PLAN_VALIDATION_FAILED,
            run_input=run_input,
            severity="warning",
            metadata={
                "harness_id": "planner",
                "phase": "plan",
                "parse_error": str(exc),
            },
        )
        loop_result.exit_reason = "plan_validation_failed"
        loop_result.status = "failed"
        loop_result.disposition = "clarify"
        loop_result.final_response = (
            "I could not parse a valid execution plan from the planner output."
        )
        return PlannerFinalizeResult(
            loop_result=loop_result,
            validation=None,
            parse_error=str(exc),
        )

    validation = PlanValidator().validate(payload, options=validation_options)
    plan_payload = validation.plan.to_dict() if validation.plan is not None else payload
    emit(
        HarnessTraceEventType.PLAN_CREATED,
        run_input=run_input,
        metadata={
            "harness_id": "planner",
            "phase": "plan",
            "plan": plan_payload,
            "task_count": len(plan_payload.get("tasks") or []),
        },
    )
    if validation.valid:
        emit(
            HarnessTraceEventType.PLAN_VALIDATION_COMPLETED,
            run_input=run_input,
            metadata={
                "harness_id": "planner",
                "phase": "plan",
                "plan_id": validation.plan.plan_id if validation.plan else None,
                "task_count": len(validation.plan.tasks) if validation.plan else 0,
            },
        )
        summary = _planner_success_reply(validation.plan)
        loop_result.final_response = summary
        loop_result.disposition = "respond"
        loop_result.exit_reason = "plan_validated"
    else:
        emit(
            HarnessTraceEventType.PLAN_VALIDATION_FAILED,
            run_input=run_input,
            severity="warning",
            metadata={
                "harness_id": "planner",
                "phase": "plan",
                "issues": [issue.to_dict() for issue in validation.issues],
            },
        )
        loop_result.exit_reason = "plan_validation_failed"
        loop_result.status = "failed"
        loop_result.disposition = "clarify"
        loop_result.final_response = _planner_failure_reply(validation)
    return PlannerFinalizeResult(
        loop_result=loop_result,
        validation=validation,
    )


def _planner_success_reply(plan: PlanSpec | None) -> str:
    if plan is None:
        return "Plan validation succeeded but no plan payload was returned."
    lines = [
        f"Plan `{plan.plan_id}` validated with {len(plan.tasks)} task(s).",
        "",
        f"Goal: {plan.goal}",
        "",
        "Tasks:",
    ]
    for task in plan.tasks:
        tools = ", ".join(task.tool_names)
        lines.append(f"- {task.task_id}: {task.title} (tools: {tools})")
    lines.append("")
    lines.append("Reply /execute to run these tasks with the worker harness.")
    return "\n".join(lines)


def _planner_failure_reply(validation: PlanValidationResult) -> str:
    lines = ["The planner produced an invalid plan:", ""]
    for issue in validation.issues[:5]:
        suffix = f" (task {issue.task_id})" if issue.task_id else ""
        lines.append(f"- [{issue.code}] {issue.message}{suffix}")
    if len(validation.issues) > 5:
        lines.append(f"- ... and {len(validation.issues) - 5} more issue(s)")
    return "\n".join(lines)


__all__ = [
    "PLANNER_AGENT_ROLE",
    "PLANNER_POLICY_PROFILE",
    "PlannerFinalizeResult",
    "build_planner_user_text",
    "finalize_planner_run",
    "planner_run_budget",
    "resolve_planner_validation_options",
]
