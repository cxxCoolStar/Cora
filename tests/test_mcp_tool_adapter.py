"""Unit tests for MCP tool adapter."""

from __future__ import annotations

import sys

import pytest
import pytest_asyncio

from core.mcp.config import MCPServerConfig
from core.mcp.manager import MCPClientManager
from core.mcp.tool_adapter import MCPToolAdapter
from core.tools.registry import ToolSpec


@pytest_asyncio.fixture
async def mcp_manager_with_tools() -> MCPClientManager:
    """Create MCP manager with connected stub server."""
    config = MCPServerConfig(
        name="test",
        transport="stdio",
        command=sys.executable,
        args=["-m", "tests.mcp.stub_server"],
        enabled=True,
    )
    
    manager = MCPClientManager([config])
    await manager.connect_all()
    
    yield manager
    
    await manager.disconnect_all()


@pytest.mark.asyncio
async def test_mcp_tool_adapter_creates_tool_specs(
    mcp_manager_with_tools: MCPClientManager
) -> None:
    """Test that adapter creates ToolSpec instances for MCP tools."""
    adapter = MCPToolAdapter(mcp_manager_with_tools)
    
    tool_specs = adapter.create_tool_specs()
    
    # Should have specs for all MCP tools
    assert len(tool_specs) > 0
    
    # Check that specs are ToolSpec instances
    for spec in tool_specs:
        assert isinstance(spec, ToolSpec)
        assert spec.name.startswith("mcp_test_")
        assert spec.toolset == "mcp_test"
        assert spec.description != ""
        assert spec.schema is not None
        assert spec.handler is not None


@pytest.mark.asyncio
async def test_mcp_tool_adapter_tool_spec_properties(
    mcp_manager_with_tools: MCPClientManager
) -> None:
    """Test that tool specs have correct properties."""
    adapter = MCPToolAdapter(mcp_manager_with_tools)
    
    tool_specs = adapter.create_tool_specs()
    echo_spec = next((s for s in tool_specs if s.name == "mcp_test_echo"), None)
    
    assert echo_spec is not None
    assert echo_spec.name == "mcp_test_echo"
    assert echo_spec.toolset == "mcp_test"
    assert "echo" in echo_spec.description.lower()
    assert echo_spec.is_agent_stateful is False
    assert echo_spec.read_only is True  # Non-mutating tool
    assert echo_spec.risk == "medium"
    assert echo_spec.requires_sandbox is False


@pytest.mark.asyncio
async def test_mcp_tool_adapter_handler_executes_tool(
    mcp_manager_with_tools: MCPClientManager
) -> None:
    """Test that generated handler can execute MCP tool."""
    from core.clawbot.planner import ToolPlan
    from core.tools.registry import ToolInvocation
    
    adapter = MCPToolAdapter(mcp_manager_with_tools)
    tool_specs = adapter.create_tool_specs()
    
    echo_spec = next((s for s in tool_specs if s.name == "mcp_test_echo"), None)
    assert echo_spec is not None
    
    # Create a mock invocation
    invocation = ToolInvocation(
        session_id="test-session",
        source_message_id="test-message",
        plan=ToolPlan(
            tool="mcp_test_echo",
            arguments={"text": "hello from test"},
            reason="test execution"
        ),
        text=None,
        upload=None,
        context={},
    )
    
    # Execute the handler
    result = await echo_spec.handler(None, invocation)
    
    # Check result
    assert result.status == "completed"
    assert result.reply == "hello from test"
    assert result.metadata.get("mcp_tool") == "mcp_test_echo"


@pytest.mark.asyncio
async def test_mcp_tool_adapter_handler_handles_errors(
    mcp_manager_with_tools: MCPClientManager
) -> None:
    """Test that handler properly handles MCP tool errors."""
    from core.clawbot.planner import ToolPlan
    from core.tools.registry import ToolInvocation
    
    adapter = MCPToolAdapter(mcp_manager_with_tools)
    tool_specs = adapter.create_tool_specs()
    
    error_spec = next((s for s in tool_specs if s.name == "mcp_test_error"), None)
    assert error_spec is not None
    
    # Create a mock invocation
    invocation = ToolInvocation(
        session_id="test-session",
        source_message_id="test-message",
        plan=ToolPlan(
            tool="mcp_test_error",
            arguments={},
            reason="test error handling"
        ),
        text=None,
        upload=None,
        context={},
    )
    
    # Execute the handler
    result = await error_spec.handler(None, invocation)
    
    # Check error result
    assert result.status == "failed"
    assert "Intentional error" in result.reply
    assert result.metadata.get("error_code") == "-32000"
