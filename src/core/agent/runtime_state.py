from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


PendingKind = Literal["selection", "choice", "upload"]
UNSET = object()


@dataclass(slots=True)
class EventSnapshot:
    source_event_id: str
    event_type: str
    channel: str
    raw_text: str = ""
    original_file_name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PendingSessionState:
    pending_id: str
    skill_name: str | None
    kind: PendingKind
    question: str
    choices: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RuntimeContextSnapshot:
    current_source_event_id: str | None = None
    recent_events: list[EventSnapshot] = field(default_factory=list)
    pending_state: PendingSessionState | None = None
    last_action: str | None = None
    skill_state: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RuntimeStateDelta:
    last_action: str | None = None
    current_source_event_id: str | None = None
    pending_state: PendingSessionState | None | object = UNSET
    skill_state: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ConversationRuntimeState:
    session_id: str
    current_source_event_id: str | None = None
    recent_events: list[EventSnapshot] = field(default_factory=list)
    pending_state: PendingSessionState | None = None
    last_action: str | None = None
    skill_state: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def summary_lines(self) -> list[str]:
        lines = [
            f"session_id={self.session_id}",
            f"current_source_event_id={self.current_source_event_id or 'none'}",
            f"last_action={self.last_action or 'none'}",
            f"pending_owner={self.pending_state.skill_name if self.pending_state and self.pending_state.skill_name else 'none'}",
            f"pending_state={self.pending_state.kind if self.pending_state else 'none'}",
            f"skill_state_keys={','.join(sorted(self.skill_state.keys())) or 'none'}",
            f"recent_events_count={len(self.recent_events)}",
        ]
        return lines
