from __future__ import annotations

TOOLSETS: dict[str, dict[str, object]] = {
    "user_memory": {
        "description": "Read and maintain the user's long-term personal memory file.",
        "tools": ["user_memory"],
    },
    "file": {
        "description": "Inspect and edit text files in the local workspace through listing, search, read, and write tools.",
        "tools": ["list_files", "search_files", "read_file", "write_file"],
    },
    "skills": {
        "description": "Discover local skills and load their instructions or supporting files on demand.",
        "tools": ["skills_list", "skill_view"],
    },
    "skills_execute": {
        "description": "Execute helper scripts that belong to a local skill after inspecting that skill's instructions.",
        "tools": ["skill_run"],
    },
    "automation": {
        "description": "Create and manage scheduled background tasks, reminders, and recurring checks.",
        "tools": ["scheduled_tasks"],
    },
    "terminal": {
        "description": "Run terminal commands and manage shell-oriented task execution.",
        "tools": ["shell_exec"],
    },
    "web": {
        "description": "Search the web and extract page content for current information tasks.",
        "tools": ["web_search", "web_fetch"],
    },
    "browser": {
        "description": "Drive an interactive browser for navigation and page interaction tasks.",
        "tools": ["browser_navigate", "browser_snapshot", "browser_click", "browser_type", "browser_back"],
    },
    "session_search": {
        "description": "Search prior sessions and historical records through explicit retrieval.",
        "tools": ["search_sessions"],
    },
    "subagent": {
        "description": "Delegate isolated sub-runs to child workers and merge structured results back.",
        "tools": ["spawn_worker", "spawn_workers"],
    },
}

TOOLSET_ALIASES: dict[str, str] = {
    "memory": "user_memory",
    "files": "file",
    "skills": "skills",
    "skills_execute": "skills_execute",
}

TOOLSET_PRESETS: dict[str, dict[str, object]] = {
    "cora-wechat": {
        "description": "Compact tool surface for messaging shells.",
        "toolsets": ["user_memory", "file", "web", "skills", "skills_execute", "session_search", "automation"],
    },
    "cora-cli": {
        "description": "Coding-focused tool surface for the local CLI shell.",
        "toolsets": ["user_memory", "file", "terminal", "web", "browser", "skills", "skills_execute", "session_search", "automation", "subagent"],
    },
    "cora-api": {
        "description": "General programmable tool surface for the HTTP/API shell.",
        "toolsets": ["user_memory", "file", "terminal", "web", "browser", "skills", "skills_execute", "session_search", "automation", "subagent"],
    },
}

TOOLSET_PRESET_ALIASES: dict[str, str] = {
    "wechat": "cora-wechat",
    "cli": "cora-cli",
    "api": "cora-api",
}


def resolve_toolset_names(toolset_names: list[str]) -> list[str]:
    resolved: list[str] = []
    for name in toolset_names:
        normalized_name = str(name or "").strip().lower()
        if not normalized_name:
            continue
        canonical_name = TOOLSET_ALIASES.get(normalized_name, normalized_name)
        if canonical_name not in TOOLSETS or canonical_name in resolved:
            continue
        resolved.append(canonical_name)
    return resolved


def resolve_toolset_preset(preset_name: str) -> list[str]:
    normalized_name = str(preset_name or "").strip().lower()
    if not normalized_name:
        return []
    canonical_name = TOOLSET_PRESET_ALIASES.get(normalized_name, normalized_name)
    preset = TOOLSET_PRESETS.get(canonical_name)
    if not preset:
        return []
    return resolve_toolset_names([str(name) for name in preset.get("toolsets", [])])


def resolve_toolsets(toolset_names: list[str]) -> list[str]:
    tools: list[str] = []
    for canonical_name in resolve_toolset_names(toolset_names):
        toolset = TOOLSETS.get(canonical_name)
        if not toolset:
            continue
        for tool_name in toolset.get("tools", []):
            if tool_name not in tools:
                tools.append(str(tool_name))
    return tools
