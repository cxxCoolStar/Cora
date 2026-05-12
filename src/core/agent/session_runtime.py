from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.agent.runtime_state import EventSnapshot, RuntimeContextSnapshot


@dataclass(slots=True)
class SessionRuntimeSnapshotLoader:
    message_repository: Any
    source_event_repository: Any

    def load_context_snapshot(self, *, session_id: str) -> RuntimeContextSnapshot:
        base_context = self.message_repository.get_latest_assistant_context(session_id=session_id) or {}
        return self.compose_context_snapshot(session_id=session_id, base_context=base_context)

    def compose_context_snapshot(
        self,
        *,
        session_id: str,
        base_context: dict[str, Any],
        last_action: str | None = None,
    ) -> RuntimeContextSnapshot:
        current_source_event_id = str(base_context.get("current_source_event_id") or "").strip() or None
        last_action_value = str(last_action or base_context.get("last_action") or "").strip() or None
        pending_skill = str(base_context.get("pending_skill") or "").strip() or None
        return RuntimeContextSnapshot(
            current_source_event_id=current_source_event_id,
            recent_events=self.load_recent_event_snapshots(session_id=session_id),
            last_action=last_action_value,
            pending_skill=pending_skill,
            skill_state=dict(base_context.get("skill_state") or {}),
        )

    def load_recent_event_snapshots(self, *, session_id: str, limit: int = 5) -> list[EventSnapshot]:
        snapshots: list[EventSnapshot] = []
        for event in self.source_event_repository.list_by_session(session_id=session_id, limit=limit):
            snapshots.append(
                EventSnapshot(
                    source_event_id=event.id,
                    event_type=event.event_type,
                    channel=event.channel,
                    raw_text=event.raw_text,
                    original_file_name=event.original_file_name,
                    metadata={
                        "mime_type": event.mime_type,
                        "created_at": event.created_at.isoformat(),
                    },
                )
            )
        return snapshots
