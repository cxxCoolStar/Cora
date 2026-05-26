"""Unit tests for MCP client manager."""

from __future__ import annotations

import sys

import pytest

from core.mcp.config import MCPServerConfig
from core.mcp.manager import MCPClientManager


@pytest.fixture
def stub_server_configs() -> list[MCPServerConfig]:
    """Create configurations for multiple stub MCP servers."""
    return [
        MCPServerConfig(
            name="server1",
            transport="stdio",
            command=sys.executable,
            args=["-m", "tests.mcp.stub_server"],
            enabled=True,
            timeout=10.0,
        ),
        MCPServerConfig(
            name="server2",
            transport="stdio",
            command=sys.executable,
            args=["-m", "tests.mcp.stub_server"],
            enabled=True,
            timeout=10.0,
        ),
    ]


@pytest.mark.asyncio
async def test_mcp_manager_connects_to_multiple_servers(
    stub_server_configs: list[MCPServerConfig]
) -> None:
    """Test connecting to multiple MCP servers."""
    manager = MCPClientManager(stub_server_configs)
    
    try:
        await manager.connect_all()
        
        # Should have connected to both servers
        assert len(manager.clients) == 2
        assert "server1" in manager.clients
        assert "server2" in manager.clients
        
        # Should have tools from both servers with prefixed names
        assert "mcp_server1_echo" in manager.all_tools
        assert "mcp_server1_add" in manager.all_tools
        assert "mcp_server2_echo" in manager.all_tools
        assert "mcp_server2_add" in manager.all_tools
    
    finally:
        await manager.disconnect_all()


@pytest.mark.asyncio
async def test_mcp_manager_skips_disabled_servers() -> None:
    """Test that disabled servers are skipped."""
    configs = [
        MCPServerConfig(
            name="enabled",
            transport="stdio",
            command=sys.executable,
            args=["-m", "tests.mcp.stub_server"],
            enabled=True,
        ),
        MCPServerConfig(
            name="disabled",
            transport="stdio",
            command=sys.executable,
            args=["-m", "tests.mcp.stub_server"],
            enabled=False,
        ),
    ]
    
    manager = MCPClientManager(configs)
    
    try:
        await manager.connect_all()
        
        # Only enabled server should be connected
        assert len(manager.clients) == 1
        assert "enabled" in manager.clients
        assert "disabled" not in manager.clients
    
    finally:
        await manager.disconnect_all()


@pytest.mark.asyncio
async def test_mcp_manager_call_tool(stub_server_configs: list[MCPServerConfig]) -> None:
    """Test calling tools through manager."""
    manager = MCPClientManager(stub_server_configs)
    
    try:
        await manager.connect_all()
        
        # Call tool from server1
        result1 = await manager.call_tool(
            tool_name="mcp_server1_echo",
            arguments={"text": "from server1"}
        )
        assert not result1.is_error
        assert result1.to_text() == "from server1"
        
        # Call tool from server2
        result2 = await manager.call_tool(
            tool_name="mcp_server2_add",
            arguments={"a": 10, "b": 20}
        )
        assert not result2.is_error
        assert result2.to_text() == "30"
    
    finally:
        await manager.disconnect_all()


@pytest.mark.asyncio
async def test_mcp_manager_raises_on_invalid_tool_name(
    stub_server_configs: list[MCPServerConfig]
) -> None:
    """Test that invalid tool names raise ValueError."""
    manager = MCPClientManager(stub_server_configs)
    
    try:
        await manager.connect_all()
        
        # Not an MCP tool (missing prefix)
        with pytest.raises(ValueError, match="Not an MCP tool"):
            await manager.call_tool(
                tool_name="echo",
                arguments={"text": "test"}
            )
        
        # Invalid format
        with pytest.raises(ValueError, match="Invalid MCP tool name"):
            await manager.call_tool(
                tool_name="mcp_invalid",
                arguments={}
            )
        
        # Server not connected
        with pytest.raises(ValueError, match="not connected"):
            await manager.call_tool(
                tool_name="mcp_unknown_server_echo",
                arguments={"text": "test"}
            )
    
    finally:
        await manager.disconnect_all()


@pytest.mark.asyncio
async def test_mcp_manager_get_all_tool_specs(
    stub_server_configs: list[MCPServerConfig]
) -> None:
    """Test getting all tool specs for LLM."""
    manager = MCPClientManager(stub_server_configs)
    
    try:
        await manager.connect_all()
        
        tool_specs = manager.get_all_tool_specs()
        
        # Should have tools from both servers
        assert len(tool_specs) >= 4  # At least 2 tools per server
        
        # Check format
        for spec in tool_specs:
            assert spec["type"] == "function"
            assert "function" in spec
            assert "name" in spec["function"]
            assert "description" in spec["function"]
            assert "parameters" in spec["function"]
    
    finally:
        await manager.disconnect_all()


@pytest.mark.asyncio
async def test_mcp_manager_get_tool_schema(
    stub_server_configs: list[MCPServerConfig]
) -> None:
    """Test getting tool schema by name."""
    manager = MCPClientManager(stub_server_configs)
    
    try:
        await manager.connect_all()
        
        # Get existing tool
        schema = manager.get_tool_schema("mcp_server1_echo")
        assert schema is not None
        assert schema.name == "echo"
        assert schema.server_name == "server1"
        
        # Get non-existent tool
        schema = manager.get_tool_schema("mcp_server1_nonexistent")
        assert schema is None
    
    finally:
        await manager.disconnect_all()


@pytest.mark.asyncio
async def test_mcp_manager_disconnect_all(
    stub_server_configs: list[MCPServerConfig]
) -> None:
    """Test disconnecting all servers."""
    manager = MCPClientManager(stub_server_configs)
    
    await manager.connect_all()
    assert len(manager.clients) == 2
    assert len(manager.all_tools) > 0
    
    await manager.disconnect_all()
    assert len(manager.clients) == 0
    assert len(manager.all_tools) == 0
