from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


ToolPolicyDecisionKind = Literal["allow", "ask", "deny", "sandbox"]
ToolRisk = Literal["low", "medium", "high"]


@dataclass(frozen=True, slots=True)
class ToolPolicyDecision:
    decision: ToolPolicyDecisionKind
    reason: str
    tool_name: str
    risk: ToolRisk = "medium"
    policy_profile: str | None = None
    requires_confirmation: bool = False
    requires_sandbox: bool = False
    safe_user_message: str = ""
    audit_metadata: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "decision": self.decision,
            "reason": self.reason,
            "tool_name": self.tool_name,
            "risk": self.risk,
            "policy_profile": self.policy_profile,
            "requires_confirmation": self.requires_confirmation,
            "requires_sandbox": self.requires_sandbox,
            "safe_user_message": self.safe_user_message,
            "audit_metadata": dict(self.audit_metadata),
        }


def allow_tool_policy_decision(
    *,
    tool_name: str,
    reason: str = "tool_allowed",
    policy_profile: str | None = None,
    risk: ToolRisk = "medium",
    requires_confirmation: bool = False,
    requires_sandbox: bool = False,
    audit_metadata: dict[str, object] | None = None,
) -> ToolPolicyDecision:
    return ToolPolicyDecision(
        decision="allow",
        reason=reason,
        tool_name=tool_name,
        risk=risk,
        policy_profile=policy_profile,
        requires_confirmation=requires_confirmation,
        requires_sandbox=requires_sandbox,
        audit_metadata=dict(audit_metadata or {}),
    )


def deny_tool_policy_decision(
    *,
    tool_name: str,
    reason: str,
    policy_profile: str | None = None,
    risk: ToolRisk = "medium",
    audit_metadata: dict[str, object] | None = None,
) -> ToolPolicyDecision:
    return ToolPolicyDecision(
        decision="deny",
        reason=reason,
        tool_name=tool_name,
        risk=risk,
        policy_profile=policy_profile,
        safe_user_message=f"Tool `{tool_name}` is not allowed by this run's harness policy.",
        audit_metadata=dict(audit_metadata or {}),
    )


def ask_tool_policy_decision(
    *,
    tool_name: str,
    reason: str = "confirmation_required",
    policy_profile: str | None = None,
    risk: ToolRisk = "medium",
    requires_confirmation: bool = True,
    requires_sandbox: bool = False,
    audit_metadata: dict[str, object] | None = None,
) -> ToolPolicyDecision:
    return ToolPolicyDecision(
        decision="ask",
        reason=reason,
        tool_name=tool_name,
        risk=risk,
        policy_profile=policy_profile,
        requires_confirmation=requires_confirmation,
        requires_sandbox=requires_sandbox,
        safe_user_message=(
            f"This action needs your confirmation before I run `{tool_name}`."
        ),
        audit_metadata=dict(audit_metadata or {}),
    )


def deny_max_tool_calls_decision(
    *,
    tool_name: str,
    max_tool_calls: int,
    policy_profile: str | None = None,
    risk: ToolRisk = "medium",
    audit_metadata: dict[str, object] | None = None,
) -> ToolPolicyDecision:
    return ToolPolicyDecision(
        decision="deny",
        reason="max_tool_calls_exceeded",
        tool_name=tool_name,
        risk=risk,
        policy_profile=policy_profile,
        safe_user_message=(
            f"Tool call budget exceeded after {max_tool_calls} allowed call(s). "
            f"`{tool_name}` was not executed."
        ),
        audit_metadata=dict(audit_metadata or {}),
    )


__all__ = [
    "ToolPolicyDecision",
    "ToolPolicyDecisionKind",
    "ToolRisk",
    "allow_tool_policy_decision",
    "ask_tool_policy_decision",
    "deny_tool_policy_decision",
    "deny_max_tool_calls_decision",
]
