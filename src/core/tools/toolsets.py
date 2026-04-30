from __future__ import annotations

TOOLSETS: dict[str, dict[str, object]] = {
    "capture": {
        "description": "Capture new source materials into the personal wiki.",
        "tools": ["save_content", "save_file"],
    },
    "wiki_browse": {
        "description": "Browse the knowledge base structure and topics.",
        "tools": ["overview_knowledge_base", "list_topics", "open_topic"],
    },
    "wiki_read": {
        "description": "Read or summarize known items already selected from the wiki.",
        "tools": ["read_item", "summarize_item"],
    },
    "channel_delivery": {
        "description": "Deliver previously saved files back to the user through a supported channel.",
        "tools": ["send_file_to_user"],
    },
    "agent_state": {
        "description": "Clarification and short-term conversation state management.",
        "tools": ["clarify_reference", "clarify_capture_intent"],
    },
    "wiki_maintenance": {
        "description": "Maintenance actions over the wiki/topic index.",
        "tools": [],
    },
}


def resolve_toolsets(toolset_names: list[str]) -> list[str]:
    tools: list[str] = []
    for name in toolset_names:
        toolset = TOOLSETS.get(name)
        if not toolset:
            continue
        for tool_name in toolset.get("tools", []):
            if tool_name not in tools:
                tools.append(str(tool_name))
    return tools
