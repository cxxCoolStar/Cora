from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from core.schemas.harness import HarnessRunInput, RunTraceEvent


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class AgentRunRecord:
    run_id: str
    session_id: str
    source_message_id: str
    harness_id: str
    status: str
    outcome: str | None = None
    started_at: datetime = field(default_factory=_utc_now)
    completed_at: datetime | None = None
    steps: int | None = None
    input_metadata: dict[str, Any] = field(default_factory=dict)
    trace_events: list[RunTraceEvent] = field(default_factory=list)
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class AgentRunRecordRepository(Protocol):
    def create_started(
        self,
        *,
        run_input: HarnessRunInput,
        harness_id: str,
        input_metadata: dict[str, Any] | None = None,
    ) -> AgentRunRecord:
        ...

    def mark_completed(
        self,
        *,
        run_id: str,
        status: str,
        outcome: str,
        steps: int | None,
        trace_events: list[RunTraceEvent],
        metadata: dict[str, Any] | None = None,
    ) -> AgentRunRecord:
        ...

    def mark_failed(
        self,
        *,
        run_id: str,
        error: str,
        trace_events: list[RunTraceEvent],
        metadata: dict[str, Any] | None = None,
    ) -> AgentRunRecord:
        ...

    def get(self, *, run_id: str) -> AgentRunRecord:
        ...

    def list_by_session(self, *, session_id: str) -> list[AgentRunRecord]:
        ...


class InMemoryAgentRunRecordRepository:
    def __init__(self) -> None:
        self._records: dict[str, AgentRunRecord] = {}

    def create_started(
        self,
        *,
        run_input: HarnessRunInput,
        harness_id: str,
        input_metadata: dict[str, Any] | None = None,
    ) -> AgentRunRecord:
        record = AgentRunRecord(
            run_id=run_input.run_id,
            session_id=run_input.session_id,
            source_message_id=run_input.source_message_id,
            harness_id=harness_id,
            status="running",
            input_metadata=dict(input_metadata or {}),
        )
        self._records[record.run_id] = record
        return record

    def mark_completed(
        self,
        *,
        run_id: str,
        status: str,
        outcome: str,
        steps: int | None,
        trace_events: list[RunTraceEvent],
        metadata: dict[str, Any] | None = None,
    ) -> AgentRunRecord:
        record = self.get(run_id=run_id)
        record.status = status
        record.outcome = outcome
        record.completed_at = _utc_now()
        record.steps = steps
        record.trace_events = list(trace_events)
        record.error = None
        record.metadata = dict(metadata or {})
        return record

    def mark_failed(
        self,
        *,
        run_id: str,
        error: str,
        trace_events: list[RunTraceEvent],
        metadata: dict[str, Any] | None = None,
    ) -> AgentRunRecord:
        record = self.get(run_id=run_id)
        record.status = "failed"
        record.outcome = "error"
        record.completed_at = _utc_now()
        record.trace_events = list(trace_events)
        record.error = error
        record.metadata = dict(metadata or {})
        return record

    def get(self, *, run_id: str) -> AgentRunRecord:
        try:
            return self._records[run_id]
        except KeyError as exc:
            raise KeyError(f"Agent run record not found: {run_id}") from exc

    def list_by_session(self, *, session_id: str) -> list[AgentRunRecord]:
        return [
            record
            for record in self._records.values()
            if record.session_id == session_id
        ]
