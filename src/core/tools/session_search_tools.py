from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from core.storage.repositories import ChannelSessionMapRepository, MessageRepository, SessionSummaryRepository


DEFAULT_RESULT_LIMIT = 5
MAX_RESULT_LIMIT = 8
MAX_QUERY_CHARS = 400
MAX_SESSIONS_TO_SCAN = 12
MAX_MESSAGES_PER_SESSION = 40
MAX_EXCERPT_CHARS = 240


@dataclass(slots=True)
class SessionSearchHit:
    session_id: str
    source: str
    excerpt: str
    score: int
    created_at: datetime | None = None
    role: str | None = None


@dataclass(slots=True)
class SessionSearchResult:
    query: str
    session_ids: list[str]
    hits: list[SessionSearchHit]

    def render(self) -> str:
        if not self.hits:
            return (
                f"No matches for `{self.query}` in the current or prior conversation history "
                f"across {len(self.session_ids)} session(s)."
            )
        lines = [
            f"Conversation history matches for `{self.query}` across {len(self.session_ids)} session(s):",
        ]
        for hit in self.hits:
            label = hit.source
            if hit.role:
                label = f"{label}:{hit.role}"
            timestamp = self._format_time(hit.created_at)
            lines.append(f"- [{label}] session={hit.session_id} {timestamp}: {hit.excerpt}")
        return "\n".join(lines)

    def metadata(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "sessions_scanned": list(self.session_ids),
            "hit_count": len(self.hits),
            "hits": [
                {
                    "session_id": hit.session_id,
                    "source": hit.source,
                    "role": hit.role,
                    "score": hit.score,
                    "created_at": hit.created_at.isoformat() if hit.created_at is not None else None,
                    "excerpt": hit.excerpt,
                }
                for hit in self.hits
            ],
        }

    @staticmethod
    def _format_time(value: datetime | None) -> str:
        if value is None:
            return ""
        try:
            return value.isoformat(timespec="seconds")
        except ValueError:
            return value.isoformat()


@dataclass(slots=True)
class SessionSearchToolStore:
    message_repository: MessageRepository
    summary_repository: SessionSummaryRepository
    session_map_repository: ChannelSessionMapRepository | None = None
    channel_name: str = "wechat"

    def search(self, *, session_id: str, query: str, limit: int = DEFAULT_RESULT_LIMIT) -> SessionSearchResult:
        cleaned_query = " ".join(str(query or "").split())
        if not cleaned_query:
            raise ValueError("query cannot be empty")
        if len(cleaned_query) > MAX_QUERY_CHARS:
            raise ValueError(f"query is too long; limit is {MAX_QUERY_CHARS} characters")
        result_limit = max(1, min(int(limit or DEFAULT_RESULT_LIMIT), MAX_RESULT_LIMIT))
        session_ids = self._candidate_session_ids(session_id=session_id)
        hits = self._search_summaries(session_ids=session_ids, query=cleaned_query)
        hits.extend(self._search_messages(session_ids=session_ids, query=cleaned_query))
        hits.sort(
            key=lambda hit: (
                hit.score,
                hit.created_at.isoformat() if hit.created_at is not None else "",
            ),
            reverse=True,
        )
        return SessionSearchResult(
            query=cleaned_query,
            session_ids=session_ids,
            hits=hits[:result_limit],
        )

    def _candidate_session_ids(self, *, session_id: str) -> list[str]:
        session_ids = [session_id]
        if self.session_map_repository is None:
            return session_ids
        external_user_id = self.session_map_repository.get_external_user_id(
            channel=self.channel_name,
            session_id=session_id,
        )
        if not external_user_id:
            return session_ids
        linked_session_ids = self.session_map_repository.list_session_ids_for_user(
            channel=self.channel_name,
            external_user_id=external_user_id,
            limit=MAX_SESSIONS_TO_SCAN,
        )
        for linked_session_id in linked_session_ids:
            if linked_session_id not in session_ids:
                session_ids.append(linked_session_id)
        return session_ids

    def _search_summaries(self, *, session_ids: list[str], query: str) -> list[SessionSearchHit]:
        hits: list[SessionSearchHit] = []
        for session_id in session_ids:
            record = self.summary_repository.get_by_session(session_id=session_id)
            if record is None:
                continue
            summary_payload = dict(record.summary_json or {})
            structured = summary_payload.get("summary") if isinstance(summary_payload.get("summary"), dict) else {}
            haystack = self._summary_haystack(structured)
            score = self._score(query=query, text=haystack)
            if score <= 0:
                continue
            hits.append(
                SessionSearchHit(
                    session_id=session_id,
                    source="summary",
                    excerpt=self._clip_text(haystack),
                    score=score + 20,
                    created_at=getattr(record, "updated_at", None),
                )
            )
        return hits

    def _search_messages(self, *, session_ids: list[str], query: str) -> list[SessionSearchHit]:
        hits: list[SessionSearchHit] = []
        for session_id in session_ids:
            messages = self.message_repository.list_by_session(session_id=session_id)
            for message in reversed(messages[-MAX_MESSAGES_PER_SESSION:]):
                content = self._normalize_text(getattr(message, "content", ""))
                if not content:
                    continue
                score = self._score(query=query, text=content)
                if score <= 0:
                    continue
                hits.append(
                    SessionSearchHit(
                        session_id=session_id,
                        source="message",
                        role=str(getattr(message, "role", "") or "").strip() or None,
                        excerpt=self._clip_text(content),
                        score=score,
                        created_at=getattr(message, "created_at", None),
                    )
                )
        return hits

    @staticmethod
    def _summary_haystack(summary: dict[str, Any]) -> str:
        parts: list[str] = []
        active_task = str(summary.get("active_task") or "").strip()
        if active_task and active_task.lower() != "none":
            parts.append(f"Active task: {active_task}")
        for key, label in [
            ("user_facts", "User facts"),
            ("open_loops", "Open loops"),
            ("resolved_requests", "Resolved requests"),
            ("recent_decisions", "Recent decisions"),
            ("critical_context", "Critical context"),
        ]:
            values = summary.get(key)
            if not isinstance(values, list):
                continue
            for value in values[:3]:
                text = str(value or "").strip()
                if text:
                    parts.append(f"{label}: {text}")
        return " | ".join(parts)

    @staticmethod
    def _score(*, query: str, text: str) -> int:
        haystack = SessionSearchToolStore._normalize_text(text).lower()
        lowered_query = SessionSearchToolStore._normalize_text(query).lower()
        if not haystack or not lowered_query:
            return 0
        compact_query = "".join(lowered_query.split())
        compact_haystack = haystack.replace(" ", "")
        score = 0
        if compact_query and compact_query in compact_haystack:
            score += len(compact_query) ** 2 + 25
        if lowered_query in haystack:
            score += len(lowered_query) ** 2 + 10
        for token in [part for part in lowered_query.split() if part]:
            if token in haystack:
                score += len(token) ** 2
        return score

    @staticmethod
    def _normalize_text(value: str) -> str:
        return " ".join(str(value or "").split())

    @staticmethod
    def _clip_text(value: str) -> str:
        compact = SessionSearchToolStore._normalize_text(value)
        if len(compact) <= MAX_EXCERPT_CHARS:
            return compact
        return compact[: MAX_EXCERPT_CHARS - 3].rstrip() + "..."
