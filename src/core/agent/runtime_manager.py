from __future__ import annotations

from typing import Any

from fastapi import UploadFile

from core.agent.runtime_state import ConversationRuntimeState, EventSnapshot, PendingState, RuntimeContextSnapshot, ToolStateDelta


class AgentRuntimeManager:
    def __init__(self, *, clarification_repository: Any) -> None:
        self.clarification_repository = clarification_repository

    def build_runtime_state(
        self,
        *,
        session_id: str,
        context_snapshot: RuntimeContextSnapshot,
        source_message_id: str,
        raw_text: str | None,
        upload: UploadFile | None,
    ) -> ConversationRuntimeState:
        pending_record = self.clarification_repository.get_latest_pending(session_id=session_id)
        return ConversationRuntimeState(
            session_id=session_id,
            current_source_event_id=context_snapshot.current_source_event_id,
            recent_events=list(context_snapshot.recent_events),
            pending_state=self._runtime_pending_state(pending_record),
            last_action=context_snapshot.last_action,
            pending_skill=context_snapshot.pending_skill,
            skill_state=dict(context_snapshot.skill_state),
            metadata={
                "source_message_id": source_message_id,
                "raw_text": raw_text,
                "upload": upload,
            },
        )

    @staticmethod
    def runtime_to_context(runtime: ConversationRuntimeState) -> dict[str, Any]:
        context = {
            "recent_events": [AgentRuntimeManager._context_event_snapshot(event) for event in runtime.recent_events],
            "last_action": runtime.last_action,
            "pending_skill": runtime.pending_skill,
            "skill_state": dict(runtime.skill_state),
        }
        if runtime.current_source_event_id:
            context["current_source_event_id"] = runtime.current_source_event_id
        return context

    @staticmethod
    def snapshot_from_runtime(runtime: ConversationRuntimeState) -> RuntimeContextSnapshot:
        return RuntimeContextSnapshot(
            current_source_event_id=runtime.current_source_event_id,
            recent_events=list(runtime.recent_events),
            last_action=runtime.last_action,
            pending_skill=runtime.pending_skill,
            skill_state=dict(runtime.skill_state),
        )

    @staticmethod
    def apply_state_update(
        *,
        snapshot: RuntimeContextSnapshot,
        state_update: ToolStateDelta,
    ) -> RuntimeContextSnapshot:
        return RuntimeContextSnapshot(
            current_source_event_id=state_update.current_source_event_id or snapshot.current_source_event_id,
            recent_events=list(snapshot.recent_events),
            last_action=state_update.last_action or snapshot.last_action,
            pending_skill=state_update.pending_skill or snapshot.pending_skill,
            skill_state=AgentRuntimeManager._merge_skill_state(
                base=snapshot.skill_state,
                incoming=state_update.skill_state,
            ),
        )

    @staticmethod
    def _merge_skill_state(*, base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base)
        for key, value in (incoming or {}).items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = {**merged[key], **value}
            else:
                merged[key] = value
        return merged

    @staticmethod
    def _runtime_pending_state(pending: object | None) -> PendingState | None:
        if pending is None:
            return None
        payload = dict(getattr(pending, "pending_payload_json", {}) or {})
        kind_map = {
            "reference_resolution": "reference",
            "capture_intent": "capture_intent",
            "input_interpretation": "pending_upload_note",
        }
        pending_type = str(payload.get("type") or "")
        return PendingState(
            pending_id=str(getattr(pending, "id", "")),
            kind=kind_map.get(pending_type, "pending_upload_note"),
            question=str(getattr(pending, "question", "")),
            choices=list(getattr(pending, "candidate_intents_json", []) or []),
            payload=payload,
        )

    @staticmethod
    def _context_event_snapshot(event: EventSnapshot) -> dict[str, Any]:
        snapshot = {
            "source_event_id": event.source_event_id,
            "event_type": event.event_type,
            "channel": event.channel,
            "raw_text": event.raw_text,
            "original_file_name": event.original_file_name,
        }
        snapshot.update(event.metadata)
        return snapshot
