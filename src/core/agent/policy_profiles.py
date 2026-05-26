from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True, slots=True)
class HarnessPolicyProfile:
    name: str
    allowed_tool_names: tuple[str, ...] = field(default_factory=tuple)
    denied_tool_names: tuple[str, ...] = field(default_factory=tuple)
    max_tool_calls: int | None = None
    
    # MCP-specific policies
    mcp_default_policy: Literal["allow", "ask", "deny"] | None = None
    """Default policy for all MCP tools (if not matched by other rules)"""
    
    mcp_allowed_patterns: tuple[str, ...] = field(default_factory=tuple)
    """Wildcard patterns for allowed MCP tools (e.g., 'mcp_database_*')"""
    
    mcp_denied_patterns: tuple[str, ...] = field(default_factory=tuple)
    """Wildcard patterns for denied MCP tools (e.g., 'mcp_aws_*')"""
    
    mcp_ask_patterns: tuple[str, ...] = field(default_factory=tuple)
    """Wildcard patterns for MCP tools requiring HITL (e.g., 'mcp_*_write')"""


HARNESS_POLICY_PROFILES: dict[str, HarnessPolicyProfile] = {
    "wechat_safe": HarnessPolicyProfile(
        name="wechat_safe",
        denied_tool_names=("shell_exec", "browser_navigate", "browser_click", "browser_type", "browser_back"),
        mcp_default_policy="ask",  # All MCP tools require confirmation by default
        mcp_denied_patterns=("mcp_*_shell", "mcp_*_exec", "mcp_*_delete"),  # Deny dangerous operations
    ),
    "background_readonly": HarnessPolicyProfile(
        name="background_readonly",
        allowed_tool_names=(
            "list_files",
            "search_files",
            "read_file",
            "web_search",
            "web_fetch",
            "skills_list",
            "skill_view",
            "skill_run",
            "search_sessions",
        ),
        mcp_allowed_patterns=("mcp_*_read", "mcp_*_query", "mcp_*_list", "mcp_*_search"),  # Allow read operations
        mcp_default_policy="deny",  # Deny all other MCP tools
    ),
    "planner_readonly": HarnessPolicyProfile(
        name="planner_readonly",
        allowed_tool_names=(
            "list_files",
            "search_files",
            "read_file",
            "web_search",
            "web_fetch",
            "skills_list",
            "skill_view",
            "search_sessions",
        ),
        mcp_allowed_patterns=("mcp_*_read", "mcp_*_query", "mcp_*_list"),  # Allow read operations
        mcp_default_policy="deny",  # Deny all other MCP tools
    ),
    "coding_full": HarnessPolicyProfile(
        name="coding_full",
        mcp_default_policy="allow",  # Allow all MCP tools
    ),
}


def get_harness_policy_profile(name: str | None) -> HarnessPolicyProfile | None:
    normalized_name = str(name or "").strip()
    if not normalized_name:
        return None
    return HARNESS_POLICY_PROFILES.get(normalized_name)


__all__ = [
    "HARNESS_POLICY_PROFILES",
    "HarnessPolicyProfile",
    "get_harness_policy_profile",
]
