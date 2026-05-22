from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ToolPolicyContext:
    tool_name: str
    agent_role: str = "primary"
    platform: str | None = None
    policy_profile: str | None = None
    allowed_tool_names: frozenset[str] = field(default_factory=frozenset)
    denied_tool_names: frozenset[str] = field(default_factory=frozenset)
    tool_risk: str = "medium"
    requires_confirmation: bool = False
    requires_sandbox: bool = False
    allowed_roles: frozenset[str] = field(default_factory=frozenset)
    max_tool_calls: int | None = None
    tool_calls_so_far: int = 0
    session_kind: str | None = None
    background_execution: bool = False
    approved_tool_names: frozenset[str] = field(default_factory=frozenset)
