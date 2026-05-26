"""End-to-end MCP tool policy checks through the harness policy guard."""

from __future__ import annotations

from core.agent.tool_policy_engine import (
    ToolPolicyEngine,
    effective_allowed_tool_names,
    effective_denied_tool_names,
)
from core.schemas.harness import RunBudget
from core.schemas.tool_policy import ToolPolicyContext


def _evaluate(policy_profile: str, tool_name: str):
    budget = RunBudget(policy_profile=policy_profile)
    return ToolPolicyEngine().evaluate(
        ToolPolicyContext(
            tool_name=tool_name,
            policy_profile=policy_profile,
            allowed_tool_names=effective_allowed_tool_names(budget),
            denied_tool_names=effective_denied_tool_names(budget),
        )
    )


def test_wechat_safe_mcp_delete_is_denied() -> None:
    decision = _evaluate("wechat_safe", "mcp_server_delete")
    assert decision.decision == "deny"
    assert decision.reason == "mcp_pattern_denied"


def test_wechat_safe_mcp_echo_requires_confirmation() -> None:
    decision = _evaluate("wechat_safe", "mcp_server_echo")
    assert decision.decision == "ask"
    assert decision.reason == "mcp_default_policy_ask"
    assert decision.requires_confirmation is True


def test_background_readonly_mcp_write_is_denied() -> None:
    decision = _evaluate("background_readonly", "mcp_server_write")
    assert decision.decision == "deny"
    assert decision.reason == "mcp_default_policy_deny"


def test_background_readonly_mcp_query_is_allowed() -> None:
    decision = _evaluate("background_readonly", "mcp_db_query")
    assert decision.decision == "allow"
