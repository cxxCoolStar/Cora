from __future__ import annotations

import json
from typing import Iterable

from core.agent.runtime_state import ConversationRuntimeState, EventSnapshot, ItemSnapshot, PendingState
from core.agent.skill_loader import SkillDefinition
from core.schemas.message import Message


DEFAULT_AGENT_IDENTITY = (
    "You are Cora, a general AI agent runtime evolving from the legacy ClawBot flow. "
    "Prefer using the shared archive filesystem contract and reusable skills rather than legacy product-specific assumptions."
)


class AgentPromptBuilder:
    def __init__(self, *, agent_identity: str = DEFAULT_AGENT_IDENTITY) -> None:
        self.agent_identity = agent_identity

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
        system_parts = [self.agent_identity, "", "Runtime state:"]
        system_parts.extend(f"- {line}" for line in runtime.summary_lines())
        skill_lines = self._format_skills(skills)
        if skill_lines:
            system_parts.extend(["", "Shared skills:"])
            system_parts.extend(skill_lines)
        if upload_name:
            system_parts.extend(["", f"Current upload: {upload_name}"])
        system_parts.extend(["", self._format_state_block(runtime)])

        messages = [Message.system(session_id=session_id, content="\n".join(system_parts))]
        if history:
            messages.extend(history)
        messages.append(Message.user(session_id=session_id, content=user_text))
        return messages

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
            "primary_focus": AgentPromptBuilder._item_to_dict(runtime.primary_focus),
            "last_action": runtime.last_action,
            "working_set": [AgentPromptBuilder._item_to_dict(item) for item in runtime.working_set[:5]],
            "recent_items": [AgentPromptBuilder._item_to_dict(item) for item in runtime.recent_items[:5]],
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
