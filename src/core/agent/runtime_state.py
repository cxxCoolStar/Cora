from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


PendingKind = Literal["reference", "capture_intent", "pending_upload_note"]


@dataclass(slots=True)
class ItemSnapshot:
    item_id: str
    title: str
    item_type: str
    summary: str = ""
    rank: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EventSnapshot:
    source_event_id: str
    event_type: str
    channel: str
    raw_text: str = ""
    original_file_name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PendingState:
    pending_id: str
    kind: PendingKind
    question: str
    choices: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ConversationRuntimeState:
    session_id: str
    current_source_event_id: str | None = None
    working_set: list[ItemSnapshot] = field(default_factory=list)
    recent_items: list[ItemSnapshot] = field(default_factory=list)
    recent_events: list[EventSnapshot] = field(default_factory=list)
    primary_focus: ItemSnapshot | None = None
    pending_state: PendingState | None = None
    last_action: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def summary_lines(self) -> list[str]:
        lines = [
            f"session_id={self.session_id}",
            f"current_source_event_id={self.current_source_event_id or 'none'}",
            f"last_action={self.last_action or 'none'}",
            f"pending_state={self.pending_state.kind if self.pending_state else 'none'}",
            f"working_set_count={len(self.working_set)}",
            f"recent_items_count={len(self.recent_items)}",
            f"recent_events_count={len(self.recent_events)}",
        ]
        if self.primary_focus is not None:
            lines.append(
                f"primary_focus={self.primary_focus.title} ({self.primary_focus.item_type})"
            )
        return lines
