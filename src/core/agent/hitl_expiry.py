from __future__ import annotations

from datetime import UTC, datetime, timedelta

from core.schemas.hitl import HitlRequest

DEFAULT_HITL_TTL_MINUTES = 10


def _utc_now() -> datetime:
    return datetime.now(UTC)


def default_hitl_expires_at(*, created_at: datetime | None = None) -> datetime:
    anchor = created_at or _utc_now()
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=UTC)
    return anchor + timedelta(minutes=DEFAULT_HITL_TTL_MINUTES)


def effective_expires_at(request: HitlRequest) -> datetime:
    if request.expires_at is not None:
        expires_at = request.expires_at
    else:
        expires_at = default_hitl_expires_at(created_at=request.created_at)
    if expires_at.tzinfo is None:
        return expires_at.replace(tzinfo=UTC)
    return expires_at.astimezone(UTC)


def is_hitl_expired(request: HitlRequest, *, now: datetime | None = None) -> bool:
    if request.status != "pending":
        return False
    current = now or _utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current >= effective_expires_at(request)


__all__ = [
    "DEFAULT_HITL_TTL_MINUTES",
    "default_hitl_expires_at",
    "effective_expires_at",
    "is_hitl_expired",
]
