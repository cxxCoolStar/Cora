from __future__ import annotations

from dataclasses import dataclass

from core.schemas.harness import RunBudget


def effective_max_spawn_depth(*, budget: RunBudget, default: int) -> int:
    if budget.max_spawn_depth is not None:
        return max(0, int(budget.max_spawn_depth))
    return max(0, int(default))


def spawn_depth_exceeded(*, spawn_depth: int, max_spawn_depth: int) -> bool:
    return int(spawn_depth) > int(max_spawn_depth)


def spawn_depth_denied_message(*, spawn_depth: int, max_spawn_depth: int) -> str:
    return (
        f"Subagent spawn depth exceeded (depth={int(spawn_depth)}, max={int(max_spawn_depth)})."
    )


@dataclass(frozen=True, slots=True)
class SpawnDepthDenial:
    spawn_depth: int
    max_spawn_depth: int
    message: str

    @classmethod
    def from_depths(cls, *, spawn_depth: int, max_spawn_depth: int) -> SpawnDepthDenial:
        return cls(
            spawn_depth=int(spawn_depth),
            max_spawn_depth=int(max_spawn_depth),
            message=spawn_depth_denied_message(
                spawn_depth=spawn_depth,
                max_spawn_depth=max_spawn_depth,
            ),
        )


def check_spawn_depth_allowed(
    *,
    spawn_depth: int,
    budget: RunBudget,
    default_max_spawn_depth: int,
) -> SpawnDepthDenial | None:
    max_depth = effective_max_spawn_depth(budget=budget, default=default_max_spawn_depth)
    if not spawn_depth_exceeded(spawn_depth=spawn_depth, max_spawn_depth=max_depth):
        return None
    return SpawnDepthDenial.from_depths(spawn_depth=spawn_depth, max_spawn_depth=max_depth)


__all__ = [
    "SpawnDepthDenial",
    "check_spawn_depth_allowed",
    "effective_max_spawn_depth",
    "spawn_depth_denied_message",
    "spawn_depth_exceeded",
]
