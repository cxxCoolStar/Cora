from __future__ import annotations

from core.tools.registry import ToolSpec, registry


def register_builtin_tools() -> None:
    if registry.get("save_content") is not None:
        return

    registry.register(
        ToolSpec(
            name="save_content",
            toolset="capture",
            description="Save standalone text or a standalone link into the personal wiki.",
            schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The text or standalone URL to save into the wiki."},
                },
                "required": ["text"],
                "additionalProperties": False,
            },
            handler=lambda executor, invocation: executor._tool_save_content(invocation),
        )
    )
    registry.register(
        ToolSpec(
            name="save_file",
            toolset="capture",
            description="Save an uploaded file into the personal wiki.",
            schema={"type": "object", "properties": {}, "additionalProperties": False},
            handler=lambda executor, invocation: executor._tool_save_file(invocation),
        )
    )
    registry.register(
        ToolSpec(
            name="overview_knowledge_base",
            toolset="wiki_browse",
            description="Show a high-level overview of the current knowledge base.",
            schema={"type": "object", "properties": {}, "additionalProperties": False},
            handler=lambda executor, invocation: executor._tool_overview_knowledge_base(invocation),
            read_only=True,
        )
    )
    registry.register(
        ToolSpec(
            name="list_topics",
            toolset="wiki_browse",
            description="List topics currently available in the personal wiki.",
            schema={"type": "object", "properties": {}, "additionalProperties": False},
            handler=lambda executor, invocation: executor._tool_list_topics(invocation),
            read_only=True,
        )
    )
    registry.register(
        ToolSpec(
            name="open_topic",
            toolset="wiki_browse",
            description="Open a topic and return the most relevant items under it.",
            schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The user query to match against topics and archived items."},
                    "top_k": {"type": "integer", "description": "How many topic candidates to consider.", "minimum": 1, "maximum": 5},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            handler=lambda executor, invocation: executor._tool_open_topic(invocation),
            read_only=True,
        )
    )
    registry.register(
        ToolSpec(
            name="read_item",
            toolset="wiki_read",
            description="Read a specific item from the current working set or focus item.",
            schema={
                "type": "object",
                "properties": {
                    "target": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": ["item_id", "working_set_rank", "recent_item", "auto"],
                                "description": "How to identify the item to read.",
                            },
                            "value": {
                                "description": "The item identifier or working-set rank, depending on target.type.",
                            },
                        },
                        "required": ["type", "value"],
                        "additionalProperties": False,
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["summary", "full_text", "key_points"],
                        "description": "How much of the item to return.",
                    },
                },
                "required": ["target"],
                "additionalProperties": False,
            },
            handler=lambda executor, invocation: executor._tool_read_item(invocation),
            read_only=True,
        )
    )
    registry.register(
        ToolSpec(
            name="summarize_item",
            toolset="wiki_read",
            description="Summarize a specific item from the current working set or focus item.",
            schema={
                "type": "object",
                "properties": {
                    "target": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": ["item_id", "working_set_rank", "recent_item", "auto"],
                                "description": "How to identify the item to summarize.",
                            },
                            "value": {
                                "description": "The item identifier or working-set rank, depending on target.type.",
                            },
                        },
                        "required": ["type", "value"],
                        "additionalProperties": False,
                    },
                    "style": {
                        "type": "string",
                        "enum": ["brief", "structured", "interview_notes"],
                        "description": "The summary style to use.",
                    },
                },
                "required": ["target"],
                "additionalProperties": False,
            },
            handler=lambda executor, invocation: executor._tool_summarize_item(invocation),
            read_only=True,
        )
    )
    registry.register(
        ToolSpec(
            name="clarify_reference",
            toolset="agent_state",
            description="Ask the user which current result they mean.",
            schema={
                "type": "object",
                "properties": {
                    "reference_text": {"type": "string", "description": "The ambiguous user reference that needs clarification."},
                },
                "required": ["reference_text"],
                "additionalProperties": False,
            },
            handler=lambda executor, invocation: executor._tool_clarify_reference(invocation),
            is_agent_stateful=True,
        )
    )
    registry.register(
        ToolSpec(
            name="clarify_capture_intent",
            toolset="agent_state",
            description="Ask whether a newly provided long passage should be saved or summarized first.",
            schema={
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The clarification question to ask the user about save vs summarize.",
                    },
                },
                "required": ["question"],
                "additionalProperties": False,
            },
            handler=lambda executor, invocation: executor._tool_clarify_capture_intent(invocation),
            is_agent_stateful=True,
        )
    )
    # File delivery tool for WeChat
    registry.register(
        ToolSpec(
            name="send_file_to_user",
            toolset="channel_delivery",
            description="Send a file (document, image, video) from the personal wiki back to the user via WeChat. Use when the user asks to view, download, or receive a previously saved file.",
            schema={
                "type": "object",
                "properties": {
                    "target": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": ["item_id", "working_set_rank", "recent_item", "auto"],
                                "description": "How to identify the file item to send.",
                            },
                            "value": {
                                "description": "The item identifier or working-set rank, depending on target.type.",
                            },
                        },
                        "required": ["type", "value"],
                        "additionalProperties": False,
                    },
                    "caption": {
                        "type": "string",
                        "description": "Optional text to send along with the file.",
                    },
                    "target_title_hint": {
                        "type": "string",
                        "description": "Optional title fragment to help resolve which file to send when the target reference is ambiguous.",
                    },
                },
                "required": ["target"],
                "additionalProperties": False,
            },
            handler=lambda executor, invocation: executor._tool_send_file_to_user(invocation),
        )
    )
