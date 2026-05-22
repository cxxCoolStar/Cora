from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from core.agent.harness import new_run_input
from core.agent.run_records import AgentRunRecord
from core.agent.runtime_state import RuntimeContextSnapshot
from core.agent.spawn_depth import check_spawn_depth_allowed, effective_max_spawn_depth
from core.agent.subagent_context import normalize_context_mode
from core.agent.subagent_policy import (
    parent_effective_allow_set,
    resolve_child_allowed_tool_names,
    spawn_policy_denied_message,
)
from core.schemas.harness import HarnessTraceEventType, RunBudget, RunTraceEvent
from core.schemas.subagent import (
    DEFAULT_SUBAGENT_POLICY_PROFILE,
    SPAWN_ORCHESTRATOR_AGENT_ROLE,
    SUBAGENT_SESSION_KIND,
    SUBAGENT_WORKER_ROLE,
    SpawnWorkerRequest,
    SpawnWorkerResult,
    SpawnWorkerTaskSpec,
    SpawnWorkersResult,
    SubagentResultSpec,
    build_subagent_user_text,
    format_spawn_workers_reply,
    subagent_result_from_turn,
)

if TYPE_CHECKING:
    from core.agent.run_records import AgentRunRecordRepository
    from core.agent.turn_runner import AgentTurnRunner
    from core.storage.repositories import SessionRepository


def effective_max_child_runs(*, budget: RunBudget, default: int) -> int:
    if budget.max_child_runs is not None:
        return max(0, int(budget.max_child_runs))
    return max(0, int(default))


def child_run_limit_denied_message(*, child_count: int, max_child_runs: int) -> str:
    return (
        f"Subagent child run limit exceeded (children={int(child_count)}, max={int(max_child_runs)})."
    )


class SubagentSpawner:
    def __init__(
        self,
        *,
        turn_runner: AgentTurnRunner,
        session_repository: SessionRepository,
        run_record_repository: AgentRunRecordRepository | None,
        harness_id: str,
        default_max_spawn_depth: int = 1,
        default_max_child_runs: int = 4,
        default_max_parallel_spawns: int = 3,
    ) -> None:
        self._turn_runner = turn_runner
        self._session_repository = session_repository
        self._run_record_repository = run_record_repository
        self._harness_id = harness_id
        self._default_max_spawn_depth = max(0, int(default_max_spawn_depth))
        self._default_max_child_runs = max(0, int(default_max_child_runs))
        self._default_max_parallel_spawns = max(1, int(default_max_parallel_spawns))

    async def spawn_worker(
        self,
        *,
        request: SpawnWorkerRequest,
        parent_context_snapshot: RuntimeContextSnapshot,
        run_budget: RunBudget | None = None,
        registered_tool_names: list[str] | None = None,
    ) -> SpawnWorkerResult:
        budget = run_budget or RunBudget()
        parent_budget = request.parent_budget or budget
        context_mode = normalize_context_mode(request.context_mode)
        parent_run_id = self._resolve_parent_run_id(
            parent_session_id=request.parent_session_id,
            explicit_parent_run_id=request.parent_run_id,
        )
        max_child_runs = effective_max_child_runs(
            budget=budget,
            default=self._default_max_child_runs,
        )
        if request.parent_max_child_runs is not None:
            max_child_runs = max(0, int(request.parent_max_child_runs))
        child_count = self._count_child_runs(parent_run_id=parent_run_id)
        if child_count >= max_child_runs:
            message = child_run_limit_denied_message(
                child_count=child_count,
                max_child_runs=max_child_runs,
            )
            denial_trace = self._record_spawn_denial(
                request=request,
                parent_run_id=parent_run_id,
                message=message,
                denial_reason="child_run_limit",
            )
            return SpawnWorkerResult(
                parent_run_id=parent_run_id,
                child_session_id="",
                child_run_id=None,
                reply=message,
                status="failed",
                disposition="error",
                parent_trace_events=denial_trace,
                denied=True,
                denial_reason="child_run_limit",
            )

        child_spawn_depth = int(request.parent_spawn_depth) + 1
        max_spawn_depth = effective_max_spawn_depth(
            budget=budget,
            default=self._default_max_spawn_depth,
        )
        if request.parent_max_spawn_depth is not None:
            max_spawn_depth = max(0, int(request.parent_max_spawn_depth))
        depth_denial = check_spawn_depth_allowed(
            spawn_depth=child_spawn_depth,
            budget=RunBudget(max_spawn_depth=max_spawn_depth),
            default_max_spawn_depth=self._default_max_spawn_depth,
        )
        if depth_denial is not None:
            denial_trace = self._record_spawn_denial(
                request=request,
                parent_run_id=parent_run_id,
                message=depth_denial.message,
                denial_reason="spawn_depth",
            )
            return SpawnWorkerResult(
                parent_run_id=parent_run_id,
                child_session_id="",
                child_run_id=None,
                reply=depth_denial.message,
                status="failed",
                disposition="error",
                parent_trace_events=denial_trace,
                denied=True,
                denial_reason="spawn_depth",
            )

        tool_names = [str(name).strip() for name in request.allowed_tool_names if str(name).strip()]
        if not tool_names:
            tool_names = list(budget.allowed_tool_names)
        child_allowed, policy_denied = resolve_child_allowed_tool_names(
            parent_budget=parent_budget,
            requested_tool_names=tool_names,
            registered_tool_names=registered_tool_names,
        )
        if policy_denied or (tool_names and not child_allowed):
            parent_allow = parent_effective_allow_set(
                parent_budget=parent_budget,
                registered_tool_names=registered_tool_names,
            )
            message = spawn_policy_denied_message(
                denied_tool_names=policy_denied,
                parent_allow=parent_allow,
            )
            denial_trace = self._record_spawn_denial(
                request=request,
                parent_run_id=parent_run_id,
                message=message,
                denial_reason="policy_inherit",
            )
            return SpawnWorkerResult(
                parent_run_id=parent_run_id,
                child_session_id="",
                child_run_id=None,
                reply=message,
                status="failed",
                disposition="error",
                parent_trace_events=denial_trace,
                denied=True,
                denial_reason="policy_inherit",
            )

        parent_allow = parent_effective_allow_set(
            parent_budget=parent_budget,
            registered_tool_names=registered_tool_names,
        )
        parent_trace = self._start_orchestrator_run(
            request=request,
            parent_run_id=parent_run_id,
            budget=budget,
            parent_budget=parent_budget,
            registered_tool_names=registered_tool_names,
            child_spawn_depth=child_spawn_depth,
            max_spawn_depth=max_spawn_depth,
            max_child_runs=max_child_runs,
        )
        parent_trace.append(HarnessTraceEventType.SUBAGENT_SPAWNED)
        child_result = await self._execute_child_spawn(
            request=request,
            parent_run_id=parent_run_id,
            parent_allow=parent_allow,
            parent_context_snapshot=parent_context_snapshot,
            instruction=request.instruction,
            child_allowed=child_allowed,
            child_spawn_depth=child_spawn_depth,
            max_spawn_depth=max_spawn_depth,
            max_child_runs=max_child_runs,
            budget=budget,
            parent_budget=parent_budget,
            context_mode=context_mode,
            source_suffix="subagent",
        )
        parent_trace.append(HarnessTraceEventType.SUBAGENT_COMPLETED)
        self._finalize_orchestrator_run(
            parent_run_id=parent_run_id,
            parent_session_id=request.parent_session_id,
            source_message_id=request.source_message_id,
            trace_event_types=parent_trace,
            status=child_result.status,
            reply=child_result.reply,
            child_session_id=child_result.child_session_id,
            child_run_id=child_result.child_run_id,
            child_status=child_result.child_status,
            child_result=child_result.child_result,
            context_mode=context_mode,
        )
        return SpawnWorkerResult(
            parent_run_id=parent_run_id,
            child_session_id=child_result.child_session_id,
            child_run_id=child_result.child_run_id,
            reply=child_result.reply,
            status=child_result.status,
            disposition=child_result.disposition,
            parent_trace_events=parent_trace,
            child_status=child_result.child_status,
            child_result=child_result.child_result,
        )

    async def spawn_workers(
        self,
        *,
        request: SpawnWorkerRequest,
        tasks: list[SpawnWorkerTaskSpec],
        parent_context_snapshot: RuntimeContextSnapshot,
        run_budget: RunBudget | None = None,
        registered_tool_names: list[str] | None = None,
        max_parallel: int | None = None,
    ) -> SpawnWorkersResult:
        normalized_tasks = [task for task in tasks if str(task.instruction or "").strip()]
        if not normalized_tasks:
            return SpawnWorkersResult(
                parent_run_id="",
                reply="No subagent tasks were provided.",
                status="failed",
                disposition="error",
                denied=True,
                denial_reason="empty_tasks",
            )

        budget = run_budget or RunBudget()
        parent_budget = request.parent_budget or budget
        parent_run_id = self._resolve_parent_run_id(
            parent_session_id=request.parent_session_id,
            explicit_parent_run_id=request.parent_run_id,
        )
        max_child_runs = effective_max_child_runs(budget=budget, default=self._default_max_child_runs)
        if request.parent_max_child_runs is not None:
            max_child_runs = max(0, int(request.parent_max_child_runs))
        child_count = self._count_child_runs(parent_run_id=parent_run_id)
        if child_count + len(normalized_tasks) > max_child_runs:
            message = child_run_limit_denied_message(
                child_count=child_count,
                max_child_runs=max_child_runs,
            )
            self._record_spawn_denial(
                request=request,
                parent_run_id=parent_run_id,
                message=message,
                denial_reason="child_run_limit",
            )
            return SpawnWorkersResult(
                parent_run_id=parent_run_id,
                reply=message,
                status="failed",
                disposition="error",
                denied=True,
                denial_reason="child_run_limit",
            )

        child_spawn_depth = int(request.parent_spawn_depth) + 1
        max_spawn_depth = effective_max_spawn_depth(
            budget=budget,
            default=self._default_max_spawn_depth,
        )
        if request.parent_max_spawn_depth is not None:
            max_spawn_depth = max(0, int(request.parent_max_spawn_depth))
        depth_denial = check_spawn_depth_allowed(
            spawn_depth=child_spawn_depth,
            budget=RunBudget(max_spawn_depth=max_spawn_depth),
            default_max_spawn_depth=self._default_max_spawn_depth,
        )
        if depth_denial is not None:
            self._record_spawn_denial(
                request=request,
                parent_run_id=parent_run_id,
                message=depth_denial.message,
                denial_reason="spawn_depth",
            )
            return SpawnWorkersResult(
                parent_run_id=parent_run_id,
                reply=depth_denial.message,
                status="failed",
                disposition="error",
                denied=True,
                denial_reason="spawn_depth",
            )

        parent_allow = parent_effective_allow_set(
            parent_budget=parent_budget,
            registered_tool_names=registered_tool_names,
        )
        resolved_tasks: list[tuple[SpawnWorkerTaskSpec, list[str]]] = []
        denied_tools: list[str] = []
        for task in normalized_tasks:
            requested = list(task.tool_names) or list(request.allowed_tool_names) or list(budget.allowed_tool_names)
            child_allowed, policy_denied = resolve_child_allowed_tool_names(
                parent_budget=parent_budget,
                requested_tool_names=requested,
                registered_tool_names=registered_tool_names,
            )
            if policy_denied:
                denied_tools.extend(policy_denied)
                continue
            if requested and not child_allowed:
                denied_tools.extend(requested)
                continue
            resolved_tasks.append((task, child_allowed or sorted(parent_allow)))
        if denied_tools or not resolved_tasks:
            message = spawn_policy_denied_message(
                denied_tool_names=sorted(set(denied_tools)),
                parent_allow=parent_allow,
            )
            self._record_spawn_denial(
                request=request,
                parent_run_id=parent_run_id,
                message=message,
                denial_reason="policy_inherit",
            )
            return SpawnWorkersResult(
                parent_run_id=parent_run_id,
                reply=message,
                status="failed",
                disposition="error",
                denied=True,
                denial_reason="policy_inherit",
            )

        parent_trace = self._start_orchestrator_run(
            request=request,
            parent_run_id=parent_run_id,
            budget=budget,
            parent_budget=parent_budget,
            registered_tool_names=registered_tool_names,
            child_spawn_depth=child_spawn_depth,
            max_spawn_depth=max_spawn_depth,
            max_child_runs=max_child_runs,
        )
        parent_trace.append(HarnessTraceEventType.SUBAGENT_SPAWNED)

        parallel_limit = max(1, int(max_parallel or self._default_max_parallel_spawns))
        semaphore = asyncio.Semaphore(parallel_limit)

        async def run_task(
            task: SpawnWorkerTaskSpec,
            child_allowed: list[str],
            *,
            index: int,
        ) -> SpawnWorkerResult:
            async with semaphore:
                return await self._execute_child_spawn(
                    request=request,
                    parent_run_id=parent_run_id,
                    parent_allow=parent_allow,
                    parent_context_snapshot=parent_context_snapshot,
                    instruction=task.instruction,
                    child_allowed=child_allowed,
                    child_spawn_depth=child_spawn_depth,
                    max_spawn_depth=max_spawn_depth,
                    max_child_runs=max_child_runs,
                    budget=budget,
                    parent_budget=parent_budget,
                    context_mode=normalize_context_mode(
                        str(task.context_mode or request.context_mode)
                    ),
                    source_suffix=f"subagent-{index}",
                )

        child_results = await asyncio.gather(
            *[
                run_task(task, child_allowed, index=index)
                for index, (task, child_allowed) in enumerate(resolved_tasks, start=1)
            ]
        )
        parent_trace.append(HarnessTraceEventType.SUBAGENT_COMPLETED)
        combined_status = "completed" if all(item.status == "completed" for item in child_results) else "failed"
        reply = format_spawn_workers_reply(results=list(child_results))
        self._finalize_orchestrator_run(
            parent_run_id=parent_run_id,
            parent_session_id=request.parent_session_id,
            source_message_id=request.source_message_id,
            trace_event_types=parent_trace,
            status=combined_status,
            reply=reply,
            child_session_id=",".join(item.child_session_id for item in child_results if item.child_session_id),
            child_run_id=None,
            child_status=combined_status,
            child_result=None,
        )
        return SpawnWorkersResult(
            parent_run_id=parent_run_id,
            results=list(child_results),
            reply=reply,
            status=combined_status,
            disposition="respond" if combined_status == "completed" else "error",
        )

    async def _execute_child_spawn(
        self,
        *,
        request: SpawnWorkerRequest,
        parent_run_id: str,
        parent_allow: frozenset[str],
        parent_context_snapshot: RuntimeContextSnapshot,
        instruction: str,
        child_allowed: list[str],
        child_spawn_depth: int,
        max_spawn_depth: int,
        max_child_runs: int,
        budget: RunBudget,
        parent_budget: RunBudget,
        context_mode: str,
        source_suffix: str,
    ) -> SpawnWorkerResult:
        child_session = self._session_repository.create(
            session_kind=SUBAGENT_SESSION_KIND,
            parent_session_id=request.parent_session_id,
            metadata={
                "parent_run_id": parent_run_id,
                "parent_session_id": request.parent_session_id,
                "context_mode": context_mode,
            },
        )
        child_context = self._child_context_snapshot(
            child_session_id=child_session.id,
            parent_snapshot=parent_context_snapshot,
            context_mode=context_mode,
        )
        child_budget = RunBudget(
            policy_profile=str(request.policy_profile or parent_budget.policy_profile or budget.policy_profile or "").strip()
            or DEFAULT_SUBAGENT_POLICY_PROFILE,
            allowed_tool_names=child_allowed,
            denied_tool_names=list(parent_budget.denied_tool_names),
            max_steps=8,
            max_tool_calls=budget.max_tool_calls if budget.max_tool_calls is not None else 8,
            timeout_seconds=(
                budget.timeout_seconds if int(request.parent_spawn_depth or 0) > 0 else None
            ),
            max_spawn_depth=max_spawn_depth,
            max_child_runs=max_child_runs,
        )
        child_metadata = {
            **dict(request.run_metadata or {}),
            "agent_role": SUBAGENT_WORKER_ROLE,
            "parent_run_id": parent_run_id,
            "parent_session_id": request.parent_session_id,
            "spawn_depth": child_spawn_depth,
            "context_mode": context_mode,
            "parent_allowed_tool_names": sorted(parent_allow),
        }
        child_user_text = build_subagent_user_text(
            instruction=instruction,
            tool_names=child_allowed,
        )
        child_turn = await self._turn_runner.run_turn(
            session_id=child_session.id,
            source_message_id=f"{request.source_message_id}:{source_suffix}",
            user_text=child_user_text,
            raw_text=child_user_text,
            upload=None,
            context_snapshot=child_context,
            run_budget=child_budget,
            run_metadata=child_metadata,
        )
        child_run_id = self._latest_run_id_for_session(session_id=child_session.id) or ""
        child_result = subagent_result_from_turn(
            child_run_id=child_run_id,
            child_session_id=child_session.id,
            allowed_tool_names=child_allowed,
            turn=child_turn,
        )
        return SpawnWorkerResult(
            parent_run_id=parent_run_id,
            child_session_id=child_session.id,
            child_run_id=child_run_id or None,
            reply=format_spawn_reply(child_result=child_result, denied=False),
            status="completed" if child_turn.status == "completed" else "failed",
            disposition=child_turn.disposition,
            child_status=child_turn.status,
            child_result=child_result,
        )

    def _record_spawn_denial(
        self,
        *,
        request: SpawnWorkerRequest,
        parent_run_id: str,
        message: str,
        denial_reason: str,
    ) -> list[str]:
        trace = [HarnessTraceEventType.RUN_STARTED, HarnessTraceEventType.SUBAGENT_SPAWN_DENIED]
        if self._run_record_repository is None:
            return trace
        denial_run_id = f"spawn-denied-{uuid4().hex}"
        run_input = new_run_input(
            session_id=request.parent_session_id,
            source_message_id=request.source_message_id,
            user_text=request.instruction,
            raw_text=request.instruction,
            upload=None,
            context_snapshot=self._orchestrator_context_snapshot(request.parent_session_id),
            budget=RunBudget(),
            metadata={
                **dict(request.run_metadata or {}),
                "spawn_orchestrator": True,
                "spawn_denied": True,
                "denial_reason": denial_reason,
                "linked_parent_run_id": parent_run_id,
            },
            agent_role=SPAWN_ORCHESTRATOR_AGENT_ROLE,
        )
        run_input.run_id = denial_run_id
        run_input.trace_id = denial_run_id
        self._run_record_repository.create_started(
            run_input=run_input,
            harness_id=self._harness_id,
            input_metadata={
                "spawn_orchestrator": True,
                "spawn_denied": True,
                "denial_reason": denial_reason,
            },
        )
        trace_events = [
            RunTraceEvent(
                event_type=event_type,
                run_id=denial_run_id,
                session_id=request.parent_session_id,
                sequence=index,
                metadata={"phase": "spawn", "denial_reason": denial_reason},
            )
            for index, event_type in enumerate(trace)
        ]
        self._run_record_repository.mark_completed(
            run_id=denial_run_id,
            status="failed",
            outcome="error",
            steps=0,
            trace_events=trace_events,
            metadata={"denial_reason": denial_reason, "spawn_denied": True},
        )
        return trace

    def _get_run_record(self, *, run_id: str) -> AgentRunRecord | None:
        if self._run_record_repository is None:
            return None
        try:
            return self._run_record_repository.get(run_id=run_id)
        except KeyError:
            return None

    def _append_trace_events(
        self,
        *,
        run_id: str,
        trace_events: list[RunTraceEvent],
        metadata_patch: dict[str, Any] | None = None,
    ) -> None:
        if self._run_record_repository is None:
            return
        append = getattr(self._run_record_repository, "append_trace_events", None)
        if callable(append):
            append(
                run_id=run_id,
                trace_events=trace_events,
                metadata_patch=metadata_patch,
            )

    def _resolve_parent_run_id(
        self,
        *,
        parent_session_id: str,
        explicit_parent_run_id: str | None,
    ) -> str:
        if str(explicit_parent_run_id or "").strip():
            return str(explicit_parent_run_id).strip()
        if self._run_record_repository is None:
            return f"spawn-parent-{uuid4().hex}"
        for record in self._run_record_repository.list_by_session(session_id=parent_session_id):
            if str(record.agent_role or "").strip() == SPAWN_ORCHESTRATOR_AGENT_ROLE:
                return record.run_id
        return f"spawn-parent-{uuid4().hex}"

    def _count_child_runs(self, *, parent_run_id: str) -> int:
        if self._run_record_repository is None:
            return 0
        return len(self._run_record_repository.list_by_parent_run_id(parent_run_id=parent_run_id))

    def _start_orchestrator_run(
        self,
        *,
        request: SpawnWorkerRequest,
        parent_run_id: str,
        budget: RunBudget,
        parent_budget: RunBudget,
        registered_tool_names: list[str] | None,
        child_spawn_depth: int,
        max_spawn_depth: int,
        max_child_runs: int,
    ) -> list[str]:
        if self._run_record_repository is None:
            return [HarnessTraceEventType.RUN_STARTED]
        existing = self._get_run_record(run_id=parent_run_id)
        if existing is not None:
            return [HarnessTraceEventType.RUN_STARTED]
        run_input = new_run_input(
            session_id=request.parent_session_id,
            source_message_id=request.source_message_id,
            user_text=request.instruction,
            raw_text=request.instruction,
            upload=None,
            context_snapshot=self._orchestrator_context_snapshot(request.parent_session_id),
            budget=RunBudget(
                policy_profile=parent_budget.policy_profile or budget.policy_profile,
                allowed_tool_names=sorted(
                    parent_effective_allow_set(
                        parent_budget=parent_budget,
                        registered_tool_names=None,
                    )
                ),
                denied_tool_names=list(parent_budget.denied_tool_names or budget.denied_tool_names),
                max_spawn_depth=max_spawn_depth,
                max_child_runs=max_child_runs,
            ),
            metadata={
                **dict(request.run_metadata or {}),
                "spawn_orchestrator": True,
            },
            parent_run_id=None,
            spawn_depth=int(request.parent_spawn_depth),
            agent_role=SPAWN_ORCHESTRATOR_AGENT_ROLE,
        )
        run_input.run_id = parent_run_id
        run_input.trace_id = parent_run_id
        self._run_record_repository.create_started(
            run_input=run_input,
            harness_id=self._harness_id,
            input_metadata={
                "spawn_orchestrator": True,
                "instruction": request.instruction,
            },
        )
        return [HarnessTraceEventType.RUN_STARTED]

    def _finalize_orchestrator_run(
        self,
        *,
        parent_run_id: str,
        parent_session_id: str,
        source_message_id: str,
        trace_event_types: list[str],
        status: str,
        reply: str,
        child_session_id: str,
        child_run_id: str | None,
        child_status: str | None,
        child_result: SubagentResultSpec | None = None,
        context_mode: str | None = None,
    ) -> None:
        if self._run_record_repository is None:
            return
        spawn_metadata = {"phase": "spawn"}
        if context_mode:
            spawn_metadata["context_mode"] = normalize_context_mode(context_mode)
        trace_events = [
            RunTraceEvent(
                event_type=event_type,
                run_id=parent_run_id,
                session_id=parent_session_id,
                sequence=index,
                metadata=dict(spawn_metadata),
            )
            for index, event_type in enumerate(trace_event_types)
        ]
        existing = self._get_run_record(run_id=parent_run_id)
        if existing is not None and str(existing.agent_role or "").strip() != SPAWN_ORCHESTRATOR_AGENT_ROLE:
            self._append_trace_events(
                run_id=parent_run_id,
                trace_events=trace_events,
                metadata_patch={
                    "child_session_id": child_session_id,
                    "child_run_id": child_run_id,
                    "child_status": child_status,
                    "child_result": child_result.to_dict() if child_result is not None else None,
                    "spawn_reply_preview": reply[:240],
                    "context_mode": normalize_context_mode(context_mode) if context_mode else None,
                },
            )
            return
        completion_metadata = {
            "child_session_id": child_session_id,
            "child_run_id": child_run_id,
            "child_status": child_status,
            "child_result": child_result.to_dict() if child_result is not None else None,
            "spawn_reply_preview": reply[:240],
        }
        if context_mode:
            completion_metadata["context_mode"] = normalize_context_mode(context_mode)
        self._run_record_repository.mark_completed(
            run_id=parent_run_id,
            status=status,
            outcome="spawn_completed" if status == "completed" else "error",
            steps=0,
            trace_events=trace_events,
            metadata=completion_metadata,
        )

    def _latest_run_id_for_session(self, *, session_id: str) -> str | None:
        if self._run_record_repository is None:
            return None
        runs = self._run_record_repository.list_by_session(session_id=session_id)
        if not runs:
            return None
        return runs[0].run_id

    @staticmethod
    def _child_context_snapshot(
        *,
        child_session_id: str,
        parent_snapshot: RuntimeContextSnapshot,
        context_mode: str,
    ) -> RuntimeContextSnapshot:
        mode = normalize_context_mode(context_mode)
        if mode == "forked":
            return RuntimeContextSnapshot(
                session_kind=SUBAGENT_SESSION_KIND,
                session_metadata={"context_mode": "forked"},
                current_source_event_id=parent_snapshot.current_source_event_id,
                recent_events=list(parent_snapshot.recent_events),
                pending_state=None,
                last_action=parent_snapshot.last_action,
                skill_state=dict(parent_snapshot.skill_state),
            )
        return RuntimeContextSnapshot(
            session_kind=SUBAGENT_SESSION_KIND,
            session_metadata={"context_mode": "isolated"},
            current_source_event_id=None,
            recent_events=[],
            pending_state=None,
            last_action=None,
            skill_state={},
        )

    def _orchestrator_context_snapshot(self, parent_session_id: str) -> RuntimeContextSnapshot:
        return RuntimeContextSnapshot(
            session_kind="conversation",
            session_metadata={},
            current_source_event_id=None,
            recent_events=[],
            pending_state=None,
            last_action=None,
            skill_state={},
        )


def format_spawn_reply(
    *,
    child_result: SubagentResultSpec | None = None,
    child_turn: Any = None,
    denied: bool = False,
) -> str:
    if denied:
        return str(getattr(child_turn, "reply", child_turn) or "")
    if child_result is None:
        summary = str(getattr(child_turn, "reply", "") or "").strip() or "Subagent finished without a reply."
        return f"Subagent completed.\n\n{summary}"
    return (
        f"Subagent completed (status={child_result.status}, tools={child_result.tool_trace_count}).\n\n"
        f"{child_result.summary}"
    )


__all__ = ["SubagentSpawner", "child_run_limit_denied_message", "effective_max_child_runs", "format_spawn_reply"]
