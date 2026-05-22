from __future__ import annotations

from core.agent.policy_profiles import get_harness_policy_profile
from core.agent.tool_policy_engine import (
    effective_allowed_tool_names,
    effective_denied_tool_names,
    normalize_tool_names,
)
from core.schemas.harness import RunBudget


def parent_effective_allow_set(
    *,
    parent_budget: RunBudget,
    registered_tool_names: list[str] | None = None,
) -> frozenset[str]:
    profile = get_harness_policy_profile(parent_budget.policy_profile)
    profile_allow = list(profile.allowed_tool_names) if profile is not None else []
    explicit_allow = normalize_tool_names(parent_budget.allowed_tool_names)
    if profile_allow:
        if explicit_allow:
            return frozenset(name for name in profile_allow if name in explicit_allow)
        return frozenset(normalize_tool_names(profile_allow))

    allowed = effective_allowed_tool_names(parent_budget)
    if allowed:
        return allowed

    denied = effective_denied_tool_names(parent_budget)
    registry = frozenset(normalize_tool_names(list(registered_tool_names or [])))
    if denied and registry:
        return frozenset(name for name in registry if name not in denied)
    if registry:
        return registry
    return frozenset()


def resolve_child_allowed_tool_names(
    *,
    parent_budget: RunBudget,
    requested_tool_names: list[str],
    registered_tool_names: list[str] | None = None,
) -> tuple[list[str], list[str]]:
    parent_allow = parent_effective_allow_set(
        parent_budget=parent_budget,
        registered_tool_names=registered_tool_names,
    )
    requested = normalize_tool_names(requested_tool_names)
    if not requested:
        return sorted(parent_allow), []
    allowed = [name for name in requested if name in parent_allow]
    denied = [name for name in requested if name not in parent_allow]
    return allowed, denied


def spawn_policy_denied_message(*, denied_tool_names: list[str], parent_allow: frozenset[str]) -> str:
    denied_text = ", ".join(denied_tool_names)
    if parent_allow:
        allow_text = ", ".join(sorted(parent_allow))
        return (
            f"Subagent tool policy denied: {denied_text} is not allowed by parent run "
            f"(allowed: {allow_text})."
        )
    return f"Subagent tool policy denied: {denied_text} is not allowed by parent run."


__all__ = [
    "parent_effective_allow_set",
    "resolve_child_allowed_tool_names",
    "spawn_policy_denied_message",
]
