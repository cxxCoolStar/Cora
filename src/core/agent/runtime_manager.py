from __future__ import annotations

from typing import Any

from fastapi import UploadFile

from core.agent.execution_policy import ExecutionPolicyResolver
from core.agent.runtime_state import ConversationRuntimeState, EventSnapshot, PendingSessionState, RuntimeContextSnapshot, RuntimeStateDelta, UNSET


class AgentRuntimeManager:
    def __init__(
        self,
        *,
        pending_state_repository: Any,
        execution_policy_resolver: ExecutionPolicyResolver | None = None,
    ) -> None:
        self.pending_state_repository = pending_state_repository
        self.execution_policy_resolver = execution_policy_resolver or ExecutionPolicyResolver()

    def build_runtime_state(
        self,
        *,
        session_id: str,
        context_snapshot: RuntimeContextSnapshot,
        source_message_id: str,
        raw_text: str | None,
        upload: UploadFile | None,
        execution_mode: str | None = None,
    ) -> ConversationRuntimeState:
        pending_record = self.pending_state_repository.get_latest_pending(session_id=session_id)
        resolved_execution_mode = str(
            execution_mode
            or context_snapshot.execution_mode
            or self.execution_policy_resolver.default_mode_for_session_kind(context_snapshot.session_kind)
            or ""
        ).strip() or self.execution_policy_resolver.default_mode_for_session_kind(context_snapshot.session_kind)
        return ConversationRuntimeState(
            session_id=session_id,
            session_kind=str(context_snapshot.session_kind or "conversation").strip() or "conversation",
            execution_mode=resolved_execution_mode,
            session_metadata=dict(context_snapshot.session_metadata or {}),
            current_source_event_id=context_snapshot.current_source_event_id,
            recent_events=list(context_snapshot.recent_events),
            pending_state=self._runtime_pending_state(pending_record) or context_snapshot.pending_state,
            last_action=context_snapshot.last_action,
            skill_state=dict(context_snapshot.skill_state),
            metadata={
                "source_message_id": source_message_id,
                "raw_text": raw_text,
                "upload": upload,
            },
        )

    def runtime_to_context(self, runtime: ConversationRuntimeState) -> dict[str, Any]:
        policy = self.execution_policy_resolver.for_runtime(runtime)
        context = {
            "session_kind": runtime.session_kind,
            "session_metadata": dict(runtime.session_metadata),
            **policy.context_patch(),
            "recent_events": [AgentRuntimeManager._context_event_snapshot(event) for event in runtime.recent_events],
            "last_action": runtime.last_action,
            "skill_state": dict(runtime.skill_state),
            "pending_state": AgentRuntimeManager._pending_to_context(runtime.pending_state),
        }
        if runtime.current_source_event_id:
            context["current_source_event_id"] = runtime.current_source_event_id
        sandbox_workspace_root = str(runtime.metadata.get("sandbox_workspace_root") or "").strip()
        if sandbox_workspace_root:
            context["sandbox_workspace_root"] = sandbox_workspace_root
            context["execution_mode"] = str(runtime.metadata.get("execution_mode") or "sandbox")
        elif runtime.execution_mode:
            context["execution_mode"] = runtime.execution_mode
        for key in ("agent_run_id", "spawn_depth", "parent_run_id", "run_budget"):
            if key in runtime.metadata:
                context[key] = runtime.metadata[key]
        return context

    @staticmethod
    def snapshot_from_runtime(runtime: ConversationRuntimeState) -> RuntimeContextSnapshot:
        return RuntimeContextSnapshot(
            session_kind=runtime.session_kind,
            execution_mode=runtime.execution_mode,
            session_metadata=dict(runtime.session_metadata),
            current_source_event_id=runtime.current_source_event_id,
            recent_events=list(runtime.recent_events),
            pending_state=runtime.pending_state,
            last_action=runtime.last_action,
            skill_state=dict(runtime.skill_state),
        )

    @staticmethod
    def apply_state_delta(
        *,
        snapshot: RuntimeContextSnapshot,
        state_delta: RuntimeStateDelta,
    ) -> RuntimeContextSnapshot:
        return RuntimeContextSnapshot(
            session_kind=snapshot.session_kind,
            execution_mode=snapshot.execution_mode,
            session_metadata=dict(snapshot.session_metadata),
            current_source_event_id=state_delta.current_source_event_id or snapshot.current_source_event_id,
            recent_events=list(snapshot.recent_events),
            pending_state=(
                snapshot.pending_state
                if state_delta.pending_state is UNSET
                else state_delta.pending_state
            ),
            last_action=state_delta.last_action or snapshot.last_action,
            skill_state=AgentRuntimeManager._merge_skill_state(
                base=snapshot.skill_state,
                incoming=state_delta.skill_state,
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
    def _runtime_pending_state(pending: object | None) -> PendingSessionState | None:
        if pending is None:
            return None
        payload = dict(getattr(pending, "pending_payload_json", {}) or {})
        kind_map = {
            "item_selection": "selection",
            "save_decision": "choice",
            "upload_save": "upload",
        }
        pending_type = str(payload.get("type") or "")
        return PendingSessionState(
            pending_id=str(getattr(pending, "id", "")),
            skill_name=str(payload.get("skill_name") or "").strip() or None,
            kind=kind_map.get(pending_type, "choice"),
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

    @staticmethod
    def _pending_to_context(pending: PendingSessionState | None) -> dict[str, Any]:
        if pending is None:
            return {}
        return {
            "pending_id": pending.pending_id,
            "skill_name": pending.skill_name,
            "kind": pending.kind,
            "question": pending.question,
            "choices": list(pending.choices),
            **dict(pending.payload),
        }
