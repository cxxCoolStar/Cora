from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol
from uuid import uuid4

from core.agent.execution_policy import ExecutionPolicy
from core.agent.loop import LoopResult
from core.schemas.harness import HarnessRunInput, RunBudget, RunTraceEvent

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

    async def run(
        self,
        *,
        run_input: HarnessRunInput,
    ) -> LoopResult:
        prepared_turn = self.runner.prepare_turn(
            session_id=run_input.session_id,
            user_text=run_input.user_text,
            source_message_id=run_input.source_message_id,
            raw_text=run_input.raw_text,
            upload=run_input.upload,
            context_snapshot=run_input.context_snapshot,
        )
        self.execution_policy = prepared_turn.execution_policy
        self._emit(
            "harness.prepare.completed",
            run_input=run_input,
            metadata={
                "harness_id": self.id,
                "tool_count": len(prepared_turn.tool_specs),
                "budget_max_steps": run_input.budget.max_steps,
            },
        )
        plan = await self.runner._build_turn_execution_plan(
            session_id=run_input.session_id,
            user_text=run_input.user_text,
            raw_text=run_input.raw_text,
            upload=run_input.upload,
            prepared_turn=prepared_turn,
        )
        self._emit(
            "harness.start.completed",
            run_input=run_input,
            metadata={
                "harness_id": self.id,
                "initial_exit_reason": plan.initial_loop_result.exit_reason,
            },
        )
        loop_result = await self.runner._execute_turn_plan(plan)
        self._emit(
            "harness.resolve.completed",
            run_input=run_input,
            metadata={
                "harness_id": self.id,
                "exit_reason": loop_result.exit_reason,
                "status": loop_result.status,
                "steps": loop_result.steps,
            },
        )
        self._emit(
            "harness.cleanup.completed",
            run_input=run_input,
            metadata={"harness_id": self.id},
        )
        return loop_result

    def _emit(
        self,
        event_type: str,
        *,
        run_input: HarnessRunInput,
        metadata: dict[str, object] | None = None,
    ) -> None:
        self.trace_events.append(
            RunTraceEvent(
                event_type=event_type,
                run_id=run_input.run_id,
                session_id=run_input.session_id,
                metadata=dict(metadata or {}),
            )
        )


def new_run_input(
    *,
    session_id: str,
    source_message_id: str,
    user_text: str,
    raw_text: str | None,
    upload,
    context_snapshot,
    budget: RunBudget | None = None,
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
    )
