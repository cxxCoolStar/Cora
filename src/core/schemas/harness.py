from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastapi import UploadFile

from core.agent.runtime_state import RuntimeContextSnapshot


@dataclass(slots=True)
class RunBudget:
    max_steps: int = 6
    timeout_seconds: float | None = None
    max_tool_calls: int | None = None


@dataclass(slots=True)
class RunTraceEvent:
    event_type: str
    run_id: str
    session_id: str
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
