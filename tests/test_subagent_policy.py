from __future__ import annotations

from core.agent.subagent_policy import (
    parent_effective_allow_set,
    resolve_child_allowed_tool_names,
    spawn_policy_denied_message,
)
from core.schemas.harness import RunBudget


def test_explicit_tool_outside_profile_yields_empty_parent_allow() -> None:
    parent = RunBudget(
        policy_profile="background_readonly",
        allowed_tool_names=["write_file"],
    )
    assert parent_effective_allow_set(parent_budget=parent) == frozenset()


def test_parent_readonly_allow_set() -> None:
    parent = RunBudget(policy_profile="background_readonly")
    allowed = parent_effective_allow_set(parent_budget=parent)
    assert "search_files" in allowed
    assert "write_file" not in allowed


def test_child_requested_write_denied_under_readonly_parent() -> None:
    parent = RunBudget(policy_profile="background_readonly")
    child_allowed, denied = resolve_child_allowed_tool_names(
        parent_budget=parent,
        requested_tool_names=["write_file"],
    )
    assert child_allowed == []
    assert denied == ["write_file"]


def test_child_intersects_with_parent_allow_list() -> None:
    parent = RunBudget(
        policy_profile="background_readonly",
        allowed_tool_names=["search_files", "read_file"],
    )
    child_allowed, denied = resolve_child_allowed_tool_names(
        parent_budget=parent,
        requested_tool_names=["search_files", "write_file"],
    )
    assert child_allowed == ["search_files"]
    assert denied == ["write_file"]


def test_spawn_policy_denied_message_lists_parent_allow() -> None:
    message = spawn_policy_denied_message(
        denied_tool_names=["write_file"],
        parent_allow=frozenset({"search_files", "read_file"}),
    )
    assert "write_file" in message
    assert "not allowed by parent run" in message
    assert "search_files" in message
