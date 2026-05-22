from __future__ import annotations

from core.tools.registry import ToolRegistry, ToolSpec, registry


def register_builtin_tools(target_registry: ToolRegistry | None = None) -> None:
    active_registry = target_registry or registry
    if active_registry.get("user_memory") is None:
        active_registry.register(
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
                risk="medium",
            )
        )
    if active_registry.get("list_files") is None:
        active_registry.register(
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
                risk="low",
            )
        )
    if active_registry.get("search_files") is None:
        active_registry.register(
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
                risk="low",
            )
        )
    if active_registry.get("read_file") is None:
        active_registry.register(
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
                risk="low",
            )
        )
    if active_registry.get("write_file") is None:
        active_registry.register(
            ToolSpec(
                name="write_file",
                toolset="file",
                description="Create or overwrite a text file in the local workspace, or append text when append=true. Use this to make explicit file edits instead of only describing them.",
                schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                        "append": {"type": "boolean"},
                    },
                    "required": ["path", "content"],
                    "additionalProperties": False,
                },
                handler=lambda executor, invocation: executor._tool_write_file(invocation),
                risk="medium",
            )
        )
    if active_registry.get("shell_exec") is None:
        active_registry.register(
            ToolSpec(
                name="shell_exec",
                toolset="terminal",
                description="Run a terminal command inside the local workspace and capture stdout, stderr, exit code, and working directory.",
                schema={
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "cwd": {"type": "string"},
                        "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 120},
                    },
                    "required": ["command"],
                    "additionalProperties": False,
                },
                handler=lambda executor, invocation: executor._tool_shell_exec(invocation),
                risk="high",
                allowed_roles=("primary",),
                requires_confirmation=True,
                requires_sandbox=True,
            )
        )
    if active_registry.get("web_search") is None:
        active_registry.register(
            ToolSpec(
                name="web_search",
                toolset="web",
                description="Search the public web for current external information and return a compact list of relevant results.",
                schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "max_results": {"type": "integer", "minimum": 1, "maximum": 8},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
                handler=lambda executor, invocation: executor._tool_web_search(invocation),
                read_only=True,
                risk="low",
            )
        )
    if active_registry.get("web_fetch") is None:
        active_registry.register(
            ToolSpec(
                name="web_fetch",
                toolset="web",
                description="Fetch a specific web page or URL and extract readable text content from it.",
                schema={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "max_chars": {"type": "integer", "minimum": 200, "maximum": 8000},
                    },
                    "required": ["url"],
                    "additionalProperties": False,
                },
                handler=lambda executor, invocation: executor._tool_web_fetch(invocation),
                read_only=True,
                risk="medium",
            )
        )
    if active_registry.get("search_sessions") is None:
        active_registry.register(
            ToolSpec(
                name="search_sessions",
                toolset="session_search",
                description="Search the current and prior conversation sessions for earlier messages, summaries, and historical context.",
                schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 8},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
                handler=lambda executor, invocation: executor._tool_search_sessions(invocation),
                read_only=True,
                risk="low",
            )
        )
    if active_registry.get("skills_list") is None:
        active_registry.register(
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
                risk="low",
            )
        )
    if active_registry.get("skill_view") is None:
        active_registry.register(
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
                risk="low",
            )
        )
    if active_registry.get("skill_run") is None:
        active_registry.register(
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
                risk="medium",
            )
        )
    if active_registry.get("scheduled_tasks") is None:
        active_registry.register(
            ToolSpec(
                name="scheduled_tasks",
                toolset="automation",
                description=(
                    "Create, inspect, update, pause, resume, delete, and queue scheduled background tasks. "
                    "Use this when the user asks for reminders, recurring checks, monitors, or periodic follow-ups."
                ),
                schema={
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["create", "list", "get", "update", "pause", "resume", "delete", "run_now"],
                        },
                        "task_ref": {"type": "string"},
                        "name": {"type": "string"},
                        "prompt": {"type": "string"},
                        "schedule_text": {"type": "string"},
                        "execution": {
                            "type": "object",
                            "properties": {
                                "mode": {
                                    "type": "string",
                                    "enum": ["agent_prompt", "skill", "script"],
                                },
                                "skill_name": {"type": "string"},
                                "script_path": {"type": "string"},
                                "input": {"type": "object"},
                                "skills": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                            "additionalProperties": False,
                        },
                        "run_immediately": {"type": "boolean"},
                        "enabled": {"type": "boolean"},
                        "metadata": {"type": "object"},
                        "schedule": {
                            "type": "object",
                            "properties": {
                                "kind": {
                                    "type": "string",
                                    "enum": ["once", "interval", "daily", "weekly", "cron"],
                                },
                                "at": {"type": "string"},
                                "interval_seconds": {"type": "integer", "minimum": 1},
                                "interval_minutes": {"type": "integer", "minimum": 1},
                                "anchor_at": {"type": "string"},
                                "hour": {"type": "integer", "minimum": 0, "maximum": 23},
                                "minute": {"type": "integer", "minimum": 0, "maximum": 59},
                                "timezone": {"type": "string"},
                                "expr": {"type": "string"},
                                "days_of_week": {
                                    "type": "array",
                                    "items": {
                                        "oneOf": [
                                            {"type": "integer", "minimum": 0, "maximum": 6},
                                            {"type": "string"},
                                        ]
                                    },
                                },
                            },
                            "additionalProperties": False,
                        },
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
                handler=lambda executor, invocation: executor._tool_scheduled_tasks(invocation),
                is_agent_stateful=True,
                risk="high",
                requires_confirmation=True,
            )
        )
    if active_registry.get("spawn_worker") is None:
        active_registry.register(
            ToolSpec(
                name="spawn_worker",
                toolset="subagent",
                description=(
                    "Delegate a single isolated subagent run with a narrower tool scope. "
                    "The child completes synchronously and returns a structured summary."
                ),
                schema={
                    "type": "object",
                    "properties": {
                        "instruction": {"type": "string"},
                        "tool_names": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "context_mode": {
                            "type": "string",
                            "enum": ["isolated", "shared"],
                        },
                    },
                    "required": ["instruction"],
                    "additionalProperties": False,
                },
                handler=lambda executor, invocation: executor._tool_spawn_worker(invocation),
                is_agent_stateful=True,
                risk="medium",
                allowed_roles=("primary",),
            )
        )
    if active_registry.get("spawn_workers") is None:
        active_registry.register(
            ToolSpec(
                name="spawn_workers",
                toolset="subagent",
                description=(
                    "Delegate multiple isolated subagent runs in parallel (bounded by max parallel spawns). "
                    "Each task must include an instruction and optional tool_names inherited from the parent run."
                ),
                schema={
                    "type": "object",
                    "properties": {
                        "tasks": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "instruction": {"type": "string"},
                                    "tool_names": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                    "context_mode": {
                                        "type": "string",
                                        "enum": ["isolated", "shared"],
                                    },
                                },
                                "required": ["instruction"],
                                "additionalProperties": False,
                            },
                            "minItems": 1,
                        },
                    },
                    "required": ["tasks"],
                    "additionalProperties": False,
                },
                handler=lambda executor, invocation: executor._tool_spawn_workers(invocation),
                is_agent_stateful=True,
                risk="medium",
                allowed_roles=("primary",),
            )
        )
