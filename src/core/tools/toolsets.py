from __future__ import annotations

TOOLSETS: dict[str, dict[str, object]] = {
    "archive_capture": {
        "description": "Capture new source materials into the archive.",
        "tools": ["archive"],
    },
    "archive_search": {
        "description": "Search archived materials through topics and archive indexes.",
        "tools": ["archive"],
    },
    "archive_read": {
        "description": "Read or summarize already selected archived items.",
        "tools": ["archive"],
    },
    "archive_delivery": {
        "description": "Deliver previously saved files back to the user through a supported channel.",
        "tools": ["archive"],
    },
    "archive_state": {
        "description": "Clarification and short-term conversation state management.",
        "tools": ["archive_state"],
    },
    "archive_maintenance": {
        "description": "Maintenance actions over archive indexes and topic organization.",
        "tools": [],
    },
    "user_memory": {
        "description": "Read and maintain the user's long-term personal memory file.",
        "tools": ["user_memory"],
    },
}

TOOLSET_ALIASES: dict[str, str] = {
    "capture": "archive_capture",
    "wiki_browse": "archive_search",
    "wiki_read": "archive_read",
    "channel_delivery": "archive_delivery",
    "agent_state": "archive_state",
    "wiki_maintenance": "archive_maintenance",
    "memory": "user_memory",
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
