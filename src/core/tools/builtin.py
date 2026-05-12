from __future__ import annotations

from core.tools.registry import ToolSpec, registry


def register_builtin_tools() -> None:
    if registry.get("user_memory") is None:
        registry.register(
            ToolSpec(
                name="user_memory",
                toolset="user_memory",
                description="Manage the user's long-term personal memory stored in USER.md. Use read to inspect, add to remember a fact, replace to update one, and remove to delete one.",
                schema={
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["read", "add", "replace", "remove"],
                        },
                        "content": {"type": "string"},
                        "old_text": {"type": "string"},
                        "new_content": {"type": "string"},
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
                handler=lambda executor, invocation: executor._tool_user_memory(invocation),
                is_agent_stateful=True,
            )
        )
    if registry.get("list_files") is None:
        registry.register(
            ToolSpec(
                name="list_files",
                toolset="file",
                description="List files and directories under the local workspace. Use this before reading code when you need to orient yourself in the repository.",
                schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "recursive": {"type": "boolean"},
                        "max_results": {"type": "integer", "minimum": 1, "maximum": 200},
                        "include_hidden": {"type": "boolean"},
                    },
                    "additionalProperties": False,
                },
                handler=lambda executor, invocation: executor._tool_list_files(invocation),
                read_only=True,
            )
        )
    if registry.get("search_files") is None:
        registry.register(
            ToolSpec(
                name="search_files",
                toolset="file",
                description="Search file names and text content inside the local workspace. Prefer this before answering questions about where code lives.",
                schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "path": {"type": "string"},
                        "file_pattern": {"type": "string"},
                        "case_sensitive": {"type": "boolean"},
                        "max_results": {"type": "integer", "minimum": 1, "maximum": 50},
                        "include_hidden": {"type": "boolean"},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
                handler=lambda executor, invocation: executor._tool_search_files(invocation),
                read_only=True,
            )
        )
    if registry.get("read_file") is None:
        registry.register(
            ToolSpec(
                name="read_file",
                toolset="file",
                description="Read a text file from the local workspace, optionally by line range. Use this to inspect code or configuration instead of guessing.",
                schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "start_line": {"type": "integer", "minimum": 1},
                        "end_line": {"type": "integer", "minimum": 1},
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
                handler=lambda executor, invocation: executor._tool_read_file(invocation),
                read_only=True,
            )
        )
    if registry.get("skills_list") is None:
        registry.register(
            ToolSpec(
                name="skills_list",
                toolset="skills",
                description="List available local skills with short descriptions. Use this to discover reusable workflows before improvising a project-specific procedure.",
                schema={
                    "type": "object",
                    "properties": {
                        "category": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                handler=lambda executor, invocation: executor._tool_skills_list(invocation),
                read_only=True,
            )
        )
    if registry.get("skill_view") is None:
        registry.register(
            ToolSpec(
                name="skill_view",
                toolset="skills",
                description="Load a local skill's main instructions or one supporting file from references, templates, assets, or scripts. Use this when a listed skill is relevant to the user's task.",
                schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "file_path": {"type": "string"},
                    },
                    "required": ["name"],
                    "additionalProperties": False,
                },
                handler=lambda executor, invocation: executor._tool_skill_view(invocation),
                read_only=True,
            )
        )
    if registry.get("skill_run") is None:
        registry.register(
            ToolSpec(
                name="skill_run",
                toolset="skills_execute",
                description="Run an executable helper script that belongs to a loaded local skill. Use this only after reading the relevant skill instructions and pass structured JSON input expected by that script.",
                schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "script_path": {"type": "string"},
                        "input": {"type": "object"},
                    },
                    "required": ["name", "script_path", "input"],
                    "additionalProperties": False,
                },
                handler=lambda executor, invocation: executor._tool_skill_run(invocation),
                is_agent_stateful=True,
            )
        )
