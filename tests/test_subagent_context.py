from __future__ import annotations

from unittest.mock import MagicMock

from core.agent.subagent_context import (
    normalize_context_mode,
    resolve_subagent_history_session_id,
)
from core.agent.subagent_spawner import SubagentSpawner
from core.schemas.subagent import SUBAGENT_SESSION_KIND


def test_normalize_context_mode_aliases() -> None:
    assert normalize_context_mode("forked") == "forked"
    assert normalize_context_mode("shared") == "forked"
    assert normalize_context_mode("ISOLATED") == "isolated"
    assert normalize_context_mode("unknown-mode") == "isolated"


def test_resolve_subagent_history_session_id_forked() -> None:
    session_repository = MagicMock()
    session_repository.get.return_value = MagicMock(
        session_kind=SUBAGENT_SESSION_KIND,
        parent_session_id="session-parent",
        metadata_json={"context_mode": "forked"},
    )
    assert (
        resolve_subagent_history_session_id(
            session_id="session-child",
            session_repository=session_repository,
        )
        == "session-parent"
    )


def test_resolve_subagent_history_session_id_isolated() -> None:
    session_repository = MagicMock()
    session_repository.get.return_value = MagicMock(
        session_kind=SUBAGENT_SESSION_KIND,
        parent_session_id="session-parent",
        metadata_json={"context_mode": "isolated"},
    )
    assert (
        resolve_subagent_history_session_id(
            session_id="session-child",
            session_repository=session_repository,
        )
        == "session-child"
    )


def test_child_context_snapshot_forked_copies_parent_events() -> None:
    from core.agent.runtime_state import EventSnapshot, RuntimeContextSnapshot

    parent = RuntimeContextSnapshot(
        recent_events=[
            EventSnapshot(
                source_event_id="evt-1",
                event_type="message",
                channel="cli",
                raw_text="Remember codeword ALPHA",
            )
        ]
    )
    child = SubagentSpawner._child_context_snapshot(
        child_session_id="child-1",
        parent_snapshot=parent,
        context_mode="forked",
    )
    assert child.session_metadata.get("context_mode") == "forked"
    assert len(child.recent_events) == 1
    assert "ALPHA" in child.recent_events[0].raw_text
