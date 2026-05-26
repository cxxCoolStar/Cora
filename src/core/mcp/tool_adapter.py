"""Adapter to integrate MCP tools into Cora's tool system."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from core.mcp.manager import MCPClientManager
from core.mcp.schema import MCPToolSchema
from core.tools.registry import ToolSpec

if TYPE_CHECKING:
    from core.clawbot.tools import ToolExecutionResult
    from core.tools.registry import ToolInvocation

logger = logging.getLogger(__name__)


class MCPToolAdapter:
    """Adapter to make MCP tools compatible with Cora's tool system.
    
    This adapter bridges MCP tools and Cora's internal tool registry,
    allowing MCP tools to be used alongside built-in tools.
    """
    
    def __init__(self, mcp_manager: MCPClientManager):
        """Initialize MCP tool adapter.
        
        Args:
            mcp_manager: MCP client manager instance
        """
        self.mcp_manager = mcp_manager
    
    def create_tool_specs(self) -> list[ToolSpec]:
        """Create ToolSpec instances for all MCP tools.
        
        Returns:
            List of ToolSpec instances for MCP tools
        """
        tool_specs: list[ToolSpec] = []
        
        for tool_name, mcp_schema in self.mcp_manager.all_tools.items():
            spec = self._create_tool_spec(tool_name, mcp_schema)
            tool_specs.append(spec)
        
        logger.debug(f"Created {len(tool_specs)} MCP tool specs")
        return tool_specs
    
    def _create_tool_spec(self, tool_name: str, mcp_schema: MCPToolSchema) -> ToolSpec:
        """Create a ToolSpec for a single MCP tool.
        
        Args:
            tool_name: Prefixed tool name (mcp_{server}_{tool})
            mcp_schema: MCP tool schema
        
        Returns:
            ToolSpec instance
        """
        # Extract server name from prefixed tool name
        # Format: mcp_{server_name}_{original_tool_name}
        parts = tool_name.split("_", 2)
        server_name = parts[1] if len(parts) >= 2 else "unknown"
        
        return ToolSpec(
            name=tool_name,
            toolset=f"mcp_{server_name}",
            description=mcp_schema.description,
            schema=mcp_schema.input_schema,
            handler=self._create_handler(tool_name),
            is_agent_stateful=False,
            read_only=not mcp_schema.is_mutating,
            risk="medium",  # MCP tools default to medium risk
            allowed_roles=(),
            requires_confirmation=False,  # Will be controlled by policy
            requires_sandbox=False,  # MCP servers run in separate processes
        )
    
    def _create_handler(self, tool_name: str):
        """Create a handler function for an MCP tool.
        
        Args:
            tool_name: Prefixed tool name
        
        Returns:
            Handler function
        """
        async def handler(executor: Any, invocation: "ToolInvocation") -> "ToolExecutionResult":
            """Handle MCP tool invocation."""
            from core.clawbot.tools import ToolExecutionResult
            
            try:
                # Extract arguments from the tool plan
                arguments = invocation.plan.arguments or {}
                
                # Call the MCP tool
                result = await self.mcp_manager.call_tool(
                    tool_name=tool_name,
                    arguments=arguments,
                )
                
                # Convert MCP result to ToolExecutionResult
                if result.is_error:
                    return ToolExecutionResult(
                        reply=result.error_message or "MCP tool execution failed",
                        action="tool_failed",
                        status="failed",
                        disposition="respond",
                        metadata={
                            "error_code": result.error_code,
                            "mcp_tool": tool_name,
                        },
                    )
                
                return ToolExecutionResult(
                    reply=result.to_text(),
                    action="tool_completed",
                    status="completed",
                    disposition="respond",
                    metadata={
                        "mcp_tool": tool_name,
                        "mcp_metadata": result.metadata,
                    },
                )
            
            except Exception as exc:
                logger.error(f"MCP tool {tool_name} execution failed: {exc}")
                return ToolExecutionResult(
                    reply=f"MCP tool execution error: {exc}",
                    action="tool_failed",
                    status="failed",
                    disposition="respond",
                    metadata={"mcp_tool": tool_name},
                )
        
        return handler
