"""MCP Server configuration data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class MCPServerConfig:
    """MCP Server configuration.
    
    Defines how to connect to an MCP server and what capabilities it provides.
    """
    
    name: str
    """Server name (unique identifier)"""
    
    transport: Literal["stdio", "http", "websocket"]
    """Transport method for communication"""
    
    enabled: bool = True
    """Whether this server is enabled"""
    
    # stdio transport
    command: str | None = None
    """Command to start the server process (stdio only)"""
    
    args: list[str] = field(default_factory=list)
    """Command arguments (stdio only)"""
    
    env: dict[str, str] = field(default_factory=dict)
    """Environment variables for the server process (stdio only)"""
    
    # HTTP/WebSocket transport
    url: str | None = None
    """Server URL (http/websocket only)"""
    
    headers: dict[str, str] = field(default_factory=dict)
    """HTTP headers (http/websocket only)"""
    
    # Connection settings
    timeout: float = 30.0
    """Connection timeout in seconds"""
    
    retry_on_failure: bool = True
    """Whether to retry connection on failure"""
    
    max_retries: int = 3
    """Maximum number of connection retries"""
    
    def validate(self) -> None:
        """Validate configuration.
        
        Raises:
            ValueError: If configuration is invalid
        """
        if self.transport == "stdio":
            if not self.command:
                raise ValueError(f"Server {self.name}: command is required for stdio transport")
        elif self.transport in ("http", "websocket"):
            if not self.url:
                raise ValueError(f"Server {self.name}: url is required for {self.transport} transport")
        else:
            raise ValueError(f"Server {self.name}: unsupported transport {self.transport}")
