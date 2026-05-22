from __future__ import annotations

from core.schemas.harness import RunBudget


def run_budget_from_dict(payload: dict | None) -> RunBudget:
    if not isinstance(payload, dict):
        return RunBudget()
    return RunBudget(
        policy_profile=_maybe_str(payload.get("policy_profile")),
        max_steps=_maybe_int(payload.get("max_steps")),
        timeout_seconds=_maybe_float(payload.get("timeout_seconds")),
        max_tool_calls=_maybe_int(payload.get("max_tool_calls")),
        max_spawn_depth=_maybe_int(payload.get("max_spawn_depth")),
        max_child_runs=_maybe_int(payload.get("max_child_runs")),
        allowed_tool_names=_string_list(payload.get("allowed_tool_names")),
        denied_tool_names=_string_list(payload.get("denied_tool_names")),
        approved_tool_names=_string_list(payload.get("approved_tool_names")),
    )


def _maybe_str(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _maybe_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _maybe_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


__all__ = ["run_budget_from_dict"]
