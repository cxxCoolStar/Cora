from __future__ import annotations

import json
import re
from typing import Any


_JSON_FENCE_PATTERN = re.compile(
    r"```(?:json)?\s*([\s\S]*?)\s*```",
    re.IGNORECASE,
)


def _extract_json_object_candidates(text: str) -> list[str]:
    normalized = str(text or "").strip()
    if not normalized:
        return []
    candidates: list[str] = []
    fence_match = _JSON_FENCE_PATTERN.search(normalized)
    if fence_match is not None:
        candidates.append(fence_match.group(1).strip())
    candidates.append(normalized)
    start = normalized.find("{")
    while start >= 0:
        depth = 0
        for index in range(start, len(normalized)):
            char = normalized[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(normalized[start : index + 1])
                    break
        start = normalized.find("{", start + 1)
    seen: set[str] = set()
    ordered: list[str] = []
    for candidate in candidates:
        key = candidate.strip()
        if key and key not in seen:
            seen.add(key)
            ordered.append(key)
    return ordered


def parse_plan_json_from_text(text: str) -> dict[str, Any]:
    candidates = _extract_json_object_candidates(text)
    if not candidates:
        raise ValueError("Planner output is empty.")
    errors: list[str] = []
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError as exc:
            errors.append(str(exc))
            continue
        if isinstance(payload, dict):
            return payload
        errors.append("payload is not a JSON object")
    detail = errors[0] if errors else "no JSON object found"
    raise ValueError(f"Planner output is not valid JSON: {detail}")


__all__ = ["parse_plan_json_from_text"]
