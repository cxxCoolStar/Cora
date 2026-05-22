from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from core.schemas.harness import RunBudget
from core.schemas.plan import PlanRunSpec, PlanSpec, TaskResultSpec, TaskSpec

if TYPE_CHECKING:
    from core.agent.run_records import AgentRunRecordRepository
    from core.agent.turn_runner import AgentTurnResult, AgentTurnRunner
    from core.agent.runtime_state import RuntimeContextSnapshot


WORKER_AGENT_ROLE = "worker"
DEFAULT_WORKER_POLICY_PROFILE = "coding_full"


def build_worker_user_text(*, task: TaskSpec) -> str:
    tools = ", ".join(task.tool_names)
    return (
        f"{task.instruction.strip()}\n\n"
        f"[Worker task {task.task_id}] {task.title}\n"
        f"Tool scope: {tools}"
    )


def worker_run_budget(*, plan: PlanSpec, task: TaskSpec) -> RunBudget:
    profile = str(plan.policy_profile or "").strip() or DEFAULT_WORKER_POLICY_PROFILE
    return RunBudget(
        policy_profile=profile,
        allowed_tool_names=list(task.tool_names),
        max_steps=8,
        max_tool_calls=8,
    )


@dataclass(slots=True)
class PlanExecutionResult:
    plan_run: PlanRunSpec
    reply: str
    status: str
    disposition: str
    waiting_hitl: bool = False
    pending_hitl_id: str | None = None
    paused_task_index: int | None = None
    tool_trace: list[dict[str, Any]] = field(default_factory=list)


class PlanExecutor:
    def __init__(
        self,
        *,
        turn_runner: AgentTurnRunner,
        run_record_repository: AgentRunRecordRepository | None = None,
    ) -> None:
        self._turn_runner = turn_runner
        self._run_record_repository = run_record_repository

    async def execute(
        self,
        *,
        session_id: str,
        plan: PlanSpec,
        planner_run_id: str,
        source_message_id: str,
        context_snapshot: RuntimeContextSnapshot,
        run_metadata: dict[str, Any] | None = None,
        start_task_index: int = 0,
        initial_task_results: list[TaskResultSpec] | None = None,
        initial_tool_trace: list[dict[str, Any]] | None = None,
    ) -> PlanExecutionResult:
        plan_run = PlanRunSpec(plan_id=plan.plan_id, status="executing")
        metadata_base = dict(run_metadata or {})
        metadata_base.setdefault("plan_id", plan.plan_id)
        aggregated_tool_trace: list[dict[str, Any]] = list(initial_tool_trace or [])
        if initial_task_results:
            plan_run.task_results.extend(initial_task_results)

        tasks = list(plan.tasks)
        for index in range(max(0, int(start_task_index)), len(tasks)):
            task = tasks[index]
            task_result, task_tool_trace = await self._run_task(
                session_id=session_id,
                plan=plan,
                task=task,
                planner_run_id=planner_run_id,
                source_message_id=source_message_id,
                context_snapshot=context_snapshot,
                metadata_base=metadata_base,
            )
            plan_run.task_results.append(task_result)
            aggregated_tool_trace.extend(task_tool_trace)
            if task_result.status == "failed":
                plan_run.status = "failed"
                return PlanExecutionResult(
                    plan_run=plan_run,
                    reply=format_plan_execution_reply(plan=plan, plan_run=plan_run),
                    status="failed",
                    disposition="respond",
                    tool_trace=aggregated_tool_trace,
                )
            if task_result.status == "pending":
                plan_run.status = "waiting_hitl"
                return PlanExecutionResult(
                    plan_run=plan_run,
                    reply=format_plan_execution_reply(plan=plan, plan_run=plan_run),
                    status="failed",
                    disposition="clarify",
                    waiting_hitl=True,
                    paused_task_index=index,
                    tool_trace=aggregated_tool_trace,
                )

        plan_run.status = "completed"
        return PlanExecutionResult(
            plan_run=plan_run,
            reply=format_plan_execution_reply(plan=plan, plan_run=plan_run),
            status="completed",
            disposition="respond",
            tool_trace=aggregated_tool_trace,
        )

    async def resume_task_after_hitl(
        self,
        *,
        session_id: str,
        plan: PlanSpec,
        task: TaskSpec,
        planner_run_id: str,
        source_message_id: str,
        context_snapshot: RuntimeContextSnapshot,
        run_metadata: dict[str, Any],
        hitl_id: str,
        approved_tool_name: str,
    ) -> tuple[TaskResultSpec, list[dict[str, Any]], AgentTurnResult]:
        metadata = {
            **dict(run_metadata or {}),
            "agent_role": WORKER_AGENT_ROLE,
            "task_id": task.task_id,
            "parent_run_id": planner_run_id,
            "resume_hitl_id": hitl_id,
            "plan_id": plan.plan_id,
        }
        resume_text = str(run_metadata.get("resume_text") or "").strip()
        if not resume_text:
            resume_text = f"/tool {approved_tool_name} {{}}"
        budget = worker_run_budget(plan=plan, task=task)
        approved_names = list(budget.approved_tool_names)
        if approved_tool_name not in approved_names:
            approved_names.append(approved_tool_name)
        budget = RunBudget(
            policy_profile=budget.policy_profile,
            allowed_tool_names=approved_names,
            max_steps=budget.max_steps,
            max_tool_calls=budget.max_tool_calls,
            approved_tool_names=[approved_tool_name],
        )
        turn_result = await self._turn_runner.run_turn(
            session_id=session_id,
            source_message_id=f"{source_message_id}:{task.task_id}:resume",
            user_text=resume_text,
            raw_text=resume_text,
            upload=None,
            context_snapshot=context_snapshot,
            run_budget=budget,
            run_metadata=metadata,
        )
        run_id = self._resolve_worker_run_id(session_id=session_id, task_id=task.task_id)
        trace_entries = _tool_trace_entries(turn_result)
        if _task_waiting_hitl(turn_result) or _task_failed(turn_result):
            return (
                TaskResultSpec(
                    task_id=task.task_id,
                    run_id=run_id or "",
                    status="failed",
                    summary=_task_summary_from_turn(turn_result),
                    tool_trace_count=len(turn_result.tool_trace),
                ),
                trace_entries,
                turn_result,
            )
        return (
            TaskResultSpec(
                task_id=task.task_id,
                run_id=run_id or "",
                status="completed",
                summary=_task_summary_from_turn(turn_result),
                tool_trace_count=len(turn_result.tool_trace),
            ),
            trace_entries,
            turn_result,
        )

    async def _run_task(
        self,
        *,
        session_id: str,
        plan: PlanSpec,
        task: TaskSpec,
        planner_run_id: str,
        source_message_id: str,
        context_snapshot: RuntimeContextSnapshot,
        metadata_base: dict[str, Any],
    ) -> tuple[TaskResultSpec, list[dict[str, Any]]]:
        task_metadata = {
            **metadata_base,
            "agent_role": WORKER_AGENT_ROLE,
            "task_id": task.task_id,
            "parent_run_id": planner_run_id,
        }
        turn_result = await self._turn_runner.run_turn(
            session_id=session_id,
            source_message_id=f"{source_message_id}:{task.task_id}",
            user_text=build_worker_user_text(task=task),
            raw_text=build_worker_user_text(task=task),
            upload=None,
            context_snapshot=context_snapshot,
            run_budget=worker_run_budget(plan=plan, task=task),
            run_metadata=task_metadata,
        )
        run_id = self._resolve_worker_run_id(session_id=session_id, task_id=task.task_id)
        trace_entries = _tool_trace_entries(turn_result)
        if _task_waiting_hitl(turn_result):
            return (
                TaskResultSpec(
                    task_id=task.task_id,
                    run_id=run_id or "",
                    status="pending",
                    summary=_task_summary_from_turn(turn_result),
                    tool_trace_count=len(turn_result.tool_trace),
                ),
                trace_entries,
            )
        if _task_failed(turn_result):
            return (
                TaskResultSpec(
                    task_id=task.task_id,
                    run_id=run_id or "",
                    status="failed",
                    summary=_task_summary_from_turn(turn_result),
                    tool_trace_count=len(turn_result.tool_trace),
                ),
                trace_entries,
            )
        return (
            TaskResultSpec(
                task_id=task.task_id,
                run_id=run_id or "",
                status="completed",
                summary=_task_summary_from_turn(turn_result),
                tool_trace_count=len(turn_result.tool_trace),
            ),
            trace_entries,
        )

    def _resolve_worker_run_id(self, *, session_id: str, task_id: str) -> str:
        if self._run_record_repository is None:
            return ""
        for record in self._run_record_repository.list_by_session(session_id=session_id):
            if record.agent_role != WORKER_AGENT_ROLE:
                continue
            if str(record.input_metadata.get("task_id") or "").strip() == task_id:
                return record.run_id
        return ""


def _task_failed(turn_result: AgentTurnResult) -> bool:
    if str(turn_result.status or "").strip() == "failed":
        return True
    return any(trace.action == "policy_denied" for trace in turn_result.tool_trace)


def _task_waiting_hitl(turn_result: AgentTurnResult) -> bool:
    return any(trace.action == "policy_ask" for trace in turn_result.tool_trace)


def _tool_trace_entries(turn_result: AgentTurnResult) -> list[dict[str, Any]]:
    return [
        {
            "tool_name": entry.tool_name,
            "arguments": dict(entry.arguments),
            "action": entry.action,
            "status": entry.status,
            "disposition": entry.disposition,
            "metadata": dict(entry.metadata or {}),
        }
        for entry in turn_result.tool_trace
    ]


def _task_summary_from_turn(turn_result: AgentTurnResult) -> str:
    reply = str(turn_result.reply or "").strip()
    if reply:
        return reply[:500]
    if turn_result.tool_trace:
        last = turn_result.tool_trace[-1]
        content = str(last.content or "").strip()
        if content:
            return content[:500]
        return f"{last.tool_name} ({last.action}, {last.status})"
    return "No worker output."


def format_plan_execution_reply(*, plan: PlanSpec, plan_run: PlanRunSpec) -> str:
    status_label = {
        "completed": "completed successfully",
        "failed": "failed",
        "waiting_hitl": "paused for human approval",
        "executing": "is in progress",
    }.get(plan_run.status, plan_run.status)
    lines = [
        f"Plan `{plan.plan_id}` execution {status_label}.",
        "",
        f"Goal: {plan.goal}",
        "",
        "Task results:",
    ]
    for result in plan_run.task_results:
        lines.append(f"- {result.task_id} [{result.status}]: {result.summary}")
    if plan_run.status == "waiting_hitl":
        lines.append("")
        lines.append("Reply 确认 to approve the pending tool and continue this plan, or 拒绝 to cancel.")
    return "\n".join(lines)


__all__ = [
    "PlanExecutionResult",
    "PlanExecutor",
    "WORKER_AGENT_ROLE",
    "build_worker_user_text",
    "format_plan_execution_reply",
    "worker_run_budget",
]
