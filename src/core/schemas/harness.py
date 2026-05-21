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


class HarnessTraceEventType(StrEnum):
    RUN_STARTED = "run.started"
    PREPARE_COMPLETED = "prepare.completed"
    START_COMPLETED = "start.completed"
    RESOLVE_COMPLETED = "resolve.completed"
    TOOL_POLICY_APPLIED = "tool.policy.applied"
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


@dataclass(slots=True)
class HarnessRunResult:
    run_id: str
    session_id: str
    status: str
    outcome: str
    trace_events: list[RunTraceEvent] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
