from __future__ import annotations

from core.tools.registry import ToolSpec, registry


def register_builtin_tools() -> None:
    if registry.get("archive") is not None:
        return

    registry.register(
        ToolSpec(
            name="archive",
            toolset="archive_capture",
            description="Operate on archived materials. Use actions to save, inspect the archive overview, list topics, open matching results, read or summarize an item, or deliver a saved file.",
            schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["save", "overview", "list_topics", "open", "read", "summarize", "deliver", "delete"],
                    },
                    "text": {"type": "string"},
                    "query": {"type": "string"},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 5},
                    "target": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": ["item_id", "auto"],
                            },
                            "value": {},
                        },
                        "required": ["type", "value"],
                        "additionalProperties": False,
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["summary", "full_text", "key_points"],
                    },
                    "style": {
                        "type": "string",
                        "enum": ["brief", "structured", "interview_notes"],
                    },
                    "caption": {"type": "string"},
                    "target_title_hint": {"type": "string"},
                },
                "required": ["action"],
                "additionalProperties": False,
            },
            handler=lambda executor, invocation: executor._tool_archive(invocation),
        )
    )
    registry.register(
        ToolSpec(
            name="archive_state",
            toolset="archive_state",
            description="Manage short-term archive conversation state. Use actions to ask clarifying questions or resolve pending work.",
            schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["clarify_reference", "clarify_capture_intent", "resolve_pending"],
                    },
                    "reference_text": {"type": "string"},
                    "question": {"type": "string"},
                    "resolution": {
                        "type": "string",
                        "enum": ["save", "cancel", "summarize", "select"],
                    },
                    "note": {"type": "string"},
                    "target": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": ["item_id", "auto"],
                            },
                            "value": {},
                        },
                        "required": ["type", "value"],
                        "additionalProperties": False,
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["summary", "full_text", "key_points"],
                    },
                },
                "required": ["action"],
                "additionalProperties": False,
            },
            handler=lambda executor, invocation: executor._tool_archive_state(invocation),
            is_agent_stateful=True,
        )
    )
