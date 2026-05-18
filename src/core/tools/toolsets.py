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
}

TOOLSET_ALIASES: dict[str, str] = {
    "memory": "user_memory",
    "files": "file",
    "skills": "skills",
    "skills_execute": "skills_execute",
}


def resolve_toolsets(toolset_names: list[str]) -> list[str]:
    tools: list[str] = []
    for name in toolset_names:
        canonical_name = TOOLSET_ALIASES.get(name, name)
        toolset = TOOLSETS.get(canonical_name)
        if not toolset:
            continue
        for tool_name in toolset.get("tools", []):
            if tool_name not in tools:
                tools.append(str(tool_name))
    return tools
