from __future__ import annotations

from core.tools.registry import ToolSpec, registry


def register_builtin_tools() -> None:
    if registry.get("save_text") is not None:
        return

    registry.register(
        ToolSpec(
            name="save_text",
            toolset="capture",
            description="Save plain text content into the personal wiki.",
            schema={"name": "save_text", "description": "Save user text as a knowledge item."},
            handler=lambda executor, invocation: executor._tool_save_text(invocation),
        )
    )
    registry.register(
        ToolSpec(
            name="save_link",
            toolset="capture",
            description="Save a standalone link into the personal wiki.",
            schema={"name": "save_link", "description": "Save user link as a knowledge item."},
            handler=lambda executor, invocation: executor._tool_save_link(invocation),
        )
    )
    registry.register(
        ToolSpec(
            name="save_file",
            toolset="capture",
            description="Save an uploaded file into the personal wiki.",
            schema={"name": "save_file", "description": "Save an uploaded file as a knowledge item."},
            handler=lambda executor, invocation: executor._tool_save_file(invocation),
        )
    )
    registry.register(
        ToolSpec(
            name="overview_knowledge_base",
            toolset="wiki_browse",
            description="Show a high-level overview of the current knowledge base.",
            schema={"name": "overview_knowledge_base", "description": "Summarize the current knowledge base overview."},
            handler=lambda executor, invocation: executor._tool_overview_knowledge_base(invocation),
            read_only=True,
        )
    )
    registry.register(
        ToolSpec(
            name="list_topics",
            toolset="wiki_browse",
            description="List topics currently available in the personal wiki.",
            schema={"name": "list_topics", "description": "List current topics in the knowledge base."},
            handler=lambda executor, invocation: executor._tool_list_topics(invocation),
            read_only=True,
        )
    )
    registry.register(
        ToolSpec(
            name="open_topic",
            toolset="wiki_browse",
            description="Open a topic and return the most relevant items under it.",
            schema={"name": "open_topic", "description": "Open the best matching topic for the query."},
            handler=lambda executor, invocation: executor._tool_open_topic(invocation),
            read_only=True,
        )
    )
    registry.register(
        ToolSpec(
            name="read_item",
            toolset="wiki_read",
            description="Read a specific item from the current working set or focus item.",
            schema={"name": "read_item", "description": "Read full text or a selected view of a known item."},
            handler=lambda executor, invocation: executor._tool_read_item(invocation),
            read_only=True,
        )
    )
    registry.register(
        ToolSpec(
            name="summarize_item",
            toolset="wiki_read",
            description="Summarize a specific item from the current working set or focus item.",
            schema={"name": "summarize_item", "description": "Summarize a known item."},
            handler=lambda executor, invocation: executor._tool_summarize_item(invocation),
            read_only=True,
        )
    )
    registry.register(
        ToolSpec(
            name="clarify_reference",
            toolset="agent_state",
            description="Ask the user which current result they mean.",
            schema={"name": "clarify_reference", "description": "Clarify an ambiguous reference across the current working set."},
            handler=lambda executor, invocation: executor._tool_clarify_reference(invocation),
            is_agent_stateful=True,
        )
    )
