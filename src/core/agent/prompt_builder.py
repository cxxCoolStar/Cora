from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from core.agent.execution_policy import ExecutionPolicyResolver
from core.agent.runtime_state import ConversationRuntimeState, EventSnapshot, PendingSessionState
from core.agent.skill_loader import SkillDefinition
from core.schemas.message import Message
from core.user_memory import UserMemoryStore


DEFAULT_AGENT_IDENTITY = (
    "You are Cora, a general AI agent runtime for coding, local workspace tasks, research, and reusable domain workflows. "
    "Use the tools that are actually available in this session instead of assuming a capability exists. "
    "When the user explicitly asks you to remember, review, update, or forget a stable personal fact, use the user_memory tool."
)

EXECUTION_GUIDANCE = (
    "You are a tool-using assistant. Take action through available tools when they improve correctness, retrieval, editing, or state management. "
    "Do not pretend a tool action happened if it did not. If the user asks about current saved state, inspect the relevant tool-backed state instead of guessing. "
    "If the user asks about local files or repository code, prefer using list_files, search_files, and read_file before answering implementation details. "
    "If the user asks you to create or update a text file and write_file is available, use it instead of only describing the edit. "
    "If the user asks you to run a shell or terminal command and shell_exec is available, use it instead of only describing the command. "
    "If the user asks about something from earlier conversations or wants historical recall and search_sessions is available, use it instead of guessing from the current turn. "
    "If the user asks for current external information and web_search is available, use it before answering. "
    "If the user shares a URL or asks you to inspect a webpage and web_fetch is available, use it to retrieve the page content. "
    "When a project-specific workflow may exist, inspect relevant skills with skills_list or skill_view before improvising. "
    "If a loaded skill points you to an executable helper script, use skill_run with the exact script path and structured input instead of free-chatting the workflow. "
    "Do not invent script names, file paths, alternate entrypoints, or unavailable tools. "
    "When a loaded skill defines required input fields or an intent-like action selector, include those fields explicitly and do not leave them blank. "
    "Requests to manage long-term user memory must go through tools rather than plain chat replies."
)

MEMORY_GUIDANCE = (
    "Long-term user memory is stored separately from short-term conversation context. "
    "Use `user_memory` only for durable personal facts, preferences, recurring corrections, or stable reference notes the user explicitly wants remembered. "
    "Do not store temporary task progress, one-off requests, or speculative sensitive inferences as long-term memory."
)

PLANNER_GUIDANCE = (
    "You are in PLANNER mode. Do not call any tools in this turn. "
    "Respond with exactly one JSON object and no markdown prose before or after it. "
    "The JSON must match the PlanSpec shape requested in the user message "
    "(plan_id, session_id, goal, policy_profile, tasks). "
    "Each task needs task_id, title, and instruction. "
    "Use either tool_names (single worker step) or parallel_subagents (concurrent sub-runs), not both on the same task. "
    "List only registered tool names; do not execute them now. "
    "Planning workflow for complex requests: (1) add a read-only exploration task with parallel_subagents when the codebase scope is unclear, "
    "(2) add worker tasks for mutations or steps that depend on earlier findings. "
    "Worker steps (tool_names): sequential work, especially mutating tools (write_file, shell_exec, scheduled_tasks). "
    "Parallel subagents (parallel_subagents): independent read-only lookups that can run at the same time "
    "(search_files in different paths, different keywords, search_files plus web_search, etc.). "
    "How many parallel_subagents: use 1 when the user named specific files/paths or the search target is narrow; "
    "use 2-3 when multiple directories, topics, or evidence sources must be explored before implementation; "
    "use 4 only for unusually broad discovery (avoid more than 4). "
    "Give each parallel_subagents entry a distinct focus (one path, keyword, or source per entry) so work is not duplicated. "
    "Leave tool_names as [] when parallel_subagents is set. "
    "Do not use parallel_subagents when step B needs step A's output; order those as separate worker tasks instead. "
    "Set requires_review=true on mutating or high-risk worker tasks when an extra read-only review pass is warranted."
)

REVIEWER_GUIDANCE = (
    "You are in REVIEWER mode. Do not call any tools in this turn. "
    "Respond with exactly one JSON object and no markdown prose before or after it. "
    'The JSON must include keys: verdict ("accept"|"retry"|"ask_user"|"abort"), reason, '
    "and optional confidence (high|medium|low). "
    "Judge whether the worker output matches the plan task and is safe to continue. "
    "Use abort when the result is unsafe or contradicts the plan; retry only when one more worker attempt may fix it."
)

SKILLS_GUIDANCE = (
    "Skills are reusable workflow documents. If a skill matches or is even partially relevant, load it with skill_view and follow its instructions. "
    "Skills may also include supporting files under references, templates, assets, or scripts, which can be loaded with skill_view(name, file_path). "
    "When a skill provides an executable helper under scripts, run it through skill_run rather than inventing your own protocol. "
    "Use generic tools for generic tasks, and rely on skills for domain workflows or explicit reusable procedures."
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
        execution_policy_resolver: ExecutionPolicyResolver | None = None,
    ) -> None:
        self.agent_identity = agent_identity
        self.user_memory_path = user_memory_path or Path("user-memory/USER.md")
        self.user_memory_store = UserMemoryStore(self.user_memory_path)
        self.execution_policy_resolver = execution_policy_resolver or ExecutionPolicyResolver()

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
        planner_mode: bool = False,
        reviewer_mode: bool = False,
    ) -> list[Message]:
        system_parts = self._build_system_parts(
            runtime=runtime,
            skills=skills,
            upload_name=upload_name,
            delivery_available=delivery_available,
            planner_mode=planner_mode,
            reviewer_mode=reviewer_mode,
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
        planner_mode: bool = False,
        reviewer_mode: bool = False,
    ) -> list[str]:
        policy = self.execution_policy_resolver.for_runtime(runtime)
        system_parts = [self.agent_identity]
        if reviewer_mode:
            system_parts.extend(["", "Reviewer guidance:", REVIEWER_GUIDANCE])
        elif planner_mode:
            system_parts.extend(["", "Planner guidance:", PLANNER_GUIDANCE])
        else:
            system_parts.extend(["", "Execution guidance:", EXECUTION_GUIDANCE])
        for title, body in policy.prompt_sections():
            system_parts.extend(["", f"{title}:", body])
        if not planner_mode and not reviewer_mode:
            system_parts.extend(["", "Memory guidance:", MEMORY_GUIDANCE])
            system_parts.extend(["", "Skills guidance:", SKILLS_GUIDANCE])
        platform_hint = self._platform_hint(runtime=runtime, delivery_available=delivery_available)
        if platform_hint:
            system_parts.extend(["", "Platform hints:", platform_hint])

        system_parts.extend(["", "Runtime summary:"])
        system_parts.extend(f"- {line}" for line in policy.runtime_summary_lines())
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
            "Prefer generic tools for generic tasks and skills for domain workflows.",
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
