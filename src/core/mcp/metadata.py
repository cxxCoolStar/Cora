from __future__ import annotations

import fnmatch
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.mcp.schema import MCPToolSchema

logger = logging.getLogger(__name__)

DEFAULT_METADATA_PATH = Path("config") / "mcp_tool_metadata.json"


@dataclass(frozen=True, slots=True)
class MCPToolMetadata:
    is_mutating: bool = False
    is_idempotent: bool = False
    idempotency_key_extractor: str | None = None


_METADATA_ENTRIES: list[tuple[str, bool, MCPToolMetadata]] = []
_EVAL_OVERRIDES: dict[str, MCPToolMetadata] = {}


def _parse_entry(name: str, raw: dict[str, Any]) -> MCPToolMetadata:
    return MCPToolMetadata(
        is_mutating=bool(raw.get("is_mutating", False)),
        is_idempotent=bool(raw.get("is_idempotent", False)),
        idempotency_key_extractor=str(raw.get("idempotency_key_extractor") or "").strip() or None,
    )


def load_mcp_tool_metadata(config_path: str | Path | None = None) -> None:
    """Load MCP tool metadata entries from JSON config."""
    global _METADATA_ENTRIES
    path = Path(config_path) if config_path is not None else DEFAULT_METADATA_PATH
    if not path.is_file():
        logger.debug("MCP tool metadata file not found: %s", path)
        _METADATA_ENTRIES = []
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("failed to load MCP tool metadata from %s", path)
        _METADATA_ENTRIES = []
        return
    tools = payload.get("tools") if isinstance(payload, dict) else None
    if not isinstance(tools, dict):
        _METADATA_ENTRIES = []
        return
    entries: list[tuple[str, bool, MCPToolMetadata]] = []
    for name, raw in tools.items():
        if not isinstance(raw, dict):
            continue
        normalized = str(name or "").strip()
        if not normalized:
            continue
        entries.append((normalized, bool(raw.get("pattern", False)), _parse_entry(normalized, raw)))
    _METADATA_ENTRIES = entries
    logger.info("loaded %s MCP tool metadata entries from %s", len(entries), path)


def set_eval_metadata_overrides(overrides: dict[str, dict[str, Any]] | None) -> None:
    """Apply eval-case metadata overrides (exact tool names only)."""
    global _EVAL_OVERRIDES
    if not overrides:
        _EVAL_OVERRIDES = {}
        return
    parsed: dict[str, MCPToolMetadata] = {}
    for name, raw in overrides.items():
        if not isinstance(raw, dict):
            continue
        normalized = str(name or "").strip()
        if not normalized:
            continue
        parsed[normalized] = _parse_entry(normalized, raw)
    _EVAL_OVERRIDES = parsed


def resolve_mcp_tool_metadata(tool_name: str) -> MCPToolMetadata | None:
    if not str(tool_name or "").startswith("mcp_"):
        return None
    override = _EVAL_OVERRIDES.get(tool_name)
    if override is not None:
        return override
    for pattern, is_pattern, metadata in _METADATA_ENTRIES:
        if is_pattern:
            if fnmatch.fnmatch(tool_name, pattern):
                return metadata
        elif tool_name == pattern:
            return metadata
    return None


def extract_semantic_target(*, extractor: str | None, arguments: dict[str, Any]) -> Any:
    normalized = str(extractor or "").strip()
    if not normalized.startswith("args."):
        return None
    key = normalized[5:].strip()
    if not key:
        return None
    return arguments.get(key)


def apply_metadata_to_tools(tools: dict[str, MCPToolSchema]) -> None:
    for tool_name, schema in tools.items():
        metadata = resolve_mcp_tool_metadata(tool_name)
        if metadata is None:
            continue
        schema.is_mutating = metadata.is_mutating
        schema.idempotency_key_extractor = metadata.idempotency_key_extractor


def metadata_as_mutating_dict(tool_name: str) -> dict[str, Any] | None:
    """Shape compatible with core.agent.idempotency.MUTATING_TOOLS entries."""
    metadata = resolve_mcp_tool_metadata(tool_name)
    if metadata is None or not metadata.is_mutating:
        return None
    extractor = metadata.idempotency_key_extractor

    def _extractor(arguments: dict[str, Any]) -> Any:
        return extract_semantic_target(extractor=extractor, arguments=arguments)

    return {
        "is_mutating": True,
        "is_idempotent": metadata.is_idempotent,
        "semantic_target_extractor": _extractor,
    }


__all__ = [
    "DEFAULT_METADATA_PATH",
    "MCPToolMetadata",
    "apply_metadata_to_tools",
    "extract_semantic_target",
    "load_mcp_tool_metadata",
    "metadata_as_mutating_dict",
    "resolve_mcp_tool_metadata",
    "set_eval_metadata_overrides",
]
