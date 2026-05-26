"""Idempotency key generation and mutating tool metadata for plan resume."""

from __future__ import annotations

from typing import Any, Callable


# Mutating tool metadata
# Only mutating operations need idempotency keys to prevent duplicate execution on resume
MUTATING_TOOLS: dict[str, dict[str, Any]] = {
    "write_file": {
        "is_mutating": True,
        "is_idempotent": True,  # Overwrite is idempotent
        "semantic_target_extractor": lambda args: args.get("path"),
    },
    "append_to_file": {
        "is_mutating": True,
        "is_idempotent": False,  # Append is NOT idempotent
        "semantic_target_extractor": lambda args: args.get("path"),
    },
    "delete_item": {
        "is_mutating": True,
        "is_idempotent": True,  # Delete is idempotent (deleting non-existent item is safe)
        "semantic_target_extractor": lambda args: args.get("item_id"),
    },
    "send_files_to_user": {
        "is_mutating": True,
        "is_idempotent": False,  # Sending multiple times creates duplicates
        "semantic_target_extractor": lambda args: str(args.get("item_ids", [])),
    },
    "run_terminal_command": {
        "is_mutating": True,
        "is_idempotent": False,  # Commands may have side effects
        "semantic_target_extractor": lambda args: args.get("command", "")[:50],
    },
}


def _resolve_tool_metadata(tool_name: str) -> dict[str, Any] | None:
    builtin = MUTATING_TOOLS.get(tool_name)
    if builtin is not None:
        return builtin
    try:
        from core.mcp.metadata import metadata_as_mutating_dict

        return metadata_as_mutating_dict(tool_name)
    except Exception:
        return None


def generate_idempotency_key(
    *,
    run_id: str,
    task_id: str,
    tool_name: str,
    tool_arguments: dict[str, Any],
) -> str | None:
    """
    Generate an idempotency key for a mutating tool operation.
    
    Returns None if the tool is not mutating or if semantic target cannot be extracted.
    
    Format: {run_id}:{task_id}:{tool_name}:{semantic_target}
    
    Example: "run-abc123:task-2:write_file:config.py"
    """
    tool_meta = _resolve_tool_metadata(tool_name)
    if tool_meta is None:
        return None  # Not a mutating tool
    
    extractor: Callable[[dict[str, Any]], Any] = tool_meta["semantic_target_extractor"]
    target = extractor(tool_arguments)
    if not target:
        return None  # Cannot extract semantic target
    
    # Normalize target to string
    target_str = str(target).strip()
    if not target_str:
        return None
    
    return f"{run_id}:{task_id}:{tool_name}:{target_str}"


def is_tool_idempotent(tool_name: str) -> bool:
    """Check if a mutating tool is idempotent (safe to retry)."""
    tool_meta = _resolve_tool_metadata(tool_name)
    if tool_meta is None:
        return True  # Non-mutating tools are considered idempotent
    return bool(tool_meta.get("is_idempotent", False))


__all__ = [
    "MUTATING_TOOLS",
    "generate_idempotency_key",
    "is_tool_idempotent",
]
