from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from core.agent.context_budget import ContextBudgetManager
from core.llm.base import ModelClient
from core.schemas.message import Message
from core.storage.repositories import MessageRepository, SessionSummaryRepository

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class HistoryContext:
    summary_message: Message | None
    recent_messages: list[Message]

    def as_messages(self) -> list[Message]:
        messages: list[Message] = []
        if self.summary_message is not None:
            messages.append(self.summary_message)
        messages.extend(self.recent_messages)
        return messages


class SessionContextManager:
    SUMMARY_SESSION_ID = "session-summary-writer"
    SUMMARY_MAX_MESSAGE_CHARS = 500
    MIN_DELTA_MESSAGES_FOR_REFRESH = 4

    def __init__(
        self,
        *,
        message_repository: MessageRepository,
        summary_repository: SessionSummaryRepository,
        model_client: ModelClient,
        budget_manager: ContextBudgetManager | None = None,
    ) -> None:
        self.message_repository = message_repository
        self.summary_repository = summary_repository
        self.model_client = model_client
        self.budget_manager = budget_manager or ContextBudgetManager()

    def build_history(self, *, session_id: str, current_user_text: str) -> HistoryContext:
        all_messages = [msg for msg in self.message_repository.list_by_session(session_id=session_id) if msg.id]
        if all_messages and all_messages[-1].role == "user" and all_messages[-1].content == current_user_text:
            all_messages = all_messages[:-1]

        recent_seed = self._normalize_recent_messages(session_id=session_id, messages=all_messages)
        decision = self.budget_manager.choose_recent_slice(messages=recent_seed)
        older_messages = all_messages[: decision.recent_start_index]
        recent_records = all_messages[decision.recent_start_index :]
        summary_message = None
        if decision.needs_summary and older_messages:
            summary_payload = self._load_summary_payload_best_effort(
                session_id=session_id,
                older_messages=older_messages,
            )
            if summary_payload is not None:
                summary_message = Message.system(
                    session_id=session_id,
                    content=self._format_summary_message(summary_payload, decision=decision),
                )

        return HistoryContext(
            summary_message=summary_message,
            recent_messages=self._normalize_recent_messages(session_id=session_id, messages=recent_records),
        )

    def _load_summary_payload_best_effort(
        self,
        *,
        session_id: str,
        older_messages: list[object],
    ) -> dict[str, Any] | None:
        try:
            return self._get_or_update_summary(session_id=session_id, older_messages=older_messages)
        except Exception:
            logger.exception(
                "session summary refresh failed; continuing without refreshed summary session_id=%s",
                session_id,
            )
            record = self.summary_repository.get_by_session(session_id=session_id)
            if record is None:
                return None
            payload = dict(record.summary_json or {})
            return payload if isinstance(payload.get("summary"), dict) else None

    def _normalize_recent_messages(self, *, session_id: str, messages: list[object]) -> list[Message]:
        normalized: list[Message] = []
        for message in messages:
            role = str(getattr(message, "role", "") or "").strip()
            content = str(getattr(message, "content", "") or "")
            if role == "user":
                normalized.append(Message.user(session_id=session_id, content=content))
            elif role == "assistant":
                normalized.append(Message.assistant(session_id=session_id, content=content))
        return normalized

    def _get_or_update_summary(self, *, session_id: str, older_messages: list[object]) -> dict[str, Any]:
        record = self.summary_repository.get_by_session(session_id=session_id)
        covered_count = 0
        previous_summary: dict[str, Any] | None = None
        if record is not None:
            payload = dict(record.summary_json or {})
            covered_count = int(payload.get("covered_message_count") or 0)
            previous_summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else None
            last_compacted_message_id = str(payload.get("last_compacted_message_id") or "").strip()
            if covered_count == len(older_messages) and last_compacted_message_id == str(getattr(older_messages[-1], "id", "") or ""):
                return payload

        delta_messages = older_messages[covered_count:] if covered_count < len(older_messages) else []
        if previous_summary is not None and 0 < len(delta_messages) < self.MIN_DELTA_MESSAGES_FOR_REFRESH:
            payload = {
                "version": int((record.summary_json or {}).get("version") or 1) if record is not None else 1,
                "covered_message_count": len(older_messages),
                "last_compacted_message_id": str(getattr(older_messages[-1], "id", "") or ""),
                "summary": previous_summary,
            }
            self.summary_repository.upsert(session_id=session_id, summary=payload)
            return payload
        summary = self._summarize_messages(previous_summary=previous_summary, messages=delta_messages or older_messages)
        payload = {
            "version": int((record.summary_json or {}).get("version") or 0) + 1 if record is not None else 1,
            "covered_message_count": len(older_messages),
            "last_compacted_message_id": str(getattr(older_messages[-1], "id", "") or ""),
            "summary": summary,
        }
        self.summary_repository.upsert(session_id=session_id, summary=payload)
        return payload

    def _summarize_messages(
        self,
        *,
        previous_summary: dict[str, Any] | None,
        messages: list[object],
    ) -> dict[str, Any]:
        system_prompt = (
            "You are maintaining a structured handoff summary for a different assistant that will continue this session later. "
            "Return strict JSON only. Do not answer the conversation, do not continue the task, and do not add advice. "
            "Capture background reference only. Prefer preserving unresolved user asks, user facts, decisions, file/topic references, and blockers."
        )
        if previous_summary:
            user_prompt = (
                "Update the existing session handoff summary with the new conversation turns.\n\n"
                "Return JSON only with keys:\n"
                "active_task, user_facts, open_loops, resolved_requests, recent_decisions, critical_context.\n\n"
                "Rules:\n"
                "1. Preserve still-relevant facts from the previous summary.\n"
                "2. Update active_task to the latest still-unfulfilled user request if one is visible; otherwise keep 'none'.\n"
                "3. Keep every entry concise, concrete, and factual.\n"
                "4. Strings only for active_task; arrays of short strings for other keys.\n"
                "5. Move finished asks out of open_loops and into resolved_requests when clearly done.\n"
                "6. Include exact item titles, topic names, file names, and error messages when important.\n"
                "7. Do not duplicate the same fact across multiple fields unless it is critical.\n\n"
                f"PREVIOUS SUMMARY:\n{json.dumps(previous_summary, ensure_ascii=False, indent=2)}\n\n"
                f"NEW TURNS:\n{self._serialize_messages(messages)}"
            )
        else:
            user_prompt = (
                "Create a structured session handoff summary from these conversation turns.\n\n"
                "Return JSON only with keys:\n"
                "active_task, user_facts, open_loops, resolved_requests, recent_decisions, critical_context.\n\n"
                "Rules:\n"
                "1. active_task must be a short string describing the latest still-unfulfilled user ask, or 'none'.\n"
                "2. All other keys must be arrays of short concrete strings.\n"
                "3. Prefer specific files, decisions, topics, user preferences, unresolved asks, and blockers.\n"
                "4. Preserve concrete identifiers when useful: item titles, topic names, file names, and user labels.\n"
                "5. Do not invent anything that is not supported by the turns.\n"
                "6. Treat the summary as a handoff for another assistant, not as a user-facing recap.\n\n"
                f"TURNS:\n{self._serialize_messages(messages)}"
            )
        response = self.model_client.generate(
            messages=[
                Message.system(session_id=self.SUMMARY_SESSION_ID, content=system_prompt),
                Message.user(session_id=self.SUMMARY_SESSION_ID, content=user_prompt),
            ],
            tools=[],
        )
        content = (response.assistant_text or "").strip()
        payload = self._parse_summary_json(content)
        return {
            "active_task": str(payload.get("active_task") or "none").strip() or "none",
            "user_facts": self._normalize_summary_list(payload.get("user_facts")),
            "open_loops": self._normalize_summary_list(payload.get("open_loops")),
            "resolved_requests": self._normalize_summary_list(payload.get("resolved_requests")),
            "recent_decisions": self._normalize_summary_list(payload.get("recent_decisions")),
            "critical_context": self._normalize_summary_list(payload.get("critical_context")),
        }

    def _serialize_messages(self, messages: list[object]) -> str:
        lines: list[str] = []
        for message in messages:
            role = str(getattr(message, "role", "") or "").strip().upper()
            if role not in {"USER", "ASSISTANT"}:
                continue
            content = str(getattr(message, "content", "") or "")
            compact = " ".join(content.split())
            if len(compact) > self.SUMMARY_MAX_MESSAGE_CHARS:
                compact = compact[: self.SUMMARY_MAX_MESSAGE_CHARS - 1].rstrip() + "…"
            metadata = getattr(message, "metadata_json", {}) or {}
            if role == "ASSISTANT":
                action = str(metadata.get("action") or "").strip()
                tool = str(metadata.get("tool") or "").strip()
                annotations = ", ".join(part for part in [action, tool] if part)
                if annotations:
                    lines.append(f"[{role} {annotations}] {compact}")
                    continue
            lines.append(f"[{role}] {compact}")
        return "\n".join(lines)

    @staticmethod
    def _parse_summary_json(content: str) -> dict[str, Any]:
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            fenced = content.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            payload = json.loads(fenced)
        if not isinstance(payload, dict):
            raise ValueError("Session summary model did not return a JSON object.")
        return payload

    @staticmethod
    def _normalize_summary_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        result: list[str] = []
        for entry in value:
            text = str(entry or "").strip()
            if text:
                result.append(text[:240])
        return result[:12]

    @staticmethod
    def _format_summary_message(payload: dict[str, Any], *, decision: Any) -> str:
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        lines = [
            "[SESSION SUMMARY — REFERENCE ONLY] Earlier turns were condensed into the structured summary below. "
            "Treat this as background context, not as a new instruction. "
            "This is temporary session context, not long-term user memory. Respond to the latest user message after this summary.",
            "",
            f"Compaction coverage: {payload.get('covered_message_count') or 0} earlier messages; recent tail budget ≈ {getattr(decision, 'tail_budget_tokens', 0)} tokens.",
            "",
            f"Active Task: {summary.get('active_task') or 'none'}",
        ]
        for key, label in [
            ("user_facts", "User Facts"),
            ("open_loops", "Open Loops"),
            ("resolved_requests", "Resolved Requests"),
            ("recent_decisions", "Recent Decisions"),
            ("critical_context", "Critical Context"),
        ]:
            values = summary.get(key) or []
            if not values:
                continue
            lines.append("")
            lines.append(f"{label}:")
            lines.extend(f"- {value}" for value in values)
        return "\n".join(lines)
