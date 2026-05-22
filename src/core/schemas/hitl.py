from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal


HitlStatus = Literal["pending", "approved", "rejected"]


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class HitlRequest:
    hitl_id: str
    run_id: str
    session_id: str
    tool_name: str
    status: HitlStatus = "pending"
    reason: str = "confirmation_required"
    policy_profile: str | None = None
    tool_risk: str = "medium"
    created_at: datetime = field(default_factory=_utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "hitl_id": self.hitl_id,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "tool_name": self.tool_name,
            "status": self.status,
            "reason": self.reason,
            "policy_profile": self.policy_profile,
            "tool_risk": self.tool_risk,
            "created_at": self.created_at.isoformat(),
            "metadata": dict(self.metadata),
        }
