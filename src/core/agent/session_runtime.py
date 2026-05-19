from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.agent.runtime_state import EventSnapshot, RuntimeContextSnapshot


@dataclass(slots=True)
class SessionRuntimeSnapshotLoader:
    message_repository: Any
    source_event_repository: Any
    session_repository: Any | None = None

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
        pending_state = self._pending_state_from_context(dict(base_context.get("pending_state") or {}))
        session_kind = "conversation"
        session_metadata: dict[str, Any] = {}
        if self.session_repository is not None:
            try:
                session = self.session_repository.get(session_id)
            except KeyError:
                session = None
            if session is not None:
                session_kind = str(getattr(session, "session_kind", "conversation") or "conversation").strip() or "conversation"
                session_metadata = dict(getattr(session, "metadata_json", {}) or {})
        return RuntimeContextSnapshot(
            session_kind=session_kind,
            session_metadata=session_metadata,
            current_source_event_id=current_source_event_id,
            recent_events=self.load_recent_event_snapshots(session_id=session_id),
            pending_state=pending_state,
            last_action=last_action_value,
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

    @staticmethod
    def _pending_state_from_context(payload: dict[str, Any]):
        if not payload:
            return None
        from core.agent.runtime_state import PendingSessionState

        kind = str(payload.get("kind") or payload.get("type") or "choice").strip() or "choice"
        return PendingSessionState(
            pending_id=str(payload.get("pending_id") or "").strip(),
            skill_name=str(payload.get("skill_name") or "").strip() or None,
            kind=kind,  # type: ignore[arg-type]
            question=str(payload.get("question") or "").strip(),
            choices=[str(choice) for choice in payload.get("choices") or []],
            payload=dict(payload),
        )
