"""MCP (Model Context Protocol) integration module."""

from core.mcp.client import MCPClient
from core.mcp.config import MCPServerConfig
from core.mcp.config_loader import load_mcp_configs
from core.mcp.manager import MCPClientManager
from core.mcp.runtime import create_mcp_manager, disconnect_mcp_manager
from core.mcp.schema import MCPToolResult, MCPToolSchema
from core.mcp.tool_adapter import MCPToolAdapter

__all__ = [
    "MCPClient",
    "MCPClientManager",
    "MCPServerConfig",
    "MCPToolAdapter",
    "MCPToolResult",
    "MCPToolSchema",
    "create_mcp_manager",
    "disconnect_mcp_manager",
    "load_mcp_configs",
]
