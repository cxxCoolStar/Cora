from __future__ import annotations

import pytest

from core.agent.plan_parser import parse_plan_json_from_text


def test_parse_plan_json_from_markdown_fence() -> None:
    text = '说明如下：\n```json\n{"plan_id": "p1", "session_id": "s1", "goal": "g", "tasks": []}\n```'
    payload = parse_plan_json_from_text(text)
    assert payload["plan_id"] == "p1"


def test_parse_plan_json_from_embedded_object() -> None:
    text = '这是计划：{"plan_id": "p2", "session_id": "s1", "goal": "find files", "tasks": [{"task_id": "t1", "title": "x", "tool_names": ["search_files"], "instruction": "search"}]} 请执行。'
    payload = parse_plan_json_from_text(text)
    assert payload["tasks"][0]["tool_names"] == ["search_files"]


def test_parse_plan_json_rejects_empty() -> None:
    with pytest.raises(ValueError, match="empty"):
        parse_plan_json_from_text("   ")
