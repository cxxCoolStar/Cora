from __future__ import annotations

import json
import re
from typing import Any


_JSON_FENCE_PATTERN = re.compile(
    r"```(?:json)?\s*([\s\S]*?)\s*```",
    re.IGNORECASE,
)


def parse_plan_json_from_text(text: str) -> dict[str, Any]:
    normalized = str(text or "").strip()
    if not normalized:
        raise ValueError("Planner output is empty.")
    fence_match = _JSON_FENCE_PATTERN.search(normalized)
    if fence_match is not None:
        normalized = fence_match.group(1).strip()
    try:
        payload = json.loads(normalized)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Planner output is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Planner output must be a JSON object.")
    return payload


__all__ = ["parse_plan_json_from_text"]
