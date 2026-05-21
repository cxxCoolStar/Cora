from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from core.agent.runtime_state import ConversationRuntimeState
from core.schemas.execution import ExecutionHints

CONVERSATION_EXECUTION_MODE = "conversation"
JOB_EXECUTION_MODE = "job_execution"
DIRECT_TOOL_PLAN_MODE = "direct_tool_plan"
BACKGROUND_EXECUTION_PROMPT_GUIDANCE = (
    "This turn is a background scheduled execution, not a live user chat. "
    "Treat the input as an offline task instruction. Do the best useful work you can with the available tools and current context. "
    "Do not ask follow-up questions, do not create or manage reminders from inside this run, and do not leave the session waiting on clarification. "
    "If you cannot make meaningful progress or there is nothing useful to send back, reply exactly with `[SILENT]`."
)
JOB_EXECUTION_ALLOWED_TOOL_NAMES = frozenset(
    {
        "list_files",
        "search_files",
        "read_file",
        "web_search",
        "web_fetch",
        "skills_list",
        "skill_view",
        "skill_run",
        "search_sessions",
    }
)


@dataclass(frozen=True, slots=True)
class ExecutionPolicy:
    mode: str
    session_kind: str
    background_execution: bool
    allow_clarification: bool
    allowed_tool_names: frozenset[str] | None = None
    clarify_suppressed_reply: str | None = None
    clarify_policy_tag: str | None = None
    blocked_tool_policy_tag: str | None = None

    def allows_tool(self, tool_name: str | None) -> bool:
        normalized_tool_name = str(tool_name or "").strip()
        if not normalized_tool_name:
            return False
        if self.allowed_tool_names is None:
            return True
        return normalized_tool_name in self.allowed_tool_names

    def filter_tool_specs(self, tool_specs: Iterable[Any]) -> list[Any]:
        return [
            spec
            for spec in tool_specs
            if self.allows_tool(getattr(spec, "name", None))
        ]

    def normalize_disposition(self, *, disposition: str) -> str:
        normalized_disposition = str(disposition or "").strip() or "respond"
        if normalized_disposition != "clarify":
            return normalized_disposition
        if self.allow_clarification:
            return normalized_disposition
        return "respond"

    def suppressed_clarification_reply(
        self,
        *,
        hints: ExecutionHints | None = None,
        metadata: Mapping[str, Any] | None = None,
        fallback_reply: str,
    ) -> str:
        override_reply = str((hints.override_reply if hints is not None else None) or "").strip()
        if override_reply:
            return override_reply
        if metadata is not None:
            legacy_override = str(metadata.get("background_execution_reply") or "").strip()
            if legacy_override:
                return legacy_override
        if self.clarify_suppressed_reply:
            return self.clarify_suppressed_reply
        return fallback_reply

    def blocked_tool_reply(self, *, tool_name: str) -> str | None:
        normalized_tool_name = str(tool_name or "").strip()
        if not normalized_tool_name or self.allows_tool(normalized_tool_name):
            return None
        if not self.background_execution:
            return None
        return (
            f"{normalized_tool_name} is unavailable during background scheduled execution. "
            "Use the tools that are available in this job session, and never create or manage reminders from inside the run."
        )

    def context_patch(self) -> dict[str, Any]:
        return {
            "execution_mode": self.mode,
            "background_execution": self.background_execution,
            "allow_clarification": self.allow_clarification,
        }

    def prompt_sections(self) -> list[tuple[str, str]]:
        if not self.background_execution:
            return []
        return [("Background execution policy", BACKGROUND_EXECUTION_PROMPT_GUIDANCE)]

    def runtime_summary_lines(self) -> list[str]:
        return [
            f"execution_mode={self.mode}",
            f"background_execution={'true' if self.background_execution else 'false'}",
            f"allow_clarification={'true' if self.allow_clarification else 'false'}",
            f"tool_surface={'restricted' if self.allowed_tool_names is not None else 'full'}",
        ]


@dataclass(slots=True)
class ExecutionPolicyResolver:
    job_execution_allowed_tool_names: frozenset[str] = JOB_EXECUTION_ALLOWED_TOOL_NAMES

    def for_runtime(
        self,
        runtime: ConversationRuntimeState,
        *,
        mode: str | None = None,
    ) -> ExecutionPolicy:
        runtime_mode = str(getattr(runtime, "execution_mode", "") or "").strip() or None
        return self.for_session_kind(runtime.session_kind, mode=mode or runtime_mode)

    def for_context(
        self,
        context: Mapping[str, Any] | None,
        *,
        mode: str | None = None,
    ) -> ExecutionPolicy:
        payload = dict(context or {})
        context_mode = str(payload.get("execution_mode") or "").strip() or None
        return self.for_session_kind(
            payload.get("session_kind"),
            mode=mode or context_mode,
        )

    def for_session_kind(
        self,
        session_kind: str | None,
        *,
        mode: str | None = None,
    ) -> ExecutionPolicy:
        normalized_session_kind = str(session_kind or "conversation").strip() or "conversation"
        background_execution = normalized_session_kind == "job_execution"
        resolved_mode = self._resolve_mode(
            session_kind=normalized_session_kind,
            mode=mode,
        )
        if background_execution:
            return ExecutionPolicy(
                mode=resolved_mode,
                session_kind=normalized_session_kind,
                background_execution=True,
                allow_clarification=False,
                allowed_tool_names=self.job_execution_allowed_tool_names,
                clarify_suppressed_reply="[SILENT]",
                clarify_policy_tag="no_clarify",
                blocked_tool_policy_tag="restricted_tools",
            )
        return ExecutionPolicy(
            mode=resolved_mode,
            session_kind=normalized_session_kind,
            background_execution=False,
            allow_clarification=True,
        )

    def default_mode_for_runtime(self, runtime: ConversationRuntimeState) -> str:
        runtime_mode = str(getattr(runtime, "execution_mode", "") or "").strip()
        if runtime_mode in {
            CONVERSATION_EXECUTION_MODE,
            JOB_EXECUTION_MODE,
            DIRECT_TOOL_PLAN_MODE,
        }:
            return runtime_mode
        return self.default_mode_for_session_kind(runtime.session_kind)

    @staticmethod
    def default_mode_for_session_kind(session_kind: str | None) -> str:
        normalized_session_kind = str(session_kind or "conversation").strip() or "conversation"
        if normalized_session_kind == "job_execution":
            return JOB_EXECUTION_MODE
        return CONVERSATION_EXECUTION_MODE

    def _resolve_mode(self, *, session_kind: str, mode: str | None) -> str:
        normalized_mode = str(mode or "").strip()
        if normalized_mode in {
            CONVERSATION_EXECUTION_MODE,
            JOB_EXECUTION_MODE,
            DIRECT_TOOL_PLAN_MODE,
        }:
            return normalized_mode
        return self.default_mode_for_session_kind(session_kind)


__all__ = [
    "CONVERSATION_EXECUTION_MODE",
    "DIRECT_TOOL_PLAN_MODE",
    "ExecutionPolicy",
    "ExecutionPolicyResolver",
    "JOB_EXECUTION_ALLOWED_TOOL_NAMES",
    "JOB_EXECUTION_MODE",
]
