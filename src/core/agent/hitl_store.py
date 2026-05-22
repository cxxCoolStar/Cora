from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from core.agent.hitl_expiry import default_hitl_expires_at, is_hitl_expired
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

    def expire(self, *, hitl_id: str) -> HitlRequest:
        ...

    def get_latest_pending_for_session(self, *, session_id: str) -> HitlRequest | None:
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
        created_at = _utc_now()
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
            created_at=created_at,
            expires_at=default_hitl_expires_at(created_at=created_at),
            metadata=dict(metadata or {}),
        )
        self._requests[hitl_id] = request
        return request

    def get(self, *, hitl_id: str) -> HitlRequest | None:
        return self._requests.get(hitl_id)

    def approve(self, *, hitl_id: str) -> HitlRequest:
        return self._resolve(hitl_id=hitl_id, status="approved")

    def reject(self, *, hitl_id: str) -> HitlRequest:
        return self._resolve(hitl_id=hitl_id, status="rejected")

    def expire(self, *, hitl_id: str) -> HitlRequest:
        request = self._requests.get(hitl_id)
        if request is None:
            raise KeyError(f"HITL request not found: {hitl_id}")
        if request.status == "expired":
            return request
        if request.status != "pending":
            raise ValueError(f"HITL request is not pending: {hitl_id}")
        request.status = "expired"
        request.resolved_at = _utc_now()
        return request

    def get_latest_pending_for_session(self, *, session_id: str) -> HitlRequest | None:
        normalized_session_id = str(session_id or "").strip()
        pending = [
            request
            for request in self._requests.values()
            if request.session_id == normalized_session_id and request.status == "pending"
        ]
        if not pending:
            return None
        latest = max(pending, key=lambda item: item.created_at)
        if is_hitl_expired(latest):
            self.expire(hitl_id=latest.hitl_id)
            return None
        return latest

    def _resolve(self, *, hitl_id: str, status: str) -> HitlRequest:
        request = self._require_pending(hitl_id=hitl_id)
        request.status = status  # type: ignore[assignment]
        request.resolved_at = _utc_now()
        return request

    def _require_pending(self, *, hitl_id: str) -> HitlRequest:
        request = self._requests.get(hitl_id)
        if request is None:
            raise KeyError(f"HITL request not found: {hitl_id}")
        if request.status == "expired":
            raise ValueError(f"HITL request expired: {hitl_id}")
        if is_hitl_expired(request):
            self.expire(hitl_id=hitl_id)
            raise ValueError(f"HITL request expired: {hitl_id}")
        if request.status != "pending":
            raise ValueError(f"HITL request is not pending: {hitl_id}")
        return request


__all__ = ["HitlStore", "InMemoryHitlStore"]
