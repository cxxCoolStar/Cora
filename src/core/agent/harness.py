from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import MethodType
from typing import TYPE_CHECKING, Any, Protocol
from uuid import uuid4

from core.agent.execution_policy import ExecutionPolicy
from core.agent.loop import LoopResult
from core.agent.policy_profiles import get_harness_policy_profile
from core.agent.run_records import AgentRunRecordRepository
from core.agent.runtime_state import ConversationRuntimeState
from core.schemas.message import Message
from core.schemas.tool import ToolResult
from core.schemas.harness import HarnessRunInput, HarnessTraceEventType, RunBudget, RunTraceEvent

if TYPE_CHECKING:
    from core.agent.turn_runner import AgentTurnRunner


class AgentHarness(Protocol):
    id: str

    async def run(
        self,
        *,
        run_input: HarnessRunInput,
    ) -> LoopResult:
        ...


@dataclass(slots=True)
class DefaultAgentHarness:
    runner: AgentTurnRunner
    id: str = "default-single-agent"
    trace_events: list[RunTraceEvent] = field(default_factory=list)
    execution_policy: ExecutionPolicy | None = None
    run_record_repository: AgentRunRecordRepository | None = None

    async def run(
        self,
        *,
        run_input: HarnessRunInput,
    ) -> LoopResult:
        self.trace_events = []
        self._create_run_record(run_input=run_input)
        self._emit(
            HarnessTraceEventType.RUN_STARTED,
            run_input=run_input,
            metadata={
                "harness_id": self.id,
                "phase": "run",
            },
        )
        previous_max_steps = self.runner.loop.max_steps
        self.runner.loop.max_steps = self._resolved_max_steps(
            run_input.budget,
            fallback=previous_max_steps,
        )
        try:
            with self._tool_policy_guard(run_input=run_input):
                with self._tool_call_budget(run_input=run_input):
                    loop_result = await self._run_with_timeout(run_input=run_input)
            self._mark_run_completed(run_input=run_input, loop_result=loop_result)
            return loop_result
        except TimeoutError:
            loop_result = self._timeout_loop_result(run_input=run_input)
            self._mark_run_completed(run_input=run_input, loop_result=loop_result)
            return loop_result
        except Exception as exc:
            self._emit(
                HarnessTraceEventType.RUN_FAILED,
                run_input=run_input,
                severity="error",
                metadata={
                    "harness_id": self.id,
                    "phase": "run",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            self._mark_run_failed(run_input=run_input, error=exc)
            raise
        finally:
            self.runner.loop.max_steps = previous_max_steps

    async def _run_with_timeout(self, *, run_input: HarnessRunInput) -> LoopResult:
        timeout_seconds = run_input.budget.timeout_seconds
        if timeout_seconds is None or timeout_seconds <= 0:
            return await self._run_lifecycle(run_input=run_input)
        return await asyncio.wait_for(
            self._run_lifecycle(run_input=run_input),
            timeout=float(timeout_seconds),
        )

    async def _run_lifecycle(self, *, run_input: HarnessRunInput) -> LoopResult:
        prepared_turn = self.runner.prepare_turn(
            session_id=run_input.session_id,
            user_text=run_input.user_text,
            source_message_id=run_input.source_message_id,
            raw_text=run_input.raw_text,
            upload=run_input.upload,
            context_snapshot=run_input.context_snapshot,
        )
        original_tool_names = sorted(prepared_turn.tool_names)
        self._apply_run_tool_policy(run_input=run_input, prepared_turn=prepared_turn)
        self.execution_policy = prepared_turn.execution_policy
        profile = get_harness_policy_profile(run_input.budget.policy_profile)
        self._emit(
            HarnessTraceEventType.PREPARE_COMPLETED,
            run_input=run_input,
            metadata={
                "harness_id": self.id,
                "phase": "prepare",
                "policy_profile": profile.name if profile is not None else None,
                "tool_count": len(prepared_turn.tool_specs),
                "original_tool_count": len(original_tool_names),
                "budget_max_steps": self.runner.loop.max_steps,
                "budget_timeout_seconds": run_input.budget.timeout_seconds,
                "budget_max_tool_calls": run_input.budget.max_tool_calls,
                "budget_allowed_tool_names": self._effective_allowed_tool_names(run_input.budget),
                "budget_denied_tool_names": self._effective_denied_tool_names(run_input.budget),
            },
        )
        self._emit_tool_policy(
            run_input=run_input,
            prepared_turn=prepared_turn,
            original_tool_names=original_tool_names,
        )
        plan = await self.runner._build_turn_execution_plan(
            session_id=run_input.session_id,
            user_text=run_input.user_text,
            raw_text=run_input.raw_text,
            upload=run_input.upload,
            prepared_turn=prepared_turn,
        )
        self._emit(
            HarnessTraceEventType.START_COMPLETED,
            run_input=run_input,
            metadata={
                "harness_id": self.id,
                "phase": "start",
                "initial_exit_reason": plan.initial_loop_result.exit_reason,
            },
        )
        loop_result = await self.runner._execute_turn_plan(plan)
        self._emit(
            HarnessTraceEventType.RESOLVE_COMPLETED,
            run_input=run_input,
            metadata={
                "harness_id": self.id,
                "phase": "resolve",
                "exit_reason": loop_result.exit_reason,
                "status": loop_result.status,
                "steps": loop_result.steps,
            },
        )
        self._emit_tool_events(run_input=run_input, loop_result=loop_result)
        self._emit(
            HarnessTraceEventType.CLEANUP_COMPLETED,
            run_input=run_input,
            metadata={
                "harness_id": self.id,
                "phase": "cleanup",
            },
        )
        return loop_result

    def _timeout_loop_result(self, *, run_input: HarnessRunInput) -> LoopResult:
        self._emit(
            HarnessTraceEventType.BUDGET_TIMEOUT,
            run_input=run_input,
            severity="warning",
            metadata={
                "harness_id": self.id,
                "phase": "budget",
                "timeout_seconds": run_input.budget.timeout_seconds,
            },
        )
        message = Message.assistant(
            session_id=run_input.session_id,
            content="I reached the run timeout before finishing.",
        )
        return LoopResult(
            final_response=message.content,
            trace=[message],
            runtime=ConversationRuntimeState(session_id=run_input.session_id),
            exit_reason="timeout",
            steps=0,
            status="incomplete",
            disposition="respond",
        )

    def _create_run_record(self, *, run_input: HarnessRunInput) -> None:
        if self.run_record_repository is None:
            return
        self.run_record_repository.create_started(
            run_input=run_input,
            harness_id=self.id,
            input_metadata={
                "has_upload": run_input.upload is not None,
                "raw_text_present": bool(run_input.raw_text),
                **dict(run_input.metadata),
            },
        )

    def _mark_run_completed(self, *, run_input: HarnessRunInput, loop_result: LoopResult) -> None:
        if self.run_record_repository is None:
            return
        self.run_record_repository.mark_completed(
            run_id=run_input.run_id,
            status=loop_result.status,
            outcome=loop_result.exit_reason,
            steps=loop_result.steps,
            trace_events=list(self.trace_events),
            metadata={
                "disposition": loop_result.disposition,
                "tool_trace_count": len(loop_result.tool_trace),
                "artifact_count": len(loop_result.artifacts),
            },
        )

    def _mark_run_failed(self, *, run_input: HarnessRunInput, error: Exception) -> None:
        if self.run_record_repository is None:
            return
        self.run_record_repository.mark_failed(
            run_id=run_input.run_id,
            error=f"{type(error).__name__}: {error}",
            trace_events=list(self.trace_events),
            metadata={"harness_id": self.id},
        )

    def _emit(
        self,
        event_type: HarnessTraceEventType | str,
        *,
        run_input: HarnessRunInput,
        severity: str = "info",
        metadata: dict[str, object] | None = None,
    ) -> None:
        self.trace_events.append(
            RunTraceEvent(
                event_type=str(event_type),
                run_id=run_input.run_id,
                session_id=run_input.session_id,
                sequence=len(self.trace_events) + 1,
                severity=severity,
                metadata=dict(metadata or {}),
            )
        )

    def _emit_tool_events(self, *, run_input: HarnessRunInput, loop_result: LoopResult) -> None:
        for index, execution in enumerate(loop_result.tool_trace, start=1):
            severity = "error" if execution.status == "failed" else "info"
            self._emit(
                HarnessTraceEventType.TOOL_COMPLETED,
                run_input=run_input,
                severity=severity,
                metadata={
                    "harness_id": self.id,
                    "phase": "tool",
                    "tool_index": index,
                    "tool_name": execution.tool_name,
                    "action": execution.action,
                    "status": execution.status,
                    "disposition": execution.disposition,
                    "artifact_count": len(execution.artifacts),
                },
            )

    def _apply_run_tool_policy(self, *, run_input: HarnessRunInput, prepared_turn) -> None:
        allowed_tool_names = set(self._effective_allowed_tool_names(run_input.budget))
        denied_tool_names = set(self._effective_denied_tool_names(run_input.budget))
        if not allowed_tool_names and not denied_tool_names:
            return
        filtered_tool_specs = []
        for spec in prepared_turn.tool_specs:
            tool_name = str(getattr(spec, "name", "") or "").strip()
            if not tool_name:
                continue
            if allowed_tool_names and tool_name not in allowed_tool_names:
                continue
            if tool_name in denied_tool_names:
                continue
            filtered_tool_specs.append(spec)
        prepared_turn.tool_specs = filtered_tool_specs
        prepared_turn.tool_names = {
            str(spec.name).strip()
            for spec in filtered_tool_specs
            if str(spec.name or "").strip()
        }
        prepared_turn.decision_policy = self.runner._decision_policy(tool_names=prepared_turn.tool_names)

    def _emit_tool_policy(self, *, run_input: HarnessRunInput, prepared_turn, original_tool_names: list[str]) -> None:
        policy = prepared_turn.execution_policy
        profile = get_harness_policy_profile(run_input.budget.policy_profile)
        allowed_tool_names = sorted(policy.allowed_tool_names or [])
        run_allowed_tool_names = self._effective_allowed_tool_names(run_input.budget)
        run_denied_tool_names = self._effective_denied_tool_names(run_input.budget)
        exposed_tool_names = sorted(prepared_turn.tool_names)
        filtered_tool_names = [
            name for name in original_tool_names
            if name not in prepared_turn.tool_names
        ]
        unavailable_allowed_tool_names = [
            name for name in allowed_tool_names
            if name not in prepared_turn.tool_names
        ]
        self._emit(
            HarnessTraceEventType.TOOL_POLICY_APPLIED,
            run_input=run_input,
            metadata={
                "harness_id": self.id,
                "phase": "policy",
                "policy_profile": profile.name if profile is not None else None,
                "mode": policy.mode,
                "session_kind": policy.session_kind,
                "background_execution": policy.background_execution,
                "allow_clarification": policy.allow_clarification,
                "tool_surface": "restricted" if policy.allowed_tool_names is not None else "full",
                "allowed_tool_names": allowed_tool_names,
                "run_allowed_tool_names": run_allowed_tool_names,
                "run_denied_tool_names": run_denied_tool_names,
                "profile_allowed_tool_names": list(profile.allowed_tool_names) if profile is not None else [],
                "profile_denied_tool_names": list(profile.denied_tool_names) if profile is not None else [],
                "original_tool_names": original_tool_names,
                "filtered_tool_names": filtered_tool_names,
                "exposed_tool_names": exposed_tool_names,
                "exposed_tool_count": len(exposed_tool_names),
                "unavailable_allowed_tool_names": unavailable_allowed_tool_names,
                "max_tool_calls": run_input.budget.max_tool_calls,
            },
        )

    @contextmanager
    def _tool_call_budget(self, *, run_input: HarnessRunInput):
        max_tool_calls = self._effective_max_tool_calls(run_input.budget)
        if max_tool_calls is None:
            yield
            return
        max_tool_calls = max(0, int(max_tool_calls))
        executor = self.runner.loop.tool_executor
        original_execute_tool_call = executor.execute_tool_call
        counter = {"count": 0}

        async def guarded_execute_tool_call(inner_self, *, session_id, tool_call, runtime):
            if counter["count"] >= max_tool_calls:
                self._emit(
                    HarnessTraceEventType.TOOL_DENIED,
                    run_input=run_input,
                    severity="warning",
                    metadata={
                        "harness_id": self.id,
                        "phase": "policy",
                        "reason": "max_tool_calls_exceeded",
                        "max_tool_calls": max_tool_calls,
                        "attempted_tool_name": tool_call.tool_name,
                    },
                )
                return ToolResult(
                    success=False,
                    content=(
                        f"Tool call budget exceeded after {max_tool_calls} allowed call(s). "
                        f"`{tool_call.tool_name}` was not executed."
                    ),
                    status="failed",
                    disposition="respond",
                    action="policy_denied",
                    error="max_tool_calls_exceeded",
                    metadata={
                        "policy_denied": True,
                        "policy_reason": "max_tool_calls_exceeded",
                        "max_tool_calls": max_tool_calls,
                    },
                )
            counter["count"] += 1
            return await original_execute_tool_call(
                session_id=session_id,
                tool_call=tool_call,
                runtime=runtime,
            )

        executor.execute_tool_call = MethodType(guarded_execute_tool_call, executor)
        try:
            yield
        finally:
            executor.execute_tool_call = original_execute_tool_call

    @contextmanager
    def _tool_policy_guard(self, *, run_input: HarnessRunInput):
        allowed_tool_names = set(self._effective_allowed_tool_names(run_input.budget))
        denied_tool_names = set(self._effective_denied_tool_names(run_input.budget))
        if not allowed_tool_names and not denied_tool_names:
            yield
            return
        executor = self.runner.loop.tool_executor
        original_execute_tool_call = executor.execute_tool_call

        async def guarded_execute_tool_call(inner_self, *, session_id, tool_call, runtime):
            tool_name = str(tool_call.tool_name or "").strip()
            denial_reason = None
            if allowed_tool_names and tool_name not in allowed_tool_names:
                denial_reason = "tool_not_allowed"
            if tool_name in denied_tool_names:
                denial_reason = "tool_denied"
            if denial_reason is not None:
                self._emit(
                    HarnessTraceEventType.TOOL_DENIED,
                    run_input=run_input,
                    severity="warning",
                    metadata={
                        "harness_id": self.id,
                        "phase": "policy",
                        "reason": denial_reason,
                        "attempted_tool_name": tool_name,
                        "run_allowed_tool_names": sorted(allowed_tool_names),
                        "run_denied_tool_names": sorted(denied_tool_names),
                    },
                )
                return ToolResult(
                    success=False,
                    content=f"Tool `{tool_name}` is not allowed by this run's harness policy.",
                    status="failed",
                    disposition="respond",
                    action="policy_denied",
                    error=denial_reason,
                    metadata={
                        "policy_denied": True,
                        "policy_reason": denial_reason,
                        "attempted_tool_name": tool_name,
                    },
                )
            return await original_execute_tool_call(
                session_id=session_id,
                tool_call=tool_call,
                runtime=runtime,
            )

        executor.execute_tool_call = MethodType(guarded_execute_tool_call, executor)
        try:
            yield
        finally:
            executor.execute_tool_call = original_execute_tool_call

    @staticmethod
    def _resolved_max_steps(budget: RunBudget, *, fallback: int) -> int:
        if budget.max_steps is None:
            return max(1, int(fallback or 1))
        return max(1, int(budget.max_steps or 1))

    @staticmethod
    def _normalized_tool_names(values: list[str]) -> list[str]:
        names: list[str] = []
        for value in values:
            name = str(value or "").strip()
            if name and name not in names:
                names.append(name)
        return names

    @classmethod
    def _effective_allowed_tool_names(cls, budget: RunBudget) -> list[str]:
        profile = get_harness_policy_profile(budget.policy_profile)
        profile_names = list(profile.allowed_tool_names) if profile is not None else []
        explicit_names = cls._normalized_tool_names(budget.allowed_tool_names)
        if not profile_names:
            return explicit_names
        if not explicit_names:
            return cls._normalized_tool_names(profile_names)
        explicit_set = set(explicit_names)
        return [name for name in cls._normalized_tool_names(profile_names) if name in explicit_set]

    @classmethod
    def _effective_denied_tool_names(cls, budget: RunBudget) -> list[str]:
        profile = get_harness_policy_profile(budget.policy_profile)
        names = list(profile.denied_tool_names) if profile is not None else []
        names.extend(budget.denied_tool_names)
        return cls._normalized_tool_names(names)

    @staticmethod
    def _effective_max_tool_calls(budget: RunBudget) -> int | None:
        profile = get_harness_policy_profile(budget.policy_profile)
        if budget.max_tool_calls is not None:
            return budget.max_tool_calls
        if profile is not None:
            return profile.max_tool_calls
        return None


def new_run_input(
    *,
    session_id: str,
    source_message_id: str,
    user_text: str,
    raw_text: str | None,
    upload,
    context_snapshot,
    budget: RunBudget | None = None,
    metadata: dict[str, Any] | None = None,
) -> HarnessRunInput:
    return HarnessRunInput(
        run_id=f"run-{uuid4().hex}",
        session_id=session_id,
        source_message_id=source_message_id,
        user_text=user_text,
        raw_text=raw_text,
        upload=upload,
        context_snapshot=context_snapshot,
        budget=budget or RunBudget(),
        metadata=dict(metadata or {}),
    )
