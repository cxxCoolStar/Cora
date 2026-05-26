from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Callable

from core.schemas.tool import ToolSpec as ModelToolSpec
from core.tools.builtin import register_builtin_tools
from core.tools.registry import ToolRegistry, ToolSpec, registry
from core.tools.toolsets import resolve_toolset_preset, resolve_toolsets


SchemaTransformer = Callable[[ToolSpec, dict[str, Any]], dict[str, Any]]


@dataclass(slots=True)
class ToolManager:
    registry: ToolRegistry = registry
    auto_register_builtins: bool = True
    mcp_manager: Any = None  # MCPClientManager, optional
    mcp_adapter: Any = None  # MCPToolAdapter, optional

    def __post_init__(self) -> None:
        if self.auto_register_builtins:
            register_builtin_tools(self.registry)
        
        # Register MCP tools if MCP manager is provided
        if self.mcp_manager is not None:
            self._register_mcp_tools()
    
    def _register_mcp_tools(self) -> None:
        """Register MCP tools into the registry."""
        if self.mcp_manager is None:
            return
        
        # Import here to avoid circular dependency
        from core.mcp.tool_adapter import MCPToolAdapter
        
        if self.mcp_adapter is None:
            self.mcp_adapter = MCPToolAdapter(self.mcp_manager)
        
        # Create and register tool specs for all MCP tools
        mcp_tool_specs = self.mcp_adapter.create_tool_specs()
        for spec in mcp_tool_specs:
            self.registry.register(spec)

    def resolve_preset_toolsets(self, toolset_preset: str) -> list[str]:
        return resolve_toolset_preset(toolset_preset)

    def resolve_tool_names(
        self,
        toolsets: list[str] | None = None,
        *,
        toolset_preset: str | None = None,
    ) -> list[str]:
        resolved_toolsets: list[str] = []
        if toolset_preset:
            resolved_toolsets.extend(self.resolve_preset_toolsets(toolset_preset))
        for toolset in toolsets or []:
            if toolset not in resolved_toolsets:
                resolved_toolsets.append(toolset)
        return resolve_toolsets(resolved_toolsets)

    def get_registered_specs(
        self,
        toolsets: list[str] | None = None,
        *,
        toolset_preset: str | None = None,
    ) -> list[ToolSpec]:
        tool_names = self.resolve_tool_names(toolsets=toolsets, toolset_preset=toolset_preset)
        return self.registry.get_many(tool_names)

    def build_model_tool_specs(
        self,
        *,
        toolsets: list[str] | None = None,
        toolset_preset: str | None = None,
        schema_transformer: SchemaTransformer | None = None,
    ) -> list[ModelToolSpec]:
        specs: list[ModelToolSpec] = []
        for registered in self.get_registered_specs(toolsets, toolset_preset=toolset_preset):
            input_schema = deepcopy(registered.schema)
            if schema_transformer is not None:
                input_schema = schema_transformer(registered, input_schema)
            specs.append(
                ModelToolSpec(
                    name=registered.name,
                    description=registered.description,
                    input_schema=input_schema,
                    toolset=registered.toolset,
                    read_only=registered.read_only,
                    risk=registered.risk,
                    allowed_roles=list(registered.allowed_roles),
                    requires_confirmation=registered.requires_confirmation,
                    requires_sandbox=registered.requires_sandbox,
                )
            )
        return specs
