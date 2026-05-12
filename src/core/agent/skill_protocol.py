from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


UNSET = object()


@dataclass(slots=True)
class HostEffect:
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PendingRequest:
    kind: str
    question: str
    choices: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PendingStateDelta:
    request: PendingRequest | None | object = UNSET
    status: str | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "PendingStateDelta":
        raw_request = payload["request"] if "request" in payload else UNSET
        request: PendingRequest | None | object = UNSET
        if isinstance(raw_request, dict):
            request = PendingRequest(
                kind=str(raw_request.get("kind") or raw_request.get("payload", {}).get("type") or "").strip(),
                question=str(raw_request.get("question") or "").strip(),
                choices=[str(choice) for choice in raw_request.get("choices") or []],
                payload=dict(raw_request.get("payload") or {}),
            )
        elif raw_request is None:
            request = None
        return cls(
            request=request,
            status=str(payload.get("status") or "").strip() or None,
        )


@dataclass(slots=True)
class SkillStateDelta:
    last_action: str | None = None
    current_source_event_id: str | None = None
    skill_state: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "SkillStateDelta":
        return cls(
            last_action=str(payload.get("last_action") or "").strip() or None,
            current_source_event_id=str(payload.get("current_source_event_id") or "").strip() or None,
            skill_state=dict(payload.get("skill_state") or {}),
        )


@dataclass(slots=True)
class SkillExecutionResult:
    message: str
    action: str
    status: str = "completed"
    disposition: str = "respond"
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    effects: list[HostEffect] = field(default_factory=list)
    pending_state_delta: PendingStateDelta = field(default_factory=PendingStateDelta)
    state_delta: SkillStateDelta = field(default_factory=SkillStateDelta)
    raw_payload: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "SkillExecutionResult":
        effects: list[HostEffect] = []
        raw_effects = payload.get("effects")
        if isinstance(raw_effects, list):
            for entry in raw_effects:
                if not isinstance(entry, dict):
                    continue
                kind = str(entry.get("kind") or "").strip()
                if not kind:
                    continue
                effects.append(HostEffect(kind=kind, payload=dict(entry.get("payload") or {})))
        artifacts = list(payload.get("artifacts") or [])

        return cls(
            message=str(payload.get("message") or ""),
            action=str(payload.get("action") or "skill").strip() or "skill",
            status=str(payload.get("status") or "completed"),
            disposition=str(payload.get("disposition") or "respond"),
            artifacts=artifacts,
            effects=effects,
            pending_state_delta=PendingStateDelta.from_payload(dict(payload.get("pending_state_delta") or {})),
            state_delta=SkillStateDelta.from_payload(dict(payload.get("state_delta") or {})),
            raw_payload=dict(payload),
        )


__all__ = [
    "HostEffect",
    "PendingRequest",
    "PendingStateDelta",
    "SkillExecutionResult",
    "SkillStateDelta",
    "UNSET",
]
