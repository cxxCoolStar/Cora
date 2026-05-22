from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from fastapi import UploadFile

if TYPE_CHECKING:
    from core.agent.runtime_state import RuntimeContextSnapshot


@dataclass(slots=True)
class RunBudget:
    policy_profile: str | None = None
    max_steps: int | None = None
    timeout_seconds: float | None = None
    max_tool_calls: int | None = None
    allowed_tool_names: list[str] = field(default_factory=list)
    denied_tool_names: list[str] = field(default_factory=list)
    approved_tool_names: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_profile": self.policy_profile,
            "max_steps": self.max_steps,
            "timeout_seconds": self.timeout_seconds,
            "max_tool_calls": self.max_tool_calls,
            "allowed_tool_names": list(self.allowed_tool_names),
            "denied_tool_names": list(self.denied_tool_names),
            "approved_tool_names": list(self.approved_tool_names),
        }


class HarnessTraceEventType(StrEnum):
    RUN_STARTED = "run.started"
    PREPARE_COMPLETED = "prepare.completed"
    START_COMPLETED = "start.completed"
    RESOLVE_COMPLETED = "resolve.completed"
    TOOL_POLICY_APPLIED = "tool.policy.applied"
    TOOL_REQUESTED = "tool.requested"
    TOOL_HITL_APPROVED = "tool.hitl.approved"
    TOOL_SANDBOX_APPLIED = "tool.sandbox.applied"
    TOOL_COMPLETED = "tool.completed"
    TOOL_DENIED = "tool.denied"
    CLEANUP_COMPLETED = "cleanup.completed"
    BUDGET_TIMEOUT = "budget.timeout"
    RUN_FAILED = "run.failed"


@dataclass(slots=True)
class RunTraceEvent:
    event_type: str
    run_id: str
    session_id: str
    sequence: int = 0
    severity: str = "info"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class HarnessRunInput:
    run_id: str
    session_id: str
    source_message_id: str
    user_text: str
    raw_text: str | None
    upload: UploadFile | None
    context_snapshot: RuntimeContextSnapshot
    budget: RunBudget = field(default_factory=RunBudget)
    metadata: dict[str, Any] = field(default_factory=dict)
    trace_id: str | None = None
    parent_run_id: str | None = None
    agent_role: str = "primary"


@dataclass(slots=True)
class HarnessRunResult:
    run_id: str
    session_id: str
    status: str
    outcome: str
    trace_events: list[RunTraceEvent] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
