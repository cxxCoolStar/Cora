from __future__ import annotations

from core.tools import ToolManager


def test_tool_manager_resolves_registered_specs_for_file_toolset() -> None:
    manager = ToolManager()

    specs = manager.get_registered_specs(["file"])

    assert [spec.name for spec in specs] == ["list_files", "search_files", "read_file"]


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
