from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from core.schemas.plan import PlanSpec, plan_spec_from_dict


@dataclass(slots=True)
class StoredValidatedPlan:
    session_id: str
    plan: PlanSpec
    planner_run_id: str

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "plan": self.plan.to_dict(),
            "planner_run_id": self.planner_run_id,
        }


class PlanStore(Protocol):
    def save(self, *, stored: StoredValidatedPlan) -> None:
        ...

    def get_latest(self, *, session_id: str, plan_id: str | None = None) -> StoredValidatedPlan | None:
        ...

    def clear_session(self, *, session_id: str) -> None:
        ...


class InMemoryPlanStore:
    def __init__(self) -> None:
        self._by_session: dict[str, StoredValidatedPlan] = {}

    def save(self, *, stored: StoredValidatedPlan) -> None:
        self._by_session[stored.session_id] = stored

    def get_latest(self, *, session_id: str, plan_id: str | None = None) -> StoredValidatedPlan | None:
        stored = self._by_session.get(session_id)
        if stored is None:
            return None
        if plan_id and stored.plan.plan_id != plan_id:
            return None
        return stored

    def clear_session(self, *, session_id: str) -> None:
        self._by_session.pop(session_id, None)


def stored_plan_from_metadata(
    *,
    session_id: str,
    planner_run_id: str,
    plan_payload: dict,
) -> StoredValidatedPlan:
    return StoredValidatedPlan(
        session_id=session_id,
        plan=plan_spec_from_dict(plan_payload),
        planner_run_id=planner_run_id,
    )


__all__ = [
    "InMemoryPlanStore",
    "PlanStore",
    "StoredValidatedPlan",
    "stored_plan_from_metadata",
]
