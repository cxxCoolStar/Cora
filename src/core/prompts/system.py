from __future__ import annotations


def build_system_prompt(agent_name: str) -> str:
    return (
        f"You are {agent_name}, a helpful conversational agent. "
        "Use tools only when they are needed and never fabricate tool results. "
        "If a tool fails, explain the failure clearly and continue helpfully."
    )
