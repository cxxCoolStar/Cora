from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable

from core.schemas.tool import ToolSpec as ModelToolSpec
from core.tools.builtin import register_builtin_tools
from core.tools.registry import ToolRegistry, ToolSpec, registry
from core.tools.toolsets import resolve_toolsets


SchemaTransformer = Callable[[ToolSpec, dict[str, Any]], dict[str, Any]]


@dataclass(slots=True)
class ToolManager:
    registry: ToolRegistry = registry
    auto_register_builtins: bool = True

    def __post_init__(self) -> None:
        if self.auto_register_builtins:
            register_builtin_tools()

    def resolve_tool_names(self, toolsets: list[str]) -> list[str]:
        return resolve_toolsets(toolsets)

    def get_registered_specs(self, toolsets: list[str]) -> list[ToolSpec]:
        tool_names = self.resolve_tool_names(toolsets)
        return self.registry.get_many(tool_names)

    def build_model_tool_specs(
        self,
        *,
        toolsets: list[str],
        schema_transformer: SchemaTransformer | None = None,
    ) -> list[ModelToolSpec]:
        specs: list[ModelToolSpec] = []
        for registered in self.get_registered_specs(toolsets):
            input_schema = deepcopy(registered.schema)
            if schema_transformer is not None:
                input_schema = schema_transformer(registered, input_schema)
            specs.append(
                ModelToolSpec(
                    name=registered.name,
                    description=registered.description,
                    input_schema=input_schema,
                )
            )
        return specs
