from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from core.agent.plan_reviewer import PlanReviewVerdict, should_review_task
from core.agent.retry_policy import ErrorCategory, calculate_backoff_delay, classify_error, log_retry_event
from core.schemas.harness import RunBudget
from core.schemas.plan import PlanRunSpec, PlanSpec, TaskResultSpec, TaskSpec

if TYPE_CHECKING:
    from core.agent.run_records import AgentRunRecordRepository
    from core.agent.turn_runner import AgentTurnResult, AgentTurnRunner
    from core.agent.runtime_state import RuntimeContextSnapshot
    from core.schemas.subagent import SpawnWorkersResult


WORKER_AGENT_ROLE = "worker"
DEFAULT_WORKER_POLICY_PROFILE = "coding_full"


def build_worker_user_text(*, task: TaskSpec, plan_resume: bool = False) -> str:
    tools = ", ".join(task.tool_names)
    lines = [
        task.instruction.strip(),
        "",
        f"[Worker task {task.task_id}] {task.title}",
        f"Tool scope: {tools}",
    ]
    if plan_resume:
        lines.append("[Plan resume]")
    return "\n".join(lines)


def worker_run_budget(*, plan: PlanSpec, task: TaskSpec) -> RunBudget:
    profile = str(plan.policy_profile or "").strip() or DEFAULT_WORKER_POLICY_PROFILE
    return RunBudget(
        policy_profile=profile,
        allowed_tool_names=list(task.tool_names),
        max_steps=8,
        max_tool_calls=8,
    )


def plan_subagent_run_budget(*, plan: PlanSpec, task: TaskSpec) -> RunBudget:
    profile = str(plan.policy_profile or "").strip() or DEFAULT_WORKER_POLICY_PROFILE
    allowed_tools: list[str] = []
    for subtask in task.parallel_subagents:
        for tool_name in subtask.tool_names:
            if tool_name not in allowed_tools:
                allowed_tools.append(tool_name)
    return RunBudget(
        policy_profile=profile,
        allowed_tool_names=allowed_tools,
        max_steps=8,
        max_tool_calls=8,
        max_spawn_depth=1,
        max_child_runs=max(len(task.parallel_subagents), 1),
    )


SpawnWorkersForPlan = Callable[..., Awaitable["SpawnWorkersResult"]]
ReviewPlanTask = Callable[..., Awaitable["PlanReviewVerdict | None"]]


@dataclass(slots=True)
class PlanExecutionResult:
    plan_run: PlanRunSpec
    reply: str
    status: str
    disposition: str
    waiting_hitl: bool = False
    pending_hitl_id: str | None = None
    paused_task_index: int | None = None
    pause_reason: str | None = None
    tool_trace: list[dict[str, Any]] = field(default_factory=list)
    total_retry_count: int = 0
    """Total number of retries across all tasks in this plan execution"""
    execution_start_time: float | None = None
    """Timestamp when plan execution started (seconds since epoch)"""
    execution_end_time: float | None = None
    """Timestamp when plan execution ended (seconds since epoch)"""


class PlanExecutor:
    def __init__(
        self,
        *,
        turn_runner: AgentTurnRunner,
        run_record_repository: AgentRunRecordRepository | None = None,
        spawn_workers_for_plan: SpawnWorkersForPlan | None = None,
        review_plan_task: ReviewPlanTask | None = None,
        plan_review_mode: str | None = None,
    ) -> None:
        self._turn_runner = turn_runner
        self._run_record_repository = run_record_repository
        self._spawn_workers_for_plan = spawn_workers_for_plan
        self._review_plan_task = review_plan_task
        self._plan_review_mode = plan_review_mode

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
        execution_start_time = time.time()
        
        plan_run = PlanRunSpec(plan_id=plan.plan_id, status="executing")
        metadata_base = dict(run_metadata or {})
        metadata_base.setdefault("plan_id", plan.plan_id)
        aggregated_tool_trace: list[dict[str, Any]] = list(initial_tool_trace or [])
        if initial_task_results:
            plan_run.task_results.extend(initial_task_results)

        # Retry configuration
        total_retry_count = 0
        max_total_retries = 10
        
        tasks = list(plan.tasks)
        for index in range(max(0, int(start_task_index)), len(tasks)):
            task = tasks[index]
            
            # Check global retry limit
            if total_retry_count >= max_total_retries:
                plan_run.status = "failed"
                return PlanExecutionResult(
                    plan_run=plan_run,
                    reply=format_plan_execution_reply(
                        plan=plan,
                        plan_run=plan_run,
                        pause_reason="retry_limit_exceeded",
                        paused_task_index=index,
                    ) + f"\n\nPlan total retry limit exceeded ({max_total_retries} retries).",
                    status="failed",
                    disposition="respond",
                    paused_task_index=index,
                    pause_reason="retry_limit_exceeded",
                    tool_trace=aggregated_tool_trace,
                    total_retry_count=total_retry_count,
                    execution_start_time=execution_start_time,
                    execution_end_time=time.time(),
                )
            
            task_result, task_tool_trace = await self._run_task_with_retry(
                session_id=session_id,
                plan=plan,
                task=task,
                planner_run_id=planner_run_id,
                source_message_id=source_message_id,
                context_snapshot=context_snapshot,
                metadata_base=metadata_base,
                total_retry_count=total_retry_count,
                max_total_retries=max_total_retries,
            )
            
            # Update total retry count
            total_retry_count += task_result.retry_count
            
            plan_run.task_results.append(task_result)
            aggregated_tool_trace.extend(task_tool_trace)
            
            if task_result.status == "failed":
                plan_run.status = "failed"
                return PlanExecutionResult(
                    plan_run=plan_run,
                    reply=format_plan_execution_reply(
                        plan=plan,
                        plan_run=plan_run,
                        pause_reason="failed",
                        paused_task_index=index,
                    ),
                    status="failed",
                    disposition="respond",
                    paused_task_index=index,
                    pause_reason="failed",
                    tool_trace=aggregated_tool_trace,
                    total_retry_count=total_retry_count,
                    execution_start_time=execution_start_time,
                    execution_end_time=time.time(),
                )
            if task_result.status == "pending":
                plan_run.status = "waiting_hitl"
                return PlanExecutionResult(
                    plan_run=plan_run,
                    reply=format_plan_execution_reply(
                        plan=plan,
                        plan_run=plan_run,
                        pause_reason="hitl",
                        paused_task_index=index,
                    ),
                    status="failed",
                    disposition="clarify",
                    waiting_hitl=True,
                    paused_task_index=index,
                    pause_reason="hitl",
                    tool_trace=aggregated_tool_trace,
                    total_retry_count=total_retry_count,
                    execution_start_time=execution_start_time,
                    execution_end_time=time.time(),
                )

        plan_run.status = "completed"
        return PlanExecutionResult(
            plan_run=plan_run,
            reply=format_plan_execution_reply(plan=plan, plan_run=plan_run),
            status="completed",
            disposition="respond",
            tool_trace=aggregated_tool_trace,
            total_retry_count=total_retry_count,
            execution_start_time=execution_start_time,
            execution_end_time=time.time(),
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
        completed_operations = _extract_completed_operations(turn_result)
        
        if _task_waiting_hitl(turn_result) or _task_failed(turn_result):
            return (
                TaskResultSpec(
                    task_id=task.task_id,
                    run_id=run_id or "",
                    status="failed",
                    summary=_task_summary_from_turn(turn_result),
                    tool_trace_count=len(turn_result.tool_trace),
                    completed_operations=completed_operations,
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
                completed_operations=completed_operations,
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
        if task.uses_parallel_subagents():
            return await self._run_parallel_subagent_task(
                session_id=session_id,
                plan=plan,
                task=task,
                planner_run_id=planner_run_id,
                source_message_id=source_message_id,
                metadata_base=metadata_base,
            )
        task_result, trace_entries = await self._run_worker_turn(
            session_id=session_id,
            plan=plan,
            task=task,
            planner_run_id=planner_run_id,
            source_message_id=f"{source_message_id}:{task.task_id}",
            context_snapshot=context_snapshot,
            metadata_base=metadata_base,
        )
        if task_result.status != "completed":
            return task_result, trace_entries
        reviewed = await self._apply_task_review(
            session_id=session_id,
            plan=plan,
            task=task,
            task_result=task_result,
            planner_run_id=planner_run_id,
            source_message_id=source_message_id,
            context_snapshot=context_snapshot,
            metadata_base=metadata_base,
        )
        if reviewed is not None:
            return reviewed, trace_entries
        return task_result, trace_entries

    async def _run_task_with_retry(
        self,
        *,
        session_id: str,
        plan: PlanSpec,
        task: TaskSpec,
        planner_run_id: str,
        source_message_id: str,
        context_snapshot: RuntimeContextSnapshot,
        metadata_base: dict[str, Any],
        total_retry_count: int,
        max_total_retries: int,
    ) -> tuple[TaskResultSpec, list[dict[str, Any]]]:
        """Run a task with automatic retry on transient errors."""
        max_retries = 3
        retry_timeout = 300.0  # 5 minutes
        retry_start_time = time.time()
        retry_count = 0
        aggregated_trace: list[dict[str, Any]] = []
        last_task_result: TaskResultSpec | None = None
        
        while retry_count <= max_retries:
            # Check retry timeout
            if time.time() - retry_start_time > retry_timeout:
                if last_task_result is not None:
                    last_task_result.retry_count = retry_count
                    last_task_result.last_error = f"Task retry timeout exceeded ({retry_timeout}s)"
                    last_task_result.error_category = ErrorCategory.TIMEOUT.value
                    last_task_result.retryable = False
                    return last_task_result, aggregated_trace
                return (
                    TaskResultSpec(
                        task_id=task.task_id,
                        run_id="",
                        status="failed",
                        summary=f"Task retry timeout exceeded ({retry_timeout}s)",
                        retry_count=retry_count,
                        last_error=f"Task retry timeout exceeded ({retry_timeout}s)",
                        error_category=ErrorCategory.TIMEOUT.value,
                        retryable=False,
                    ),
                    aggregated_trace,
                )
            
            # Check global retry limit
            if total_retry_count + retry_count >= max_total_retries:
                if last_task_result is not None:
                    last_task_result.retry_count = retry_count
                    last_task_result.last_error = f"Plan total retry limit exceeded ({max_total_retries})"
                    last_task_result.error_category = ErrorCategory.UNKNOWN.value
                    last_task_result.retryable = False
                    return last_task_result, aggregated_trace
                return (
                    TaskResultSpec(
                        task_id=task.task_id,
                        run_id="",
                        status="failed",
                        summary=f"Plan total retry limit exceeded ({max_total_retries})",
                        retry_count=retry_count,
                        last_error=f"Plan total retry limit exceeded ({max_total_retries})",
                        error_category=ErrorCategory.UNKNOWN.value,
                        retryable=False,
                    ),
                    aggregated_trace,
                )
            
            # Execute task
            try:
                task_result, trace_entries = await self._run_task(
                    session_id=session_id,
                    plan=plan,
                    task=task,
                    planner_run_id=planner_run_id,
                    source_message_id=source_message_id,
                    context_snapshot=context_snapshot,
                    metadata_base=metadata_base,
                )
                aggregated_trace.extend(trace_entries)
                
                # Success: return result
                if task_result.status == "completed":
                    task_result.retry_count = retry_count
                    return task_result, aggregated_trace
                
                # HITL pending: return immediately (not retryable)
                if task_result.status == "pending":
                    task_result.retry_count = retry_count
                    return task_result, aggregated_trace
                
                # Failed: classify error and decide whether to retry
                error_category, retryable = classify_error(
                    error=task_result.summary,
                    tool_name=None,
                    status_code=None,
                )
                
                task_result.error_category = error_category.value
                task_result.retryable = retryable
                task_result.last_error = task_result.summary
                last_task_result = task_result
                
                # Not retryable or reached max retries: fail
                if not retryable or retry_count >= max_retries:
                    task_result.retry_count = retry_count
                    return task_result, aggregated_trace
                
                # Retryable: backoff and retry
                retry_count += 1
                delay = calculate_backoff_delay(attempt=retry_count - 1)
                
                # Log retry event
                retry_event = log_retry_event(
                    task_id=task.task_id,
                    retry_count=retry_count,
                    delay=delay,
                    error_category=error_category,
                    error_message=task_result.summary,
                )
                aggregated_trace.append(retry_event)
                
                # Wait for backoff delay
                await asyncio.sleep(delay)
                
            except Exception as exc:
                # Uncaught exception: classify and decide whether to retry
                error_category, retryable = classify_error(error=exc)
                
                last_task_result = TaskResultSpec(
                    task_id=task.task_id,
                    run_id="",
                    status="failed",
                    summary=str(exc),
                    retry_count=retry_count,
                    last_error=str(exc),
                    error_category=error_category.value,
                    retryable=retryable,
                )
                
                # Not retryable or reached max retries: fail
                if not retryable or retry_count >= max_retries:
                    return last_task_result, aggregated_trace
                
                # Retryable: backoff and retry
                retry_count += 1
                delay = calculate_backoff_delay(attempt=retry_count - 1)
                
                # Log retry event
                retry_event = log_retry_event(
                    task_id=task.task_id,
                    retry_count=retry_count,
                    delay=delay,
                    error_category=error_category,
                    error_message=str(exc),
                )
                aggregated_trace.append(retry_event)
                
                # Wait for backoff delay
                await asyncio.sleep(delay)
        
        # Should not reach here, but return last result if we do
        if last_task_result is not None:
            last_task_result.retry_count = retry_count
            return last_task_result, aggregated_trace
        
        return (
            TaskResultSpec(
                task_id=task.task_id,
                run_id="",
                status="failed",
                summary="Task failed after maximum retries",
                retry_count=retry_count,
                last_error="Task failed after maximum retries",
                error_category=ErrorCategory.UNKNOWN.value,
                retryable=False,
            ),
            aggregated_trace,
        )

    async def _apply_task_review(
        self,
        *,
        session_id: str,
        plan: PlanSpec,
        task: TaskSpec,
        task_result: TaskResultSpec,
        planner_run_id: str,
        source_message_id: str,
        context_snapshot: RuntimeContextSnapshot,
        metadata_base: dict[str, Any],
    ) -> TaskResultSpec | None:
        if task_result.status != "completed":
            return None
        if self._review_plan_task is None:
            return None
        if not should_review_task(task=task, review_mode=self._plan_review_mode or ""):
            return None
        verdict = await self._review_plan_task(
            session_id=session_id,
            plan=plan,
            task=task,
            task_result=task_result,
            planner_run_id=planner_run_id,
            source_message_id=source_message_id,
            context_snapshot=context_snapshot,
            metadata_base=metadata_base,
        )
        if verdict is None:
            return None
        if verdict.verdict == "accept":
            return None
        if verdict.verdict == "retry":
            retry_result, _ = await self._run_worker_turn(
                session_id=session_id,
                plan=plan,
                task=task,
                planner_run_id=planner_run_id,
                source_message_id=f"{source_message_id}:{task.task_id}:retry",
                context_snapshot=context_snapshot,
                metadata_base=metadata_base,
            )
            if retry_result.status == "completed":
                return None
            return retry_result
        summary = f"Plan review {verdict.verdict}: {verdict.reason}"
        return TaskResultSpec(
            task_id=task.task_id,
            run_id=task_result.run_id,
            status="failed",
            summary=summary,
            tool_trace_count=task_result.tool_trace_count,
        )

    async def _run_worker_turn(
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
        plan_resume = bool(metadata_base.get("plan_resume"))
        
        # Inject completed operations for idempotency checking on resume
        completed_ops: list[str] = []
        if plan_resume and "completed_operations" in metadata_base:
            completed_ops = list(metadata_base["completed_operations"])
        
        task_metadata = {
            **metadata_base,
            "agent_role": WORKER_AGENT_ROLE,
            "task_id": task.task_id,
            "parent_run_id": planner_run_id,
            "completed_operations": completed_ops,
        }
        worker_text = build_worker_user_text(task=task, plan_resume=plan_resume)
        turn_result = await self._turn_runner.run_turn(
            session_id=session_id,
            source_message_id=source_message_id,
            user_text=worker_text,
            raw_text=worker_text,
            upload=None,
            context_snapshot=context_snapshot,
            run_budget=worker_run_budget(plan=plan, task=task),
            run_metadata=task_metadata,
        )
        run_id = self._resolve_worker_run_id(session_id=session_id, task_id=task.task_id)
        trace_entries = _tool_trace_entries(turn_result)
        
        # Extract completed operations from tool trace
        completed_operations = _extract_completed_operations(turn_result)
        
        if _task_waiting_hitl(turn_result):
            return (
                TaskResultSpec(
                    task_id=task.task_id,
                    run_id=run_id or "",
                    status="pending",
                    summary=_task_summary_from_turn(turn_result),
                    tool_trace_count=len(turn_result.tool_trace),
                    completed_operations=completed_operations,
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
                    completed_operations=completed_operations,
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
                completed_operations=completed_operations,
            ),
            trace_entries,
        )

    async def _run_parallel_subagent_task(
        self,
        *,
        session_id: str,
        plan: PlanSpec,
        task: TaskSpec,
        planner_run_id: str,
        source_message_id: str,
        metadata_base: dict[str, Any],
    ) -> tuple[TaskResultSpec, list[dict[str, Any]]]:
        if self._spawn_workers_for_plan is None:
            return (
                TaskResultSpec(
                    task_id=task.task_id,
                    run_id=planner_run_id,
                    status="failed",
                    summary="Parallel subagent execution is not configured for plan runs.",
                    tool_trace_count=0,
                ),
                [],
            )
        task_metadata = {
            **metadata_base,
            "agent_role": "plan_subagent",
            "task_id": task.task_id,
            "parent_run_id": planner_run_id,
            "plan_id": plan.plan_id,
        }
        spawn_result = await self._spawn_workers_for_plan(
            session_id=session_id,
            source_message_id=f"{source_message_id}:{task.task_id}",
            planner_run_id=planner_run_id,
            plan=plan,
            task=task,
            run_metadata=task_metadata,
        )
        trace_entries = [
            {
                "tool_name": "spawn_workers",
                "arguments": {
                    "task_count": len(task.parallel_subagents),
                    "task_id": task.task_id,
                },
                "action": "plan_subagent",
                "status": spawn_result.status,
                "disposition": spawn_result.disposition,
                "metadata": {
                    "parent_run_id": spawn_result.parent_run_id,
                    "denied": spawn_result.denied,
                    "denial_reason": spawn_result.denial_reason,
                },
            }
        ]
        child_tool_count = sum(
            int(getattr(item.child_result, "tool_trace_count", 0) or 0)
            for item in spawn_result.results
            if item.child_result is not None
        )
        if spawn_result.denied or spawn_result.status != "completed":
            return (
                TaskResultSpec(
                    task_id=task.task_id,
                    run_id=spawn_result.parent_run_id or planner_run_id,
                    status="failed",
                    summary=str(spawn_result.reply or "").strip() or "Parallel subagent task failed.",
                    tool_trace_count=child_tool_count,
                ),
                trace_entries,
            )
        return (
            TaskResultSpec(
                task_id=task.task_id,
                run_id=spawn_result.parent_run_id or planner_run_id,
                status="completed",
                summary=str(spawn_result.reply or "").strip() or task.instruction,
                tool_trace_count=child_tool_count,
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


def _extract_completed_operations(turn_result: AgentTurnResult) -> list[str]:
    """Extract idempotency keys from tool trace metadata."""
    completed_ops: list[str] = []
    for entry in turn_result.tool_trace:
        metadata = entry.metadata or {}
        idempotency_key = metadata.get("idempotency_key")
        if idempotency_key and isinstance(idempotency_key, str):
            if idempotency_key not in completed_ops:
                completed_ops.append(idempotency_key)
    return completed_ops


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


def format_plan_execution_reply(
    *,
    plan: PlanSpec,
    plan_run: PlanRunSpec,
    pause_reason: str | None = None,
    paused_task_index: int | None = None,
) -> str:
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
    elif plan_run.status == "failed" and str(pause_reason or "").strip().lower() == "failed":
        failed_task = ""
        if paused_task_index is not None and 0 <= int(paused_task_index) < len(plan.tasks):
            failed_task = plan.tasks[int(paused_task_index)].task_id
        lines.append("")
        if failed_task:
            lines.append(
                f"Execution stopped at `{failed_task}`. "
                "Reply /execute resume to retry from the failed task, "
                "or /execute restart to run the full plan again."
            )
        else:
            lines.append(
                "Execution stopped before completion. "
                "Reply /execute resume to continue, or /execute restart to run the full plan again."
            )
    return "\n".join(lines)


__all__ = [
    "PlanExecutionResult",
    "PlanExecutor",
    "SpawnWorkersForPlan",
    "WORKER_AGENT_ROLE",
    "build_worker_user_text",
    "format_plan_execution_reply",
    "plan_subagent_run_budget",
    "worker_run_budget",
]
