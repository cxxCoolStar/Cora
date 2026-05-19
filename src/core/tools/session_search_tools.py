from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Any

from core.storage.repositories import ChannelSessionMapRepository, MessageRepository, SessionSummaryRepository


DEFAULT_RESULT_LIMIT = 5
MAX_RESULT_LIMIT = 8
MAX_QUERY_CHARS = 400
MAX_SESSIONS_TO_SCAN = 12
MAX_MESSAGES_PER_SESSION = 40
MAX_EXCERPT_CHARS = 240
MAX_PHRASE_TOKENS = 5
SUMMARY_SEGMENT_SEPARATOR = " | "


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
            segments = self._summary_segments(structured)
            haystack = self._summary_haystack(segments)
            score = self._score(query=query, text=haystack)
            if score <= 0:
                continue
            hits.append(
                SessionSearchHit(
                    session_id=session_id,
                    source="summary",
                    excerpt=self._summary_excerpt(query=query, segments=segments, haystack=haystack),
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
    def _summary_segments(summary: dict[str, Any]) -> list[str]:
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
        return parts

    @staticmethod
    def _summary_haystack(segments: list[str]) -> str:
        return SUMMARY_SEGMENT_SEPARATOR.join(segments)

    @classmethod
    def _summary_excerpt(cls, *, query: str, segments: list[str], haystack: str) -> str:
        best_segment = ""
        best_score = 0
        for segment in segments:
            segment_score = cls._score(query=query, text=segment)
            if segment_score > best_score:
                best_score = segment_score
                best_segment = segment
        if best_segment and best_score > 0:
            return cls._excerpt_for_query(text=best_segment, query=query)
        return cls._clip_text(haystack)

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
        query_tokens = SessionSearchToolStore._query_tokens(lowered_query)
        score += SessionSearchToolStore._phrase_match_bonus(
            query_tokens=query_tokens,
            haystack=haystack,
        )
        for token in query_tokens:
            if token in haystack:
                score += len(token) ** 2
        return score

    @staticmethod
    def _query_tokens(query: str) -> list[str]:
        lowered_query = SessionSearchToolStore._normalize_text(query).lower()
        if not lowered_query:
            return []
        alnum_tokens = re.findall(r"[a-z0-9]+(?:[-.][a-z0-9]+)*", lowered_query)
        if alnum_tokens:
            return alnum_tokens
        return [part for part in lowered_query.split() if part]

    @staticmethod
    def _query_phrases(query_tokens: list[str]) -> list[str]:
        phrases: list[str] = []
        seen_phrases: set[str] = set()
        max_tokens = min(MAX_PHRASE_TOKENS, len(query_tokens))
        for length in range(max_tokens, 1, -1):
            for index in range(0, len(query_tokens) - length + 1):
                phrase = " ".join(query_tokens[index : index + length])
                if phrase in seen_phrases:
                    continue
                seen_phrases.add(phrase)
                phrases.append(phrase)
        return phrases

    @staticmethod
    def _phrase_match_bonus(*, query_tokens: list[str], haystack: str) -> int:
        if len(query_tokens) < 2:
            return 0
        bonus = 0
        index = 0
        while index < len(query_tokens) - 1:
            max_length = min(MAX_PHRASE_TOKENS, len(query_tokens) - index)
            matched_length = 0
            for length in range(max_length, 1, -1):
                phrase = " ".join(query_tokens[index : index + length])
                if phrase in haystack:
                    compact_phrase = phrase.replace(" ", "")
                    bonus += len(compact_phrase) ** 2 + length * 12
                    matched_length = length
                    break
            if matched_length:
                index += matched_length
            else:
                index += 1
        return bonus

    @classmethod
    def _excerpt_for_query(cls, *, text: str, query: str) -> str:
        compact = cls._normalize_text(text)
        if len(compact) <= MAX_EXCERPT_CHARS:
            return compact
        span = cls._query_match_span(text=compact, query=query)
        if span is None:
            return cls._clip_text(compact)
        match_start, match_end = span
        body_budget = MAX_EXCERPT_CHARS - 6
        if body_budget <= 0:
            return cls._clip_text(compact)
        match_center = match_start + ((match_end - match_start) // 2)
        start = max(0, match_center - (body_budget // 2))
        end = min(len(compact), start + body_budget)
        if end - start < body_budget:
            start = max(0, end - body_budget)
        prefix = "..." if start > 0 else ""
        suffix = "..." if end < len(compact) else ""
        body_budget = MAX_EXCERPT_CHARS - len(prefix) - len(suffix)
        if body_budget <= 0:
            return cls._clip_text(compact)
        if end - start > body_budget:
            if match_end - match_start >= body_budget:
                start = match_start
            else:
                start = max(0, min(start, match_start - max(0, (body_budget - (match_end - match_start)) // 2)))
            end = min(len(compact), start + body_budget)
            if match_end > end:
                end = min(len(compact), match_end)
                start = max(0, end - body_budget)
            prefix = "..." if start > 0 else ""
            suffix = "..." if end < len(compact) else ""
        body = compact[start:end].strip()
        return f"{prefix}{body}{suffix}"

    @classmethod
    def _query_match_span(cls, *, text: str, query: str) -> tuple[int, int] | None:
        lowered_text = cls._normalize_text(text).lower()
        lowered_query = cls._normalize_text(query).lower()
        if not lowered_text or not lowered_query:
            return None
        query_tokens = cls._query_tokens(lowered_query)
        for phrase in cls._query_phrases(query_tokens):
            index = lowered_text.find(phrase)
            if index >= 0:
                return (index, index + len(phrase))
        full_index = lowered_text.find(lowered_query)
        if full_index >= 0:
            return (full_index, full_index + len(lowered_query))
        for token in query_tokens:
            index = lowered_text.find(token)
            if index >= 0:
                return (index, index + len(token))
        return None

    @staticmethod
    def _normalize_text(value: str) -> str:
        return " ".join(str(value or "").split())

    @staticmethod
    def _clip_text(value: str) -> str:
        compact = SessionSearchToolStore._normalize_text(value)
        if len(compact) <= MAX_EXCERPT_CHARS:
            return compact
        return compact[: MAX_EXCERPT_CHARS - 3].rstrip() + "..."
