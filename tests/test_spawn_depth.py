from __future__ import annotations

from core.agent.spawn_depth import (
    check_spawn_depth_allowed,
    effective_max_spawn_depth,
    spawn_depth_exceeded,
)
from core.schemas.harness import RunBudget


def test_spawn_depth_exceeded_comparison() -> None:
    assert not spawn_depth_exceeded(spawn_depth=0, max_spawn_depth=1)
    assert not spawn_depth_exceeded(spawn_depth=1, max_spawn_depth=1)
    assert spawn_depth_exceeded(spawn_depth=2, max_spawn_depth=1)


def test_effective_max_spawn_depth_uses_budget_then_default() -> None:
    assert effective_max_spawn_depth(budget=RunBudget(), default=1) == 1
    assert effective_max_spawn_depth(budget=RunBudget(max_spawn_depth=0), default=1) == 0


def test_check_spawn_depth_allowed_returns_denial() -> None:
    denial = check_spawn_depth_allowed(
        spawn_depth=2,
        budget=RunBudget(max_spawn_depth=1),
        default_max_spawn_depth=1,
    )
    assert denial is not None
    assert "depth=2" in denial.message
    assert check_spawn_depth_allowed(
        spawn_depth=1,
        budget=RunBudget(max_spawn_depth=1),
        default_max_spawn_depth=1,
    ) is None
