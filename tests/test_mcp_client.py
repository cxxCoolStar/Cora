"""Unit tests for MCP client."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from core.mcp.client import MCPClient
from core.mcp.config import MCPServerConfig
from core.mcp.schema import MCPToolResult


@pytest.fixture
def stub_server_config() -> MCPServerConfig:
    """Create configuration for stub MCP server."""
    return MCPServerConfig(
        name="test",
        transport="stdio",
        command=sys.executable,
        args=["-m", "tests.mcp.stub_server"],
        enabled=True,
        timeout=10.0,
    )


@pytest.mark.asyncio
async def test_mcp_client_connect_and_disconnect(stub_server_config: MCPServerConfig) -> None:
    """Test connecting to and disconnecting from MCP server."""
    client = MCPClient(stub_server_config)
    
    # Initially not connected
    assert not client.connected
    assert len(client.tools) == 0
    
    # Connect
    await client.connect()
    assert client.connected
    assert len(client.tools) > 0
    
    # Disconnect
    await client.disconnect()
    assert not client.connected
    assert len(client.tools) == 0


@pytest.mark.asyncio
async def test_mcp_client_discovers_tools(stub_server_config: MCPServerConfig) -> None:
    """Test that client discovers tools from server."""
    client = MCPClient(stub_server_config)
    
    try:
        await client.connect()
        
        # Should discover the tools provided by stub server
        assert "echo" in client.tools
        assert "add" in client.tools
        assert "error" in client.tools
        
        # Check tool schema
        echo_tool = client.tools["echo"]
        assert echo_tool.name == "echo"
        assert echo_tool.description == "Echo back the input text"
        assert echo_tool.server_name == "test"
        assert "text" in echo_tool.input_schema.get("properties", {})
    
    finally:
        await client.disconnect()


@pytest.mark.asyncio
async def test_mcp_client_call_echo_tool(stub_server_config: MCPServerConfig) -> None:
    """Test calling the echo tool."""
    client = MCPClient(stub_server_config)
    
    try:
        await client.connect()
        
        # Call echo tool
        result = await client.call_tool(
            tool_name="echo",
            arguments={"text": "hello world"}
        )
        
        assert isinstance(result, MCPToolResult)
        assert not result.is_error
        assert result.to_text() == "hello world"
    
    finally:
        await client.disconnect()


@pytest.mark.asyncio
async def test_mcp_client_call_add_tool(stub_server_config: MCPServerConfig) -> None:
    """Test calling the add tool."""
    client = MCPClient(stub_server_config)
    
    try:
        await client.connect()
        
        # Call add tool
        result = await client.call_tool(
            tool_name="add",
            arguments={"a": 5, "b": 3}
        )
        
        assert isinstance(result, MCPToolResult)
        assert not result.is_error
        assert result.to_text() == "8"
    
    finally:
        await client.disconnect()


@pytest.mark.asyncio
async def test_mcp_client_handles_tool_error(stub_server_config: MCPServerConfig) -> None:
    """Test handling tool execution errors."""
    client = MCPClient(stub_server_config)
    
    try:
        await client.connect()
        
        # Call error tool (always returns error)
        result = await client.call_tool(
            tool_name="error",
            arguments={}
        )
        
        assert isinstance(result, MCPToolResult)
        assert result.is_error
        assert result.error_code == "-32000"
        assert "Intentional error" in result.error_message
    
    finally:
        await client.disconnect()


@pytest.mark.asyncio
async def test_mcp_client_raises_on_unknown_tool(stub_server_config: MCPServerConfig) -> None:
    """Test that calling unknown tool raises ValueError."""
    client = MCPClient(stub_server_config)
    
    try:
        await client.connect()
        
        # Try to call non-existent tool
        with pytest.raises(ValueError, match="Tool unknown_tool not found"):
            await client.call_tool(
                tool_name="unknown_tool",
                arguments={}
            )
    
    finally:
        await client.disconnect()


@pytest.mark.asyncio
async def test_mcp_client_raises_when_not_connected() -> None:
    """Test that calling tool when not connected raises RuntimeError."""
    config = MCPServerConfig(
        name="test",
        transport="stdio",
        command="echo",
        args=[],
    )
    client = MCPClient(config)
    
    # Try to call tool without connecting
    with pytest.raises(RuntimeError, match="not connected"):
        await client.call_tool(
            tool_name="echo",
            arguments={"text": "test"}
        )


@pytest.mark.asyncio
async def test_mcp_client_concurrent_tool_calls(stub_server_config: MCPServerConfig) -> None:
    """Test making concurrent tool calls."""
    client = MCPClient(stub_server_config)
    
    try:
        await client.connect()
        
        # Make multiple concurrent calls
        tasks = [
            client.call_tool(tool_name="echo", arguments={"text": f"message {i}"})
            for i in range(5)
        ]
        
        results = await asyncio.gather(*tasks)
        
        # All should succeed
        assert len(results) == 5
        for i, result in enumerate(results):
            assert not result.is_error
            assert result.to_text() == f"message {i}"
    
    finally:
        await client.disconnect()
