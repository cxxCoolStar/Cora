from __future__ import annotations

from core.agent.policy_profiles import get_harness_policy_profile
from core.agent.sandbox_runtime import MUTATING_TOOLS_IN_SANDBOX
from core.agent.tool_policy import (
    ToolPolicyDecision,
    ToolRisk,
    allow_tool_policy_decision,
    ask_tool_policy_decision,
    deny_tool_policy_decision,
    deny_max_tool_calls_decision,
    sandbox_tool_policy_decision,
)
from core.schemas.harness import RunBudget
from core.schemas.tool_policy import ToolPolicyContext


def normalize_tool_risk(value: object) -> ToolRisk:
    text = str(value or "medium").strip().lower()
    if text in {"low", "medium", "high"}:
        return text  # type: ignore[return-value]
    return "medium"


def normalize_tool_names(values: list[str]) -> list[str]:
    names: list[str] = []
    for value in values:
        name = str(value or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def effective_allowed_tool_names(budget: RunBudget) -> frozenset[str]:
    profile = get_harness_policy_profile(budget.policy_profile)
    profile_names = list(profile.allowed_tool_names) if profile is not None else []
    explicit_names = normalize_tool_names(budget.allowed_tool_names)
    if not profile_names:
        return frozenset(explicit_names)
    if not explicit_names:
        return frozenset(normalize_tool_names(profile_names))
    explicit_set = set(explicit_names)
    return frozenset(
        name for name in normalize_tool_names(profile_names) if name in explicit_set
    )


def effective_denied_tool_names(budget: RunBudget) -> frozenset[str]:
    profile = get_harness_policy_profile(budget.policy_profile)
    names = list(profile.denied_tool_names) if profile is not None else []
    names.extend(budget.denied_tool_names)
    return frozenset(normalize_tool_names(names))


def effective_max_tool_calls(budget: RunBudget) -> int | None:
    profile = get_harness_policy_profile(budget.policy_profile)
    if budget.max_tool_calls is not None:
        return budget.max_tool_calls
    if profile is not None:
        return profile.max_tool_calls
    return None


def has_runtime_tool_governance(budget: RunBudget) -> bool:
    return (
        effective_max_tool_calls(budget) is not None
        or bool(effective_allowed_tool_names(budget))
        or bool(effective_denied_tool_names(budget))
    )


def resolve_platform_name(value: object) -> str | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text in {"weixin", "wechat"}:
        return "wechat"
    if text in {"cli", "api", "http", "tui"}:
        return text
    return text


def requires_sandbox_execution(context: ToolPolicyContext) -> bool:
    if context.requires_sandbox:
        return True
    platform = resolve_platform_name(context.platform)
    if platform == "wechat" and context.tool_name in MUTATING_TOOLS_IN_SANDBOX:
        return True
    return False


def requires_hitl_confirmation(context: ToolPolicyContext) -> bool:
    if not context.requires_confirmation:
        return False
    if context.background_execution:
        return False
    if context.tool_name in context.approved_tool_names:
        return False
    risk = normalize_tool_risk(context.tool_risk)
    if risk not in {"medium", "high"}:
        return False
    platform = resolve_platform_name(context.platform)
    if platform == "cli":
        return False
    return True


def should_expose_tool(
    *,
    tool_name: str,
    budget: RunBudget,
) -> bool:
    allowed_tool_names = effective_allowed_tool_names(budget)
    denied_tool_names = effective_denied_tool_names(budget)
    if not allowed_tool_names and not denied_tool_names:
        return True
    if allowed_tool_names and tool_name not in allowed_tool_names:
        return False
    if tool_name in denied_tool_names:
        return False
    return True


class ToolPolicyEngine:
    def evaluate(self, context: ToolPolicyContext) -> ToolPolicyDecision:
        audit_metadata = {
            "run_allowed_tool_names": sorted(context.allowed_tool_names),
            "run_denied_tool_names": sorted(context.denied_tool_names),
            "agent_role": context.agent_role,
        }
        if context.platform:
            audit_metadata["platform"] = context.platform
        if context.session_kind:
            audit_metadata["session_kind"] = context.session_kind
        if context.background_execution:
            audit_metadata["background_execution"] = True

        if context.max_tool_calls is not None:
            max_tool_calls = max(0, int(context.max_tool_calls))
            if context.tool_calls_so_far >= max_tool_calls:
                return deny_max_tool_calls_decision(
                    tool_name=context.tool_name,
                    max_tool_calls=max_tool_calls,
                    policy_profile=context.policy_profile,
                    risk=normalize_tool_risk(context.tool_risk),
                    audit_metadata={**audit_metadata, "max_tool_calls": max_tool_calls},
                )

        if context.allowed_roles and context.agent_role not in context.allowed_roles:
            return deny_tool_policy_decision(
                tool_name=context.tool_name,
                reason="role_not_allowed",
                policy_profile=context.policy_profile,
                risk=normalize_tool_risk(context.tool_risk),
                audit_metadata={
                    **audit_metadata,
                    "allowed_roles": sorted(context.allowed_roles),
                },
            )

        if context.allowed_tool_names and context.tool_name not in context.allowed_tool_names:
            return deny_tool_policy_decision(
                tool_name=context.tool_name,
                reason="tool_not_allowed",
                policy_profile=context.policy_profile,
                risk=normalize_tool_risk(context.tool_risk),
                audit_metadata=audit_metadata,
            )

        if context.tool_name in context.denied_tool_names:
            return deny_tool_policy_decision(
                tool_name=context.tool_name,
                reason="tool_denied",
                policy_profile=context.policy_profile,
                risk=normalize_tool_risk(context.tool_risk),
                audit_metadata=audit_metadata,
            )

        if requires_hitl_confirmation(context):
            return ask_tool_policy_decision(
                tool_name=context.tool_name,
                policy_profile=context.policy_profile,
                risk=normalize_tool_risk(context.tool_risk),
                requires_confirmation=True,
                requires_sandbox=context.requires_sandbox,
                audit_metadata={
                    **audit_metadata,
                    "platform": resolve_platform_name(context.platform),
                },
            )

        if requires_sandbox_execution(context):
            return sandbox_tool_policy_decision(
                tool_name=context.tool_name,
                policy_profile=context.policy_profile,
                risk=normalize_tool_risk(context.tool_risk),
                requires_sandbox=True,
                audit_metadata={
                    **audit_metadata,
                    "platform": resolve_platform_name(context.platform),
                },
            )

        return allow_tool_policy_decision(
            tool_name=context.tool_name,
            policy_profile=context.policy_profile,
            risk=normalize_tool_risk(context.tool_risk),
            requires_confirmation=context.requires_confirmation,
            requires_sandbox=context.requires_sandbox,
            audit_metadata=audit_metadata,
        )


__all__ = [
    "ToolPolicyEngine",
    "effective_allowed_tool_names",
    "effective_denied_tool_names",
    "effective_max_tool_calls",
    "has_runtime_tool_governance",
    "normalize_tool_risk",
    "normalize_tool_names",
    "requires_hitl_confirmation",
    "requires_sandbox_execution",
    "resolve_platform_name",
    "should_expose_tool",
]
