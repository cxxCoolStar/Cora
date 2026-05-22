from __future__ import annotations

from typing import Protocol
from uuid import uuid4

from core.schemas.hitl import HitlRequest


class HitlStore(Protocol):
    def create_pending(
        self,
        *,
        run_id: str,
        session_id: str,
        tool_name: str,
        reason: str,
        policy_profile: str | None = None,
        tool_risk: str = "medium",
        metadata: dict[str, object] | None = None,
    ) -> HitlRequest:
        ...


class InMemoryHitlStore:
    def __init__(self) -> None:
        self._requests: dict[str, HitlRequest] = {}

    def create_pending(
        self,
        *,
        run_id: str,
        session_id: str,
        tool_name: str,
        reason: str,
        policy_profile: str | None = None,
        tool_risk: str = "medium",
        metadata: dict[str, object] | None = None,
    ) -> HitlRequest:
        hitl_id = f"hitl-{uuid4().hex}"
        request = HitlRequest(
            hitl_id=hitl_id,
            run_id=run_id,
            session_id=session_id,
            tool_name=tool_name,
            reason=reason,
            policy_profile=policy_profile,
            tool_risk=tool_risk,
            metadata=dict(metadata or {}),
        )
        self._requests[hitl_id] = request
        return request

    def get(self, *, hitl_id: str) -> HitlRequest | None:
        return self._requests.get(hitl_id)


__all__ = ["HitlStore", "InMemoryHitlStore"]
