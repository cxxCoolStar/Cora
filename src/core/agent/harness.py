from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import MethodType
from typing import TYPE_CHECKING, Any, Protocol
from uuid import uuid4

from core.agent.execution_policy import ExecutionPolicy
from core.agent.loop import LoopResult
from core.agent.policy_profiles import get_harness_policy_profile
from core.agent.hitl_store import HitlStore, InMemoryHitlStore
from core.agent.sandbox_runtime import SandboxContext, SandboxWorkspaceManager
from core.agent.run_records import AgentRunRecordRepository
from core.agent.runtime_state import ConversationRuntimeState
from core.agent.tool_policy import ToolPolicyDecision
from core.agent.tool_policy_engine import (
    ToolPolicyEngine,
    effective_allowed_tool_names,
    effective_approved_tool_names,
    effective_denied_tool_names,
    effective_max_tool_calls,
    normalize_tool_risk,
    resolve_platform_name,
    should_expose_tool,
)
from core.schemas.tool_policy import ToolPolicyContext
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
    hitl_store: HitlStore | None = None

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
                "budget_allowed_tool_names": sorted(effective_allowed_tool_names(run_input.budget)),
                "budget_denied_tool_names": sorted(effective_denied_tool_names(run_input.budget)),
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
                "failure_category": self._failure_category_for_loop_result(loop_result),
                "cleanup_status": self._cleanup_status_for_trace(),
            },
        )

    def _mark_run_failed(self, *, run_input: HarnessRunInput, error: Exception) -> None:
        if self.run_record_repository is None:
            return
        self.run_record_repository.mark_failed(
            run_id=run_input.run_id,
            error=f"{type(error).__name__}: {error}",
            trace_events=list(self.trace_events),
            metadata={
                "harness_id": self.id,
                "failure_category": "infrastructure_failure",
                "cleanup_status": self._cleanup_status_for_trace(),
            },
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
            policy_decision = dict(execution.metadata.get("policy_decision") or {})
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
                    "policy_decision": policy_decision,
                },
            )

    def _apply_run_tool_policy(self, *, run_input: HarnessRunInput, prepared_turn) -> None:
        if not (
            effective_allowed_tool_names(run_input.budget)
            or effective_denied_tool_names(run_input.budget)
        ):
            return
        filtered_tool_specs = []
        for spec in prepared_turn.tool_specs:
            tool_name = str(getattr(spec, "name", "") or "").strip()
            if not tool_name:
                continue
            if not should_expose_tool(tool_name=tool_name, budget=run_input.budget):
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
        run_allowed_tool_names = sorted(effective_allowed_tool_names(run_input.budget))
        run_denied_tool_names = sorted(effective_denied_tool_names(run_input.budget))
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
    def _tool_policy_guard(self, *, run_input: HarnessRunInput):
        engine = ToolPolicyEngine()
        executor = self.runner.loop.tool_executor
        original_execute_tool_call = executor.execute_tool_call
        tool_metadata = self._tool_metadata_by_name()
        counter = {"count": 0}
        sandbox_state: dict[str, SandboxWorkspaceManager | None] = {
            "manager": self._resolve_sandbox_manager(run_input=run_input),
        }

        async def guarded_execute_tool_call(inner_self, *, session_id, tool_call, runtime):
            tool_name = str(tool_call.tool_name or "").strip()
            meta = tool_metadata.get(tool_name, {})
            policy = self.execution_policy
            decision = engine.evaluate(
                ToolPolicyContext(
                    tool_name=tool_name,
                    agent_role=run_input.agent_role,
                    platform=resolve_platform_name(
                        run_input.metadata.get("platform") or run_input.metadata.get("channel")
                    ),
                    policy_profile=run_input.budget.policy_profile,
                    allowed_tool_names=effective_allowed_tool_names(run_input.budget),
                    denied_tool_names=effective_denied_tool_names(run_input.budget),
                    tool_risk=str(meta.get("risk") or "medium"),
                    requires_confirmation=bool(meta.get("requires_confirmation")),
                    requires_sandbox=bool(meta.get("requires_sandbox")),
                    allowed_roles=frozenset(meta.get("allowed_roles") or ()),
                    max_tool_calls=effective_max_tool_calls(run_input.budget),
                    tool_calls_so_far=counter["count"],
                    session_kind=policy.session_kind if policy is not None else None,
                    background_execution=policy.background_execution if policy is not None else False,
                    approved_tool_names=effective_approved_tool_names(run_input.budget),
                )
            )
            if decision.decision == "deny":
                self._emit_tool_denied(run_input=run_input, tool_name=tool_name, decision=decision)
                return self._policy_denied_tool_result(tool_name=tool_name, decision=decision)
            if decision.decision == "ask":
                return self._policy_ask_tool_result(
                    run_input=run_input,
                    tool_name=tool_name,
                    decision=decision,
                    tool_call=tool_call,
                )
            if decision.decision == "sandbox":
                runtime = self._apply_sandbox_runtime(
                    run_input=run_input,
                    tool_name=tool_name,
                    decision=decision,
                    runtime=runtime,
                    sandbox_manager=sandbox_state["manager"],
                )
            if tool_name in effective_approved_tool_names(run_input.budget):
                self._emit_hitl_approved(
                    run_input=run_input,
                    tool_name=tool_name,
                )
            counter["count"] += 1
            result = await original_execute_tool_call(
                session_id=session_id,
                tool_call=tool_call,
                runtime=runtime,
            )
            result.metadata["policy_decision"] = decision.to_dict()
            return result

        executor.execute_tool_call = MethodType(guarded_execute_tool_call, executor)
        try:
            yield
        finally:
            executor.execute_tool_call = original_execute_tool_call
            manager = sandbox_state.get("manager")
            if manager is not None:
                manager.cleanup(run_id=run_input.run_id)

    def _emit_tool_denied(
        self,
        *,
        run_input: HarnessRunInput,
        tool_name: str,
        decision: ToolPolicyDecision,
    ) -> None:
        metadata: dict[str, object] = {
            "harness_id": self.id,
            "phase": "policy",
            "reason": decision.reason,
            "attempted_tool_name": tool_name,
            "policy_decision": decision.to_dict(),
            "run_allowed_tool_names": sorted(effective_allowed_tool_names(run_input.budget)),
            "run_denied_tool_names": sorted(effective_denied_tool_names(run_input.budget)),
        }
        max_tool_calls = decision.audit_metadata.get("max_tool_calls")
        if max_tool_calls is not None:
            metadata["max_tool_calls"] = max_tool_calls
        self._emit(
            HarnessTraceEventType.TOOL_DENIED,
            run_input=run_input,
            severity="warning",
            metadata=metadata,
        )

    def _resolve_hitl_store(self) -> HitlStore:
        return self.hitl_store or InMemoryHitlStore()

    def _resolve_sandbox_manager(self, *, run_input: HarnessRunInput) -> SandboxWorkspaceManager:
        cora_home = str(run_input.metadata.get("cora_home_dir") or "").strip()
        if cora_home:
            return SandboxWorkspaceManager.from_cora_home(Path(cora_home))
        return SandboxWorkspaceManager(base_dir=Path(".cora") / "sandboxes")

    def _apply_sandbox_runtime(
        self,
        *,
        run_input: HarnessRunInput,
        tool_name: str,
        decision: ToolPolicyDecision,
        runtime: ConversationRuntimeState,
        sandbox_manager: SandboxWorkspaceManager,
    ) -> ConversationRuntimeState:
        ctx = sandbox_manager.ensure(run_id=run_input.run_id)
        runtime.metadata = dict(runtime.metadata)
        runtime.metadata["sandbox_workspace_root"] = str(ctx.workspace_root)
        runtime.metadata["execution_mode"] = "sandbox"
        runtime.execution_mode = "sandbox"
        self._emit_sandbox_applied(
            run_input=run_input,
            tool_name=tool_name,
            decision=decision,
            sandbox_context=ctx,
        )
        return runtime

    def _emit_sandbox_applied(
        self,
        *,
        run_input: HarnessRunInput,
        tool_name: str,
        decision: ToolPolicyDecision,
        sandbox_context: SandboxContext,
    ) -> None:
        self._emit(
            HarnessTraceEventType.TOOL_SANDBOX_APPLIED,
            run_input=run_input,
            metadata={
                "harness_id": self.id,
                "phase": "policy",
                "attempted_tool_name": tool_name,
                "policy_decision": decision.to_dict(),
                "sandbox": sandbox_context.to_dict(),
            },
        )

    def _emit_hitl_approved(
        self,
        *,
        run_input: HarnessRunInput,
        tool_name: str,
    ) -> None:
        self._emit(
            HarnessTraceEventType.TOOL_HITL_APPROVED,
            run_input=run_input,
            metadata={
                "harness_id": self.id,
                "phase": "policy",
                "attempted_tool_name": tool_name,
                "resume_hitl_id": run_input.metadata.get("resume_hitl_id"),
                "parent_run_id": run_input.parent_run_id,
            },
        )

    def _policy_ask_tool_result(
        self,
        *,
        run_input: HarnessRunInput,
        tool_name: str,
        decision: ToolPolicyDecision,
        tool_call: Any,
    ) -> ToolResult:
        platform = resolve_platform_name(
            run_input.metadata.get("platform") or run_input.metadata.get("channel")
        )
        hitl_request = self._resolve_hitl_store().create_pending(
            run_id=run_input.run_id,
            session_id=run_input.session_id,
            tool_name=tool_name,
            reason=decision.reason,
            policy_profile=run_input.budget.policy_profile,
            tool_risk=decision.risk,
            tool_arguments=dict(getattr(tool_call, "arguments", None) or {}),
            metadata={
                "policy_decision": decision.to_dict(),
                "platform": platform,
            },
        )
        self._emit(
            HarnessTraceEventType.TOOL_REQUESTED,
            run_input=run_input,
            severity="warning",
            metadata={
                "harness_id": self.id,
                "phase": "policy",
                "reason": decision.reason,
                "attempted_tool_name": tool_name,
                "hitl_id": hitl_request.hitl_id,
                "policy_decision": decision.to_dict(),
            },
        )
        return ToolResult(
            success=False,
            content=decision.safe_user_message,
            status="failed",
            disposition="clarify",
            action="policy_ask",
            error=decision.reason,
            metadata={
                "policy_ask": True,
                "policy_reason": decision.reason,
                "attempted_tool_name": tool_name,
                "hitl_id": hitl_request.hitl_id,
                "needs_clarification": True,
                "policy_decision": decision.to_dict(),
            },
        )

    @staticmethod
    def _policy_denied_tool_result(*, tool_name: str, decision: ToolPolicyDecision) -> ToolResult:
        metadata: dict[str, object] = {
            "policy_denied": True,
            "policy_reason": decision.reason,
            "attempted_tool_name": tool_name,
            "policy_decision": decision.to_dict(),
        }
        max_tool_calls = decision.audit_metadata.get("max_tool_calls")
        if max_tool_calls is not None:
            metadata["max_tool_calls"] = max_tool_calls
        return ToolResult(
            success=False,
            content=decision.safe_user_message,
            status="failed",
            disposition="respond",
            action="policy_denied",
            error=decision.reason,
            metadata=metadata,
        )

    @staticmethod
    def _resolved_max_steps(budget: RunBudget, *, fallback: int) -> int:
        if budget.max_steps is None:
            return max(1, int(fallback or 1))
        return max(1, int(budget.max_steps or 1))

    def _tool_metadata_by_name(self) -> dict[str, dict[str, object]]:
        metadata_by_name: dict[str, dict[str, object]] = {}
        for spec in self.runner.loop.tool_specs:
            tool_name = str(getattr(spec, "name", "") or "").strip()
            if not tool_name:
                continue
            allowed_roles = getattr(spec, "allowed_roles", None) or []
            metadata_by_name[tool_name] = {
                "risk": normalize_tool_risk(getattr(spec, "risk", None)),
                "requires_confirmation": bool(getattr(spec, "requires_confirmation", False)),
                "requires_sandbox": bool(getattr(spec, "requires_sandbox", False)),
                "allowed_roles": tuple(str(role).strip() for role in allowed_roles if str(role).strip()),
            }
        return metadata_by_name

    def _failure_category_for_loop_result(self, loop_result: LoopResult) -> str | None:
        if loop_result.exit_reason == "timeout":
            return "timeout"
        if any(trace.action == "policy_ask" for trace in loop_result.tool_trace):
            return "needs_confirmation"
        if any(trace.action == "policy_denied" for trace in loop_result.tool_trace):
            return "permission_denied"
        if loop_result.status == "failed":
            return "tool_failure"
        return None

    def _cleanup_status_for_trace(self) -> str:
        if any(event.event_type == HarnessTraceEventType.CLEANUP_COMPLETED for event in self.trace_events):
            return "completed"
        return "skipped"


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
    trace_id: str | None = None,
    parent_run_id: str | None = None,
    agent_role: str = "primary",
) -> HarnessRunInput:
    run_id = f"run-{uuid4().hex}"
    return HarnessRunInput(
        run_id=run_id,
        session_id=session_id,
        source_message_id=source_message_id,
        user_text=user_text,
        raw_text=raw_text,
        upload=upload,
        context_snapshot=context_snapshot,
        budget=budget or RunBudget(),
        metadata=dict(metadata or {}),
        trace_id=trace_id or run_id,
        parent_run_id=parent_run_id,
        agent_role=agent_role,
    )
