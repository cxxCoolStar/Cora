from __future__ import annotations

from typing import Literal

PlanCommand = Literal["plan", "execute"]


def parse_plan_command(text: str | None) -> PlanCommand | None:
    normalized = str(text or "").strip()
    if not normalized:
        return None
    lowered = normalized.lower()
    if lowered == "/execute" or lowered.startswith("/execute "):
        return "execute"
    if lowered.startswith("/plan"):
        return "plan"
    return None


def plan_command_text(text: str | None) -> str:
    normalized = str(text or "").strip()
    if normalized.lower().startswith("/plan"):
        return normalized[5:].strip()
    return normalized


__all__ = ["PlanCommand", "parse_plan_command", "plan_command_text"]
