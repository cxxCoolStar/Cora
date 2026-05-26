"""MCP runtime helpers for connecting servers at application startup."""

from __future__ import annotations

import logging
from pathlib import Path

from core.mcp.config_loader import load_mcp_configs
from core.mcp.manager import MCPClientManager

logger = logging.getLogger(__name__)


async def create_mcp_manager(
    *,
    enabled: bool,
    config_path: Path | None = None,
) -> MCPClientManager | None:
    """Connect to enabled MCP servers and return a manager, or None if disabled."""
    if not enabled:
        return None

    configs = load_mcp_configs(config_path)
    active_configs = [config for config in configs if config.enabled]
    if not active_configs:
        logger.info("MCP enabled but no active servers found in config")
        return None

    manager = MCPClientManager(active_configs)
    await manager.connect_all()
    logger.info("Connected to %s MCP server(s), %s tool(s)", len(manager.clients), len(manager.all_tools))
    return manager


async def disconnect_mcp_manager(manager: MCPClientManager | None) -> None:
    if manager is None:
        return
    await manager.disconnect_all()
