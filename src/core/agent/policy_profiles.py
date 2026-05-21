from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class HarnessPolicyProfile:
    name: str
    allowed_tool_names: tuple[str, ...] = field(default_factory=tuple)
    denied_tool_names: tuple[str, ...] = field(default_factory=tuple)
    max_tool_calls: int | None = None


HARNESS_POLICY_PROFILES: dict[str, HarnessPolicyProfile] = {
    "wechat_safe": HarnessPolicyProfile(
        name="wechat_safe",
        denied_tool_names=("shell_exec", "browser_navigate", "browser_click", "browser_type", "browser_back"),
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
    ),
    "coding_full": HarnessPolicyProfile(name="coding_full"),
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
