from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from core.storage.models import TopicRecord
from core.storage.repositories import TopicRepository
from core.topics.classifier import TopicClassifier, TopicDecision


@dataclass(slots=True)
class TopicSelectorInput:
    item_type: str
    title: str
    summary: str = ""
    description: str = ""
    user_note: str = ""
    keywords: list[str] | None = None


@dataclass(slots=True)
class TopicSelectorResult:
    topic_name: str
    slug: str
    summary: str
    tags: list[str]
    reason: str
    source: str
    confidence: str


@dataclass(slots=True)
class TopicCandidate:
    name: str
    slug: str
    summary: str
    tags: list[str]


class TopicSelector:
    ALIAS_MAP: dict[str, list[str]] = {
        "personal-photos": [
            "照片",
            "图片",
            "自拍",
            "人像",
            "写真",
            "女朋友照片",
            "男朋友照片",
            "公园照",
            "生活照",
            "portrait",
            "selfie",
            "photo",
            "photos",
            "picture",
            "image",
        ],
        "travel-photos": [
            "旅行照",
            "旅游照",
            "旅拍",
            "风景照",
            "beach",
            "trip",
            "travel",
            "holiday",
            "vacation",
        ],
        "documents": [
            "文档",
            "资料",
            "说明书",
            "合同",
            "pdf",
            "doc",
            "document",
        ],
        "bookmarks": [
            "网页",
            "网站",
            "链接",
            "文章链接",
            "url",
            "link",
            "bookmark",
        ],
    }

    def __init__(
        self,
        *,
        classifier: TopicClassifier,
        topic_repository: TopicRepository,
        archive_root: Path | None = None,
    ) -> None:
        self.classifier = classifier
        self.topic_repository = topic_repository
        self.archive_root = Path(archive_root).expanduser().resolve() if archive_root is not None else None

    def select(self, *, session_id: str, item_input: TopicSelectorInput) -> TopicSelectorResult:
        candidates = self._list_candidates(session_id=session_id)
        scored = self._score_candidates(item_input=item_input, candidates=candidates)
        if scored:
            best_candidate, best_score = scored[0]
            next_score = scored[1][1] if len(scored) > 1 else 0
            if self._is_high_confidence(best_candidate=best_candidate, best_score=best_score, next_score=next_score):
                return TopicSelectorResult(
                    topic_name=best_candidate.name,
                    slug=best_candidate.slug,
                    summary=best_candidate.summary,
                    tags=list(best_candidate.tags),
                    reason=f"Matched existing topic `{best_candidate.slug}` via selector rules (score={best_score}).",
                    source="rule",
                    confidence="high",
                )

        existing_topics = [
            TopicRecord(
                session_id=session_id,
                name=candidate.name,
                slug=candidate.slug,
                summary=candidate.summary,
                tags_json=list(candidate.tags),
            )
            for candidate in candidates
        ]
        decision = self.classifier.classify(
            title=item_input.title,
            normalized_text=self._normalized_text(item_input),
            existing_topics=existing_topics,
        )
        confidence = "medium" if any(candidate.slug == decision.slug for candidate in candidates) else "low"
        return self._from_decision(decision=decision, source="llm", confidence=confidence)

    def _list_candidates(self, *, session_id: str) -> list[TopicCandidate]:
        candidates: list[TopicCandidate] = []
        seen: set[str] = set()

        if self.archive_root is not None:
            topics_root = self.archive_root / "topics"
            if topics_root.exists():
                for path in sorted(topics_root.iterdir()):
                    if not path.is_dir():
                        continue
                    slug = path.name.strip()
                    if not slug or slug in seen:
                        continue
                    seen.add(slug)
                    candidates.append(
                        TopicCandidate(
                            name=slug.replace("-", " ").title(),
                            slug=slug,
                            summary="",
                            tags=[],
                        )
                    )

        for topic in self.topic_repository.list_by_session(session_id=session_id):
            slug = str(topic.slug or "").strip()
            if not slug or slug in seen:
                continue
            seen.add(slug)
            candidates.append(
                TopicCandidate(
                    name=topic.name,
                    slug=slug,
                    summary=topic.summary or "",
                    tags=list(topic.tags_json or []),
                )
            )

        for topic in self.topic_repository.list_all():
            slug = str(topic.slug or "").strip()
            if not slug or slug in seen:
                continue
            seen.add(slug)
            candidates.append(
                TopicCandidate(
                    name=topic.name,
                    slug=slug,
                    summary=topic.summary or "",
                    tags=list(topic.tags_json or []),
                )
            )
        return candidates

    def _score_candidates(
        self,
        *,
        item_input: TopicSelectorInput,
        candidates: list[TopicCandidate],
    ) -> list[tuple[TopicCandidate, int]]:
        haystack = self._normalized_text(item_input).lower()
        compact_haystack = re.sub(r"\s+", "", haystack)
        preferred_tokens = self._preferred_tokens(item_type=item_input.item_type)
        scored: list[tuple[TopicCandidate, int]] = []
        for candidate in candidates:
            score = 0
            name = (candidate.name or "").lower()
            slug = (candidate.slug or "").lower()
            summary = (candidate.summary or "").lower()
            tags = [str(tag).lower() for tag in candidate.tags]
            compact_slug = re.sub(r"[-_.\s]+", "", slug)
            if compact_slug and compact_slug in compact_haystack:
                score += len(compact_slug) ** 2
            for token in self._candidate_tokens(candidate):
                if token and token in haystack:
                    score += len(token) ** 2
            for alias in self.ALIAS_MAP.get(candidate.slug, []):
                alias_lower = alias.lower()
                if alias_lower and alias_lower in haystack:
                    score += max(9, len(alias_lower) ** 2)
            if any(preferred in slug or preferred in name or preferred in summary or preferred in tags for preferred in preferred_tokens):
                score += 4
            if score > 0:
                scored.append((candidate, score))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored

    def _candidate_tokens(self, candidate: TopicCandidate) -> list[str]:
        tokens: list[str] = []
        for source in [candidate.slug, candidate.name, candidate.summary, *candidate.tags]:
            tokens.extend(
                token
                for token in re.split(r"[\s\-_.，,/:；;（）()]+", str(source).lower())
                if token
            )
        expanded: list[str] = []
        for token in tokens:
            expanded.append(token)
            if token.endswith("s") and len(token) > 4:
                expanded.append(token[:-1])
        return list(dict.fromkeys(expanded))

    def _preferred_tokens(self, *, item_type: str) -> list[str]:
        lowered = (item_type or "").lower()
        if lowered == "image":
            return ["photo", "photos", "image", "picture", "portrait", "照片", "图片", "人像", "写真"]
        if lowered in {"document", "pdf", "doc"}:
            return ["doc", "document", "docs", "pdf", "文档", "资料"]
        if lowered in {"link", "url"}:
            return ["link", "url", "bookmark", "网页", "网站"]
        return []

    def _is_high_confidence(self, *, best_candidate: TopicCandidate, best_score: int, next_score: int) -> bool:
        if best_score < 16:
            return False
        if next_score and best_score < next_score + 4:
            return False
        return True

    def _normalized_text(self, item_input: TopicSelectorInput) -> str:
        parts = [
            item_input.title,
            item_input.summary,
            item_input.description,
            item_input.user_note,
            " ".join(item_input.keywords or []),
        ]
        return "\n".join(part.strip() for part in parts if part and part.strip())

    def _from_decision(
        self,
        *,
        decision: TopicDecision,
        source: str,
        confidence: str,
    ) -> TopicSelectorResult:
        return TopicSelectorResult(
            topic_name=decision.topic_name,
            slug=decision.slug,
            summary=decision.summary,
            tags=list(decision.tags),
            reason=decision.reason,
            source=source,
            confidence=confidence,
        )
