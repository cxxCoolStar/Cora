from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Literal

from core.agent.plan_parser import parse_plan_json_from_text
from core.agent.loop import LoopResult
from core.schemas.harness import HarnessRunInput, HarnessTraceEventType, RunBudget
from core.schemas.plan import PlanSpec, TaskSpec


REVIEWER_AGENT_ROLE = "reviewer"
REVIEWER_POLICY_PROFILE = "planner_readonly"

PlanReviewVerdictName = Literal["accept", "retry", "ask_user", "abort"]
VALID_REVIEW_VERDICTS = frozenset({"accept", "retry", "ask_user", "abort"})

HIGH_RISK_PLAN_TOOLS = frozenset(
    {
        "write_file",
        "shell_exec",
        "scheduled_tasks",
        "browser_navigate",
        "browser_click",
        "browser_type",
        "browser_back",
    }
)

DEFAULT_PLAN_REVIEW_MODE = "high_risk_only"


def resolve_plan_review_mode(*, configured: str | None = None) -> str:
    raw = str(configured or os.environ.get("CORA_PLAN_REVIEW_MODE") or DEFAULT_PLAN_REVIEW_MODE).strip().lower()
    if raw in {"off", "disabled", "none"}:
        return "off"
    if raw in {"always", "all"}:
        return "always"
    return "high_risk_only"


def should_review_task(*, task: TaskSpec, review_mode: str) -> bool:
    if task.requires_review:
        return True
    mode = resolve_plan_review_mode(configured=review_mode)
    if mode == "off":
        return False
    if task.uses_parallel_subagents():
        return mode == "always"
    if mode == "always":
        return True
    if mode == "high_risk_only":
        return bool(set(task.tool_names) & HIGH_RISK_PLAN_TOOLS)
    return False


@dataclass(slots=True)
class PlanReviewVerdict:
    verdict: PlanReviewVerdictName
    reason: str
    confidence: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "reason": self.reason,
            "confidence": self.confidence,
        }


@dataclass(slots=True)
class ReviewerFinalizeResult:
    loop_result: LoopResult
    verdict: PlanReviewVerdict | None
    parse_error: str | None = None


def build_reviewer_user_text(
    *,
    plan: PlanSpec,
    task: TaskSpec,
    worker_summary: str,
    worker_run_id: str | None = None,
) -> str:
    tools = ", ".join(task.tool_names) or "(parallel subagents)"
    reviewed_run = f"\nWorker run id: {worker_run_id}" if worker_run_id else ""
    return (
        f"Review the completed worker step for plan `{plan.plan_id}`.\n\n"
        f"Plan goal: {plan.goal}\n"
        f"Task: {task.task_id} — {task.title}\n"
        f"Instruction: {task.instruction}\n"
        f"Tool scope: {tools}{reviewed_run}\n\n"
        f"Worker output summary:\n{worker_summary.strip() or '(empty)'}\n\n"
        "[Reviewer mode] Respond with a single JSON object only (no markdown prose). "
        "Use this shape:\n"
        "{\n"
        '  "verdict": "accept|retry|ask_user|abort",\n'
        '  "reason": "<short rationale>",\n'
        '  "confidence": "high|medium|low"\n'
        "}\n"
        "Guidance:\n"
        "- accept: output matches the task and is safe to continue the plan.\n"
        "- retry: output is incomplete or wrong but a single worker retry may fix it.\n"
        "- ask_user: need human clarification before continuing.\n"
        "- abort: output is unsafe, inconsistent with the plan, or should stop execution.\n"
        "Do NOT call any tools in this reviewer turn — output JSON only."
    )


def reviewer_run_budget(*, max_steps: int | None = 2) -> RunBudget:
    return RunBudget(
        policy_profile=REVIEWER_POLICY_PROFILE,
        max_steps=max_steps,
        max_tool_calls=0,
    )


def parse_review_verdict_from_text(text: str) -> PlanReviewVerdict:
    payload = parse_plan_json_from_text(text)
    verdict = str(payload.get("verdict") or "").strip().lower()
    if verdict not in VALID_REVIEW_VERDICTS:
        raise ValueError(f"Invalid review verdict: {verdict or '(empty)'}")
    reason = str(payload.get("reason") or "").strip()
    if not reason:
        raise ValueError("Review verdict must include a non-empty reason.")
    confidence = str(payload.get("confidence") or "").strip() or None
    return PlanReviewVerdict(verdict=verdict, reason=reason, confidence=confidence)


def finalize_reviewer_run(
    *,
    emit,
    run_input: HarnessRunInput,
    loop_result: LoopResult,
    plan_id: str | None,
    task_id: str | None,
) -> ReviewerFinalizeResult:
    response_text = str(
        loop_result.final_response
        or loop_result.assistant_response
        or ""
    ).strip()
    try:
        verdict = parse_review_verdict_from_text(response_text)
    except ValueError as exc:
        emit(
            HarnessTraceEventType.PLAN_REVIEW_FAILED,
            run_input=run_input,
            severity="warning",
            metadata={
                "harness_id": "reviewer",
                "phase": "review",
                "plan_id": plan_id,
                "task_id": task_id,
                "parse_error": str(exc),
            },
        )
        loop_result.exit_reason = "plan_review_failed"
        loop_result.status = "failed"
        loop_result.disposition = "clarify"
        loop_result.final_response = (
            f"Plan review could not parse a valid verdict.\nParse error: {exc}"
        )
        loop_result.tool_trace = []
        return ReviewerFinalizeResult(
            loop_result=loop_result,
            verdict=None,
            parse_error=str(exc),
        )

    emit(
        HarnessTraceEventType.PLAN_REVIEW_COMPLETED,
        run_input=run_input,
        metadata={
            "harness_id": "reviewer",
            "phase": "review",
            "plan_id": plan_id,
            "task_id": task_id,
            "verdict": verdict.verdict,
            "confidence": verdict.confidence,
        },
    )
    loop_result.exit_reason = "plan_review_completed"
    loop_result.status = "completed"
    loop_result.disposition = "respond"
    loop_result.final_response = _reviewer_reply(verdict)
    loop_result.tool_trace = []
    run_input.metadata["review_verdict"] = verdict.to_dict()
    return ReviewerFinalizeResult(
        loop_result=loop_result,
        verdict=verdict,
    )


def _reviewer_reply(verdict: PlanReviewVerdict) -> str:
    return (
        f"Plan review verdict: {verdict.verdict} ({verdict.confidence or 'unspecified'} confidence).\n"
        f"Reason: {verdict.reason}"
    )


def verdict_from_turn_reply(reply: str) -> PlanReviewVerdict | None:
    text = str(reply or "").strip()
    if not text:
        return None
    match = re.search(
        r"Plan review verdict:\s*(accept|retry|ask_user|abort)",
        text,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    verdict_name = match.group(1).lower()
    reason_match = re.search(r"Reason:\s*(.+?)(?:\n\n|\Z)", text, flags=re.DOTALL)
    reason = reason_match.group(1).strip() if reason_match else "No reason provided."
    confidence_match = re.search(r"\((high|medium|low)\s+confidence\)", text, flags=re.IGNORECASE)
    confidence = confidence_match.group(1).lower() if confidence_match else None
    return PlanReviewVerdict(verdict=verdict_name, reason=reason, confidence=confidence)


__all__ = [
    "DEFAULT_PLAN_REVIEW_MODE",
    "HIGH_RISK_PLAN_TOOLS",
    "REVIEWER_AGENT_ROLE",
    "REVIEWER_POLICY_PROFILE",
    "PlanReviewVerdict",
    "ReviewerFinalizeResult",
    "build_reviewer_user_text",
    "finalize_reviewer_run",
    "parse_review_verdict_from_text",
    "resolve_plan_review_mode",
    "reviewer_run_budget",
    "should_review_task",
    "verdict_from_turn_reply",
]
