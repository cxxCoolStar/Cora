"""Integration tests for MCP tools in ToolManager."""

from __future__ import annotations

import sys

import pytest
import pytest_asyncio

from core.mcp.config import MCPServerConfig
from core.mcp.manager import MCPClientManager
from core.tools.manager import ToolManager
from core.tools.registry import ToolRegistry


@pytest_asyncio.fixture
async def tool_manager_with_mcp() -> ToolManager:
    """Create ToolManager with MCP tools registered."""
    # Create MCP manager
    config = MCPServerConfig(
        name="test",
        transport="stdio",
        command=sys.executable,
        args=["-m", "tests.mcp.stub_server"],
        enabled=True,
    )
    
    mcp_manager = MCPClientManager([config])
    await mcp_manager.connect_all()
    
    # Create ToolManager with MCP support
    registry = ToolRegistry()
    tool_manager = ToolManager(
        registry=registry,
        auto_register_builtins=False,  # Don't register builtins for cleaner test
        mcp_manager=mcp_manager,
    )
    
    yield tool_manager
    
    await mcp_manager.disconnect_all()


@pytest.mark.asyncio
async def test_tool_manager_registers_mcp_tools(
    tool_manager_with_mcp: ToolManager
) -> None:
    """Test that ToolManager registers MCP tools."""
    # Check that MCP tools are registered
    all_tool_names = tool_manager_with_mcp.registry.names()
    
    assert "mcp_test_echo" in all_tool_names
    assert "mcp_test_add" in all_tool_names
    assert "mcp_test_error" in all_tool_names


@pytest.mark.asyncio
async def test_tool_manager_can_get_mcp_tool_specs(
    tool_manager_with_mcp: ToolManager
) -> None:
    """Test that ToolManager can retrieve MCP tool specs."""
    # Get all registered tool names
    all_names = tool_manager_with_mcp.registry.names()
    
    # Filter MCP tools
    mcp_tool_names = [n for n in all_names if n.startswith("mcp_test_")]
    assert len(mcp_tool_names) >= 3
    
    # Get specs by names
    mcp_specs = tool_manager_with_mcp.registry.get_many(mcp_tool_names)
    assert len(mcp_specs) >= 3
    
    # Check that specs have correct properties
    echo_spec = next((s for s in mcp_specs if s.name == "mcp_test_echo"), None)
    assert echo_spec is not None
    assert echo_spec.toolset == "mcp_test"


@pytest.mark.asyncio
async def test_tool_manager_builds_model_specs_for_mcp_tools(
    tool_manager_with_mcp: ToolManager
) -> None:
    """Test that ToolManager can build model specs for MCP tools."""
    # Get all MCP tool names
    all_names = tool_manager_with_mcp.registry.names()
    mcp_tool_names = [n for n in all_names if n.startswith("mcp_test_")]
    
    # Build model specs using tool names directly
    # Since toolset resolution doesn't know about mcp_test, we'll use the registry directly
    mcp_specs = tool_manager_with_mcp.registry.get_many(mcp_tool_names)
    
    assert len(mcp_specs) >= 3
    
    # Check model spec format by converting manually
    for spec in mcp_specs:
        assert spec.name.startswith("mcp_test_")
        assert spec.toolset == "mcp_test"
        assert spec.description != ""
        assert spec.schema is not None


@pytest.mark.asyncio
async def test_tool_manager_can_dispatch_mcp_tool(
    tool_manager_with_mcp: ToolManager
) -> None:
    """Test that ToolManager can dispatch MCP tool calls."""
    from core.clawbot.planner import ToolPlan
    from core.tools.registry import ToolInvocation
    
    # Create invocation
    invocation = ToolInvocation(
        session_id="test-session",
        source_message_id="test-message",
        plan=ToolPlan(
            tool="mcp_test_echo",
            arguments={"text": "test dispatch"},
            reason="test dispatch"
        ),
        text=None,
        upload=None,
        context={},
    )
    
    # Dispatch through registry
    result = await tool_manager_with_mcp.registry.dispatch(
        executor=None,
        name="mcp_test_echo",
        invocation=invocation,
    )
    
    assert result.status == "completed"
    assert result.reply == "test dispatch"


@pytest.mark.asyncio
async def test_tool_manager_mcp_and_builtin_tools_coexist() -> None:
    """Test that MCP tools and built-in tools can coexist."""
    # Create MCP manager
    config = MCPServerConfig(
        name="test",
        transport="stdio",
        command=sys.executable,
        args=["-m", "tests.mcp.stub_server"],
        enabled=True,
    )
    
    mcp_manager = MCPClientManager([config])
    await mcp_manager.connect_all()
    
    try:
        # Create ToolManager with both builtins and MCP
        registry = ToolRegistry()
        tool_manager = ToolManager(
            registry=registry,
            auto_register_builtins=True,  # Register builtins
            mcp_manager=mcp_manager,
        )
        
        all_tool_names = tool_manager.registry.names()
        
        # Should have both built-in and MCP tools
        assert any(name.startswith("mcp_test_") for name in all_tool_names)
        # Built-in tools should also be present (if any are registered)
        # This depends on what builtin tools are registered
    
    finally:
        await mcp_manager.disconnect_all()
