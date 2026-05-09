from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from core.agent.runtime_state import ConversationRuntimeState, EventSnapshot, ItemSnapshot, PendingState
from core.agent.skill_loader import SkillDefinition
from core.schemas.message import Message
from core.user_memory import UserMemoryStore


DEFAULT_AGENT_IDENTITY = (
    "You are Cora, a general AI agent runtime evolving from the legacy ClawBot flow. "
    "Prefer using the shared archive filesystem contract and reusable skills rather than legacy product-specific assumptions. "
    "When the user explicitly asks you to remember, review, update, or forget a stable personal fact, use the user_memory tool."
)

EXECUTION_GUIDANCE = (
    "You are a tool-using assistant. Take action through available tools when they improve correctness, retrieval, or state management. "
    "Do not pretend a tool action happened if it did not. If the user asks about current saved state, inspect the relevant tool-backed state instead of guessing. "
    "If the user asks about local files or repository code, prefer using list_files, search_files, and read_file before answering implementation details."
)

MEMORY_GUIDANCE = (
    "Long-term user memory is stored separately from short-term conversation context. "
    "Use `user_memory` only for durable personal facts, preferences, recurring corrections, or stable reference notes the user explicitly wants remembered. "
    "Do not store temporary task progress, one-off requests, or speculative sensitive inferences as long-term memory."
)

SKILLS_GUIDANCE = (
    "Shared skills describe reusable workflows and conventions. Treat them as operating guidance for how to use the current capabilities well, "
    "especially the archive workflow."
)


class AgentPromptBuilder:
    def __init__(
        self,
        *,
        agent_identity: str = DEFAULT_AGENT_IDENTITY,
        user_memory_path: Path | None = None,
    ) -> None:
        self.agent_identity = agent_identity
        self.user_memory_path = user_memory_path or Path("user-memory/USER.md")
        self.user_memory_store = UserMemoryStore(self.user_memory_path)

    def build_messages(
        self,
        *,
        session_id: str,
        user_text: str,
        runtime: ConversationRuntimeState,
        skills: Iterable[SkillDefinition],
        history: list[Message] | None = None,
        upload_name: str | None = None,
    ) -> list[Message]:
        system_parts = self._build_system_parts(runtime=runtime, skills=skills, upload_name=upload_name)
        messages = [Message.system(session_id=session_id, content="\n".join(system_parts))]
        if history:
            messages.extend(history)
        messages.append(Message.user(session_id=session_id, content=user_text))
        return messages

    def _build_system_parts(
        self,
        *,
        runtime: ConversationRuntimeState,
        skills: Iterable[SkillDefinition],
        upload_name: str | None,
    ) -> list[str]:
        system_parts = [
            self.agent_identity,
            "",
            "Execution guidance:",
            EXECUTION_GUIDANCE,
            "",
            "Memory guidance:",
            MEMORY_GUIDANCE,
            "",
            "Skills guidance:",
            SKILLS_GUIDANCE,
            "",
            "Runtime state:",
        ]
        system_parts.extend(f"- {line}" for line in runtime.summary_lines())
        user_memory_block = self._load_user_memory_block()
        if user_memory_block:
            system_parts.extend(["", "User memory:", user_memory_block])
        skill_lines = self._format_skills(skills)
        if skill_lines:
            system_parts.extend(["", "Shared skills:"])
            system_parts.extend(skill_lines)
        if upload_name:
            system_parts.extend(["", f"Current upload: {upload_name}"])
        system_parts.extend(["", self._format_state_block(runtime)])
        return system_parts

    def _load_user_memory_block(self) -> str:
        return self.user_memory_store.read_text()

    @staticmethod
    def _format_skills(skills: Iterable[SkillDefinition]) -> list[str]:
        lines: list[str] = []
        for skill in skills:
            description = skill.description or "No description provided."
            lines.append(f"- {skill.name}: {description}")
        return lines

    @staticmethod
    def _format_state_block(runtime: ConversationRuntimeState) -> str:
        state = {
            "last_action": runtime.last_action,
            "recent_events": [AgentPromptBuilder._event_to_dict(event) for event in runtime.recent_events[:5]],
            "pending_clarification": AgentPromptBuilder._pending_to_dict(runtime.pending_state),
        }
        return "Conversation state:\n" + json.dumps(state, ensure_ascii=False, indent=2)

    @staticmethod
    def _item_to_dict(item: ItemSnapshot | None) -> dict | None:
        if item is None:
            return None
        return {
            "item_id": item.item_id,
            "title": item.title,
            "item_type": item.item_type,
            "summary": item.summary,
            "rank": item.rank,
            "metadata": item.metadata,
        }

    @staticmethod
    def _event_to_dict(event: EventSnapshot) -> dict:
        return {
            "source_event_id": event.source_event_id,
            "event_type": event.event_type,
            "channel": event.channel,
            "raw_text": event.raw_text,
            "original_file_name": event.original_file_name,
            "metadata": event.metadata,
        }

    @staticmethod
    def _pending_to_dict(pending: PendingState | None) -> dict:
        if pending is None:
            return {}
        return {
            "pending_id": pending.pending_id,
            "type": pending.payload.get("type") or pending.kind,
            "kind": pending.kind,
            "question": pending.question,
            "choices": list(pending.choices),
            **pending.payload,
        }
