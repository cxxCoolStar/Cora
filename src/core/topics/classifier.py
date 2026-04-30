from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from core.llm.base import ModelClient
from core.schemas.message import Message
from core.storage.models import TopicRecord

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TopicDecision:
    topic_name: str
    slug: str
    summary: str
    tags: list[str]
    reason: str


@dataclass(slots=True)
class TopicQueryDecision:
    topic_slugs: list[str]
    reason: str


class TopicClassifier:
    def __init__(self, model_client: ModelClient | None = None) -> None:
        self.model_client = model_client

    def classify(
        self,
        *,
        title: str,
        normalized_text: str,
        existing_topics: list[TopicRecord],
    ) -> TopicDecision:
        if self.model_client is None:
            raise RuntimeError("TopicClassifier requires an LLM model client.")
        logger.info(
            "topic classify_start title=%s existing_topics=%d preview=%s",
            title[:120],
            len(existing_topics),
            normalized_text[:160].replace("\n", " "),
        )

        existing = [
            {"name": topic.name, "slug": topic.slug, "summary": topic.summary[:120]}
            for topic in existing_topics[:12]
        ]
        prompt = (
            "You are organizing a user's small personal archive. "
            "Choose the best topic for the new item, or create a new topic if none fit.\n\n"
            "Rules:\n"
            "1. Prefer stable, reusable topic names over one-off file names.\n"
            "2. Use 1-3 Chinese words or a short bilingual technical phrase when appropriate.\n"
            "3. If an existing topic clearly matches, reuse its slug/name.\n"
            "4. If no topic fits, create a new one.\n"
            "5. Return JSON only with keys: topic_name, slug, summary, tags, reason.\n"
        )
        user_content = json.dumps(
            {
                "title": title,
                "content_preview": normalized_text[:1800],
                "existing_topics": existing,
            },
            ensure_ascii=False,
        )
        response = self.model_client.generate(
            messages=[
                Message.system(session_id="topic-classifier", content=prompt),
                Message.user(session_id="topic-classifier", content=user_content),
            ],
            tools=[],
        )
        content = (response.assistant_text or "").strip()
        logger.info("topic classify_raw_output title=%s output=%s", title[:80], content[:400])
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            fenced = content.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            try:
                payload = json.loads(fenced)
            except json.JSONDecodeError:
                raise ValueError(f"TopicClassifier returned non-JSON output: {content[:200]}")
        topic_name = str(payload.get("topic_name") or "").strip()
        slug = str(payload.get("slug") or "").strip()
        if not topic_name:
            raise ValueError("TopicClassifier did not return `topic_name`.")
        if not slug:
            slug = self._slugify(topic_name)
        tags = payload.get("tags") or []
        if not isinstance(tags, list):
            tags = []
        decision = TopicDecision(
            topic_name=topic_name,
            slug=self._slugify(slug),
            summary=str(payload.get("summary") or "").strip()[:240],
            tags=[str(tag).strip().lower() for tag in tags if str(tag).strip()][:8],
            reason=str(payload.get("reason") or "LLM topic classification"),
        )
        logger.info(
            "topic classify_done title=%s topic=%s slug=%s tags=%s reason=%s",
            title[:80],
            decision.topic_name,
            decision.slug,
            decision.tags,
            decision.reason[:200],
        )
        return decision

    def resolve_query_to_topics(
        self,
        *,
        query: str,
        existing_topics: list[TopicRecord],
        limit: int = 3,
    ) -> TopicQueryDecision:
        if self.model_client is None:
            raise RuntimeError("Topic query resolution requires an LLM model client.")
        logger.info(
            "topic query_resolve_start query=%s existing_topics=%d limit=%d",
            query[:200],
            len(existing_topics),
            limit,
        )

        existing = [
            {
                "name": topic.name,
                "slug": topic.slug,
                "summary": topic.summary[:160],
                "tags": list(topic.tags_json or [])[:8],
            }
            for topic in existing_topics[:20]
        ]
        prompt = (
            "You are choosing which existing topics best match a user's archive query.\n\n"
            "Rules:\n"
            "1. Select 0 to 3 existing topic slugs that best match the query intent.\n"
            "2. Use semantic understanding, not just literal string overlap.\n"
            "3. Prefer precise topics over broad ones.\n"
            "4. If nothing fits, return an empty list.\n"
            "5. Return strict JSON only with keys: topic_slugs, reason.\n"
        )
        user_content = json.dumps(
            {
                "query": query,
                "existing_topics": existing,
                "limit": limit,
            },
            ensure_ascii=False,
        )
        response = self.model_client.generate(
            messages=[
                Message.system(session_id="topic-query-router", content=prompt),
                Message.user(session_id="topic-query-router", content=user_content),
            ],
            tools=[],
        )
        content = (response.assistant_text or "").strip()
        logger.info("topic query_resolve_raw_output query=%s output=%s", query[:100], content[:400])
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            fenced = content.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            try:
                payload = json.loads(fenced)
            except json.JSONDecodeError:
                raise ValueError(f"Topic query resolver returned non-JSON output: {content[:200]}")
        slugs = payload.get("topic_slugs") or []
        if not isinstance(slugs, list):
            raise ValueError("Topic query resolver did not return `topic_slugs` as a list.")
        normalized = [self._slugify(str(slug)) for slug in slugs if str(slug).strip()]
        allowed = {topic.slug for topic in existing_topics}
        selected = [slug for slug in normalized if slug in allowed][:limit]
        decision = TopicQueryDecision(
            topic_slugs=selected,
            reason=str(payload.get("reason") or "LLM topic query resolution"),
        )
        logger.info(
            "topic query_resolve_done query=%s selected_slugs=%s reason=%s",
            query[:100],
            decision.topic_slugs,
            decision.reason[:200],
        )
        return decision

    @staticmethod
    def _slugify(text: str) -> str:
        lowered = text.strip().lower()
        lowered = re.sub(r"\s+", "-", lowered)
        lowered = re.sub(r"[^0-9a-z\u4e00-\u9fff\-_]+", "", lowered)
        return lowered or "misc"
