from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from core.agent.runtime_state import ConversationRuntimeState, EventSnapshot, PendingSessionState
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
    "If the user asks about local files or repository code, prefer using list_files, search_files, and read_file before answering implementation details. "
    "When a project-specific workflow may exist, inspect relevant skills with skills_list or skill_view before improvising. "
    "If a loaded skill points you to an executable helper script, use skill_run with the exact script path and structured input instead of free-chatting the workflow. "
    "Do not invent script names, file paths, or alternate entrypoints that are not explicitly listed by the loaded skill. "
    "When a loaded skill defines required input fields or an intent-like action selector, include those fields explicitly and do not leave them blank. "
    "Requests to delete saved content or manage long-term user memory must go through tools rather than plain chat replies."
)

MEMORY_GUIDANCE = (
    "Long-term user memory is stored separately from short-term conversation context. "
    "Use `user_memory` only for durable personal facts, preferences, recurring corrections, or stable reference notes the user explicitly wants remembered. "
    "Do not store temporary task progress, one-off requests, or speculative sensitive inferences as long-term memory."
)

SKILLS_GUIDANCE = (
    "Skills are reusable workflow documents. If a skill matches or is even partially relevant, load it with skill_view and follow its instructions. "
    "Skills may also include supporting files under references, templates, assets, or scripts, which can be loaded with skill_view(name, file_path). "
    "When a skill provides an executable helper under scripts, run it through skill_run rather than inventing your own protocol. "
    "For archive-core specifically, use only skill_run(name=\"archive-core\", script_path=\"scripts/archive_dispatch.py\", input={...}) for runtime archive actions such as save, search, read, delete, deliver, overview, list_topics, clarify, or resolve_pending."
)

PLATFORM_HINTS = {
    "wechat": (
        "You are on WeChat. Keep replies compact and chat-friendly. "
        "When delivery is available, you can send previously saved photos, images, and files back to the user through tools. "
        "Do not tell the user that file sending is impossible when the archive deliver capability is available."
    ),
}


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
        delivery_available: bool = False,
    ) -> list[Message]:
        system_parts = self._build_system_parts(
            runtime=runtime,
            skills=skills,
            upload_name=upload_name,
            delivery_available=delivery_available,
        )
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
        delivery_available: bool,
    ) -> list[str]:
        system_parts = [self.agent_identity]
        system_parts.extend(["", "Execution guidance:", EXECUTION_GUIDANCE])
        system_parts.extend(["", "Memory guidance:", MEMORY_GUIDANCE])
        system_parts.extend(["", "Skills guidance:", SKILLS_GUIDANCE])
        platform_hint = self._platform_hint(runtime=runtime, delivery_available=delivery_available)
        if platform_hint:
            system_parts.extend(["", "Platform hints:", platform_hint])

        system_parts.extend(["", "Runtime summary:"])
        system_parts.extend(f"- {line}" for line in runtime.summary_lines())

        user_memory_block = self._load_user_memory_block()
        if user_memory_block:
            system_parts.extend(["", "User memory snapshot:", user_memory_block])

        skill_lines = self._format_skills_index(skills)
        if skill_lines:
            system_parts.extend(["", "Shared skills summary:"])
            system_parts.extend(skill_lines)

        if upload_name:
            system_parts.extend(["", "Upload hint:", upload_name])

        state_block = self._format_state_block(runtime)
        system_parts.extend(["", "Structured conversation state:", state_block])
        system_parts.extend(["", "Conversation state:", state_block])
        return system_parts

    @staticmethod
    def _current_channel(runtime: ConversationRuntimeState) -> str | None:
        for event in reversed(runtime.recent_events):
            channel = (event.channel or "").strip().lower()
            if channel:
                return channel
        return None

    def _platform_hint(self, *, runtime: ConversationRuntimeState, delivery_available: bool) -> str:
        channel = self._current_channel(runtime)
        if not channel:
            return ""
        hint = PLATFORM_HINTS.get(channel)
        if not hint:
            return ""
        if delivery_available:
            return hint
        if channel == "wechat":
            return (
                "You are on WeChat. Keep replies compact and chat-friendly. "
                "This session may not currently have native file-delivery wiring, so avoid promising that a saved file was sent unless a tool actually confirms it."
            )
        return hint

    def _load_user_memory_block(self) -> str:
        return self.user_memory_store.read_text()

    @staticmethod
    def _format_skills_index(skills: Iterable[SkillDefinition]) -> list[str]:
        grouped: dict[str, list[SkillDefinition]] = {}
        for skill in skills:
            grouped.setdefault(skill.category or "general", []).append(skill)

        if not grouped:
            return []

        lines = [
            "Before replying, scan the skills below. If a skill is relevant, you must load it with skill_view(name) before relying on memory or ad-hoc reasoning.",
            "Use skills_list if you need to re-check the available skills from a tool call. Use skill_view(name, file_path) to load supporting files when a skill points you there.",
            "When a skill includes an executable helper script, run it with skill_run(name, script_path, input). Do not invent script paths.",
            "If the loaded skill defines required input fields, pass them explicitly and do not leave intent-like fields blank.",
            "archive-core runtime turns must use skill_run(name=\"archive-core\", script_path=\"scripts/archive_dispatch.py\", input={...}).",
            "<available_skills>",
        ]
        for category in sorted(grouped):
            lines.append(f"{category}:")
            seen: set[str] = set()
            for skill in sorted(grouped[category], key=lambda entry: entry.name):
                if skill.name in seen:
                    continue
                seen.add(skill.name)
                description = skill.description or "No description provided."
                lines.append(f"- {skill.name}: {description}")
        lines.append("</available_skills>")
        lines.append("Only skip skill_view when none of these skills are genuinely relevant.")
        return lines

    @staticmethod
    def _format_state_block(runtime: ConversationRuntimeState) -> str:
        state = {
            "last_action": runtime.last_action,
            "skill_state": runtime.skill_state,
            "recent_events": [AgentPromptBuilder._event_to_dict(event) for event in runtime.recent_events[:5]],
            "pending_state": AgentPromptBuilder._pending_to_dict(runtime.pending_state),
        }
        return json.dumps(state, ensure_ascii=False, indent=2)

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
    def _pending_to_dict(pending: PendingSessionState | None) -> dict:
        if pending is None:
            return {}
        return {
            "pending_id": pending.pending_id,
            "skill_name": pending.skill_name,
            "type": pending.payload.get("type") or pending.kind,
            "kind": pending.kind,
            "question": pending.question,
            "choices": list(pending.choices),
            **pending.payload,
        }
