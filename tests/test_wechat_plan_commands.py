from __future__ import annotations

from core.channels.wechat.plan_commands import parse_plan_command, plan_command_text


def test_parse_plan_command_detects_plan_and_execute() -> None:
    assert parse_plan_command("/plan find files") == "plan"
    assert parse_plan_command("/PLAN x") == "plan"
    assert parse_plan_command("/execute") == "execute"
    assert parse_plan_command("/execute now") == "execute"
    assert parse_plan_command("hello") is None


def test_plan_command_text_strips_prefix() -> None:
    assert plan_command_text("/plan 帮我在 src 里找 hello_agent") == "帮我在 src 里找 hello_agent"
    assert plan_command_text("/plan") == ""
