from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from core.schemas.hitl import HitlRequest


def _utc_now() -> datetime:
    return datetime.now(UTC)


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
        tool_arguments: dict[str, Any] | None = None,
        metadata: dict[str, object] | None = None,
    ) -> HitlRequest:
        ...

    def get(self, *, hitl_id: str) -> HitlRequest | None:
        ...

    def approve(self, *, hitl_id: str) -> HitlRequest:
        ...

    def reject(self, *, hitl_id: str) -> HitlRequest:
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
        tool_arguments: dict[str, Any] | None = None,
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
            tool_arguments=dict(tool_arguments or {}),
            metadata=dict(metadata or {}),
        )
        self._requests[hitl_id] = request
        return request

    def get(self, *, hitl_id: str) -> HitlRequest | None:
        return self._requests.get(hitl_id)

    def approve(self, *, hitl_id: str) -> HitlRequest:
        request = self._require_pending(hitl_id=hitl_id)
        request.status = "approved"
        request.resolved_at = _utc_now()
        return request

    def reject(self, *, hitl_id: str) -> HitlRequest:
        request = self._require_pending(hitl_id=hitl_id)
        request.status = "rejected"
        request.resolved_at = _utc_now()
        return request

    def _require_pending(self, *, hitl_id: str) -> HitlRequest:
        request = self._requests.get(hitl_id)
        if request is None:
            raise KeyError(f"HITL request not found: {hitl_id}")
        if request.status != "pending":
            raise ValueError(f"HITL request is not pending: {hitl_id}")
        return request


__all__ = ["HitlStore", "InMemoryHitlStore"]
