from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ToolPlan:
    tool: str
    arguments: dict[str, Any]
    reason: str
    source: str = "llm"
