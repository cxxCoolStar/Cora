from __future__ import annotations

from dataclasses import dataclass
import logging

from core.storage.models import ItemRecord, TopicRecord
from core.storage.repositories import ItemRepository, TopicActivityRepository, TopicItemRepository, TopicRepository
from core.topics.classifier import TopicClassifier

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TopicAssignment:
    topic: TopicRecord
    created: bool
    reason: str


class TopicOrganizerService:
    def __init__(
        self,
        *,
        classifier: TopicClassifier,
        topic_repository: TopicRepository,
        topic_item_repository: TopicItemRepository,
        topic_activity_repository: TopicActivityRepository,
        item_repository: ItemRepository,
    ) -> None:
        self.classifier = classifier
        self.topic_repository = topic_repository
        self.topic_item_repository = topic_item_repository
        self.topic_activity_repository = topic_activity_repository
        self.item_repository = item_repository

    def assign_item_to_topic(self, *, session_id: str, item: ItemRecord) -> TopicAssignment:
        existing_topics = self.topic_repository.list_all()
        logger.info(
            "topic organizer assign_start session_id=%s item_id=%s title=%s existing_topics=%d",
            session_id,
            item.id,
            item.title[:120],
            len(existing_topics),
        )
        decision = self.classifier.classify(
            title=item.title,
            normalized_text=item.normalized_text,
            existing_topics=existing_topics,
        )
        topic = self.topic_repository.find_by_slug(slug=decision.slug)
        created = False
        if topic is None:
            topic = self.topic_repository.create(
                session_id=session_id,
                name=decision.topic_name,
                slug=decision.slug,
                summary=decision.summary or item.summary,
                tags=decision.tags,
                metadata={"source": "topic_classifier"},
            )
            created = True
        else:
            merged_tags = sorted(set((topic.tags_json or []) + decision.tags))
            summary = topic.summary or decision.summary or item.summary
            self.topic_repository.update_summary_and_tags(topic_id=topic.id, summary=summary[:400], tags=merged_tags)
        self.topic_item_repository.link_item(
            topic_id=topic.id,
            item_id=item.id,
            confidence="high" if created else "medium",
            reason=decision.reason,
        )
        activity = "created_topic_and_linked_item" if created else "linked_item_to_topic"
        self.topic_activity_repository.create(
            topic_id=topic.id,
            item_id=item.id,
            activity_type=activity,
            message=f"{item.title} -> {topic.name}",
            metadata={"reason": decision.reason},
        )
        logger.info(
            "topic organizer assign_done session_id=%s item_id=%s topic_id=%s topic=%s created=%s reason=%s",
            session_id,
            item.id,
            topic.id,
            topic.slug,
            created,
            decision.reason[:200],
        )
        return TopicAssignment(topic=topic, created=created, reason=decision.reason)

    def ensure_topic_index(self) -> int:
        indexed = 0
        items = self.item_repository.list_all(current_only=True)
        for item in items:
            existing_links = self.topic_item_repository.list_topics_for_item(item_id=item.id)
            if existing_links:
                continue
            self.assign_item_to_topic(session_id=item.session_id, item=item)
            indexed += 1
        if indexed:
            logger.info("topic organizer backfill_done indexed_items=%d", indexed)
        return indexed

    def search_topics(self, *, session_id: str, query: str, limit: int = 3) -> list[tuple[TopicRecord, list[ItemRecord]]]:
        backfilled = self.ensure_topic_index()
        existing_topics = self.topic_repository.list_all()
        logger.info(
            "topic organizer search_start session_id=%s query=%s existing_topics=%d limit=%d backfilled=%d",
            session_id,
            query[:200],
            len(existing_topics),
            limit,
            backfilled,
        )
        decision = self.classifier.resolve_query_to_topics(
            query=query,
            existing_topics=existing_topics,
            limit=limit,
        )
        topic_map = {topic.slug: topic for topic in existing_topics}
        topics = [topic_map[slug] for slug in decision.topic_slugs if slug in topic_map]
        results: list[tuple[TopicRecord, list[ItemRecord]]] = []
        for topic in topics:
            item_ids = self.topic_item_repository.list_item_ids_for_topic(topic_id=topic.id, limit=8)
            items = self.item_repository.list_current_by_ids(item_ids=item_ids)
            results.append((topic, items))
        logger.info(
            "topic organizer search_done session_id=%s query=%s topics=%s item_counts=%s",
            session_id,
            query[:120],
            [topic.slug for topic, _ in results],
            [len(items) for _, items in results],
        )
        return results
