"""MCP configuration loader."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from core.mcp.config import MCPServerConfig

logger = logging.getLogger(__name__)


def load_mcp_configs(config_path: str | Path | None = None) -> list[MCPServerConfig]:
    """Load MCP server configurations from JSON file.
    
    Args:
        config_path: Path to configuration file. If None, uses default path.
    
    Returns:
        List of MCP server configurations
    
    Raises:
        FileNotFoundError: If config file not found
        ValueError: If config file is invalid
    """
    if config_path is None:
        # Default path: config/mcp_servers.json
        config_path = Path("config") / "mcp_servers.json"
    else:
        config_path = Path(config_path)
    
    if not config_path.exists():
        logger.warning(f"MCP config file not found: {config_path}")
        return []
    
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        if not isinstance(data, dict) or "servers" not in data:
            raise ValueError("Config must contain 'servers' key")
        
        configs: list[MCPServerConfig] = []
        for server_data in data["servers"]:
            config = _parse_server_config(server_data)
            configs.append(config)
        
        logger.info(f"Loaded {len(configs)} MCP server configs from {config_path}")
        return configs
    
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in config file: {exc}") from exc
    except Exception as exc:
        raise ValueError(f"Failed to load MCP config: {exc}") from exc


def _parse_server_config(data: dict[str, Any]) -> MCPServerConfig:
    """Parse server configuration from dict.
    
    Args:
        data: Server configuration dict
    
    Returns:
        MCPServerConfig instance
    
    Raises:
        ValueError: If required fields are missing
    """
    required_fields = ["name", "transport"]
    for field in required_fields:
        if field not in data:
            raise ValueError(f"Missing required field: {field}")
    
    return MCPServerConfig(
        name=data["name"],
        transport=data["transport"],
        enabled=data.get("enabled", True),
        command=data.get("command"),
        args=data.get("args", []),
        env=data.get("env", {}),
        url=data.get("url"),
        headers=data.get("headers", {}),
        timeout=data.get("timeout", 30.0),
        retry_on_failure=data.get("retry_on_failure", True),
        max_retries=data.get("max_retries", 3),
    )
