from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from core.schemas.subagent import SUBAGENT_SESSION_KIND

if TYPE_CHECKING:
    from core.storage.repositories import SessionRepository

SubagentContextMode = Literal["isolated", "forked"]

VALID_SUBAGENT_CONTEXT_MODES = frozenset({"isolated", "forked"})
_CONTEXT_MODE_ALIASES = {
    "shared": "forked",
    "inherit": "forked",
    "parent": "forked",
}


def normalize_context_mode(value: str | None) -> SubagentContextMode:
    mode = str(value or "isolated").strip().lower()
    mode = _CONTEXT_MODE_ALIASES.get(mode, mode)
    if mode not in VALID_SUBAGENT_CONTEXT_MODES:
        return "isolated"
    return mode  # type: ignore[return-value]


def resolve_subagent_history_session_id(
    *,
    session_id: str,
    session_repository: SessionRepository,
) -> str:
    """Return the session whose messages should seed a subagent turn."""
    try:
        record = session_repository.get(session_id)
    except KeyError:
        return session_id
    if str(record.session_kind or "").strip() != SUBAGENT_SESSION_KIND:
        return session_id
    metadata = dict(record.metadata_json or {})
    if normalize_context_mode(metadata.get("context_mode")) != "forked":
        return session_id
    parent_session_id = str(
        record.parent_session_id or metadata.get("parent_session_id") or ""
    ).strip()
    return parent_session_id or session_id


__all__ = [
    "VALID_SUBAGENT_CONTEXT_MODES",
    "normalize_context_mode",
    "resolve_subagent_history_session_id",
]
