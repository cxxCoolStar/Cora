"""MCP Client Manager for managing multiple MCP server connections."""

from __future__ import annotations

import logging
from typing import Any

from core.mcp.client import MCPClient
from core.mcp.config import MCPServerConfig
from core.mcp.schema import MCPToolResult, MCPToolSchema

logger = logging.getLogger(__name__)


class MCPClientManager:
    """Manager for multiple MCP client connections.
    
    Handles connecting to multiple MCP servers, aggregating their tools,
    and routing tool calls to the appropriate server.
    """
    
    def __init__(self, configs: list[MCPServerConfig]):
        """Initialize MCP client manager.
        
        Args:
            configs: List of server configurations
        """
        self.configs = configs
        self.clients: dict[str, MCPClient] = {}
        self.all_tools: dict[str, MCPToolSchema] = {}
    
    async def connect_all(self) -> None:
        """Connect to all enabled MCP servers.
        
        Servers that fail to connect will be skipped if retry_on_failure is False.
        """
        for config in self.configs:
            if not config.enabled:
                logger.info(f"Skipping disabled MCP server {config.name}")
                continue
            
            try:
                config.validate()
                client = MCPClient(config)
                await client.connect()
                self.clients[config.name] = client
                
                # Register tools with prefixed names to avoid conflicts
                for tool_name, tool_schema in client.tools.items():
                    prefixed_name = f"mcp_{config.name}_{tool_name}"
                    self.all_tools[prefixed_name] = tool_schema
                
                logger.info(
                    f"Connected to MCP server {config.name}, "
                    f"registered {len(client.tools)} tools"
                )
            
            except Exception as exc:
                logger.error(f"Failed to connect to MCP server {config.name}: {exc}")
                if not config.retry_on_failure:
                    raise
    
    async def call_tool(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> MCPToolResult:
        """Call an MCP tool.
        
        Args:
            tool_name: Prefixed tool name (mcp_{server}_{tool})
            arguments: Tool arguments
        
        Returns:
            Tool execution result
        
        Raises:
            ValueError: If tool name is invalid or server not connected
        """
        # Parse tool name: mcp_{server_name}_{original_tool_name}
        if not tool_name.startswith("mcp_"):
            raise ValueError(f"Not an MCP tool: {tool_name}")
        
        parts = tool_name.split("_", 2)
        if len(parts) != 3:
            raise ValueError(f"Invalid MCP tool name format: {tool_name}")
        
        server_name = parts[1]
        original_tool_name = parts[2]
        
        client = self.clients.get(server_name)
        if not client:
            raise ValueError(f"MCP server {server_name} not connected")
        
        return await client.call_tool(
            tool_name=original_tool_name,
            arguments=arguments,
        )
    
    def get_all_tool_specs(self) -> list[dict[str, Any]]:
        """Get all MCP tool specifications for LLM.
        
        Returns:
            List of tool specs in OpenAI function calling format
        """
        return [tool.to_tool_spec() for tool in self.all_tools.values()]
    
    def get_tool_schema(self, tool_name: str) -> MCPToolSchema | None:
        """Get tool schema by name.
        
        Args:
            tool_name: Prefixed tool name
        
        Returns:
            Tool schema or None if not found
        """
        return self.all_tools.get(tool_name)
    
    async def disconnect_all(self) -> None:
        """Disconnect from all MCP servers."""
        for client in self.clients.values():
            try:
                await client.disconnect()
            except Exception as exc:
                logger.warning(f"Error disconnecting from {client.config.name}: {exc}")
        
        self.clients.clear()
        self.all_tools.clear()
        logger.info("All MCP clients disconnected")
