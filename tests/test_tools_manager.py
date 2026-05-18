from __future__ import annotations

from core.tools import ToolManager
from core.tools.registry import ToolRegistry, ToolSpec


def test_tool_manager_resolves_registered_specs_for_file_toolset() -> None:
    manager = ToolManager()

    specs = manager.get_registered_specs(["file"])

    assert [spec.name for spec in specs] == ["list_files", "search_files", "read_file", "write_file"]


def test_tool_manager_can_transform_schema_during_model_spec_build() -> None:
    manager = ToolManager()

    specs = manager.build_model_tool_specs(
        toolsets=["skills_execute"],
        schema_transformer=lambda registered, schema: {
            **schema,
            "x-tool-name": registered.name,
        },
    )

    assert len(specs) == 1
    assert specs[0].name == "skill_run"
    assert specs[0].input_schema["x-tool-name"] == "skill_run"


def test_tool_manager_cli_preset_includes_registered_terminal_tools_but_wechat_does_not() -> None:
    registry = ToolRegistry()
    manager = ToolManager(registry=registry)
    registry.register(
        ToolSpec(
            name="shell_exec",
            toolset="terminal",
            description="Run a shell command.",
            schema={"type": "object", "properties": {}},
            handler=lambda executor, invocation: None,
        )
    )

    cli_specs = manager.get_registered_specs(toolset_preset="cora-cli")
    wechat_specs = manager.get_registered_specs(toolset_preset="cora-wechat")

    assert "shell_exec" in [spec.name for spec in cli_specs]
    assert "shell_exec" not in [spec.name for spec in wechat_specs]
