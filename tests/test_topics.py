from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.llm.base import ModelClient  # noqa: E402
from core.schemas.message import Message  # noqa: E402
from core.schemas.model import ModelResponse  # noqa: E402
from core.storage.models import TopicRecord  # noqa: E402
from core.schemas.tool import ToolSpec  # noqa: E402
from core.topics.classifier import TopicClassifier  # noqa: E402
from core.topics.selector import TopicSelector, TopicSelectorInput  # noqa: E402


class StubModelClient(ModelClient):
    def __init__(self, response_text: str) -> None:
        self.response_text = response_text

    def generate(self, *, messages: list[Message], tools: list[ToolSpec]) -> ModelResponse:
        return ModelResponse(assistant_text=self.response_text)


def test_topic_query_resolution_prefers_llm_output():
    classifier = TopicClassifier(
        model_client=StubModelClient('{"topic_slugs":["网络配置"],"reason":"Query is asking about intranet/network settings."}')
    )
    topics = [
        TopicRecord(session_id="s1", name="面试", slug="面试", summary="面试资料", tags_json=["interview"]),
        TopicRecord(session_id="s1", name="网络配置", slug="网络配置", summary="内网和DNS配置", tags_json=["network"]),
    ]

    decision = classifier.resolve_query_to_topics(
        query="帮我查一下我之前保存的内网文件",
        existing_topics=topics,
        limit=3,
    )

    assert decision.topic_slugs == ["网络配置"]


class FakeTopicRepository:
    def __init__(self, topics: list[TopicRecord]) -> None:
        self.topics = topics

    def list_by_session(self, *, session_id: str) -> list[TopicRecord]:
        return [topic for topic in self.topics if topic.session_id == session_id]

    def list_all(self) -> list[TopicRecord]:
        return list(self.topics)


def test_topic_selector_prefers_rule_match_from_filesystem_topics(tmp_path: Path):
    archive_root = tmp_path / "archive"
    (archive_root / "topics" / "personal-photos").mkdir(parents=True)

    class NoopClassifier:
        def classify(self, *args, **kwargs):
            raise AssertionError("classifier should not run for a high-confidence rule match")

    selector = TopicSelector(
        classifier=NoopClassifier(),
        topic_repository=FakeTopicRepository([]),
        archive_root=archive_root,
    )

    result = selector.select(
        session_id="s1",
        item_input=TopicSelectorInput(
            item_type="image",
            title="wechat_image",
            description="A portrait photo of a young woman in a garden scene.",
            user_note="Taken at Zhujiang Park",
        ),
    )

    assert result.slug == "personal-photos"
    assert result.source == "rule"
    assert result.confidence == "high"


def test_topic_selector_falls_back_to_llm_when_rules_are_ambiguous():
    classifier = TopicClassifier(
        model_client=StubModelClient(
            '{"topic_name":"旅行照片","slug":"travel-photos","summary":"旅行途中拍摄的照片。","tags":["旅行","照片"],"reason":"内容明显是旅行场景。"}'
        )
    )
    topics = [
        TopicRecord(session_id="s1", name="个人照片", slug="personal-photos", summary="个人日常照片", tags_json=["照片"]),
        TopicRecord(session_id="s1", name="工作文档", slug="work-docs", summary="工作资料", tags_json=["文档"]),
    ]
    selector = TopicSelector(
        classifier=classifier,
        topic_repository=FakeTopicRepository(topics),
    )

    result = selector.select(
        session_id="s1",
        item_input=TopicSelectorInput(
            item_type="image",
            title="trip_memory",
            description="Beachside travel snapshot during a holiday trip.",
        ),
    )

    assert result.slug == "travel-photos"
    assert result.source == "llm"


def test_topic_selector_alias_map_can_match_chinese_photo_phrases():
    classifier = TopicClassifier(
        model_client=StubModelClient(
            '{"topic_name":"未使用","slug":"unused","summary":"","tags":[],"reason":"unused"}'
        )
    )
    topics = [
        TopicRecord(session_id="s1", name="个人照片", slug="personal-photos", summary="个人日常照片", tags_json=["照片"]),
    ]
    selector = TopicSelector(
        classifier=classifier,
        topic_repository=FakeTopicRepository(topics),
    )

    result = selector.select(
        session_id="s1",
        item_input=TopicSelectorInput(
            item_type="image",
            title="wechat_image",
            description="户外拍摄的人像照片",
            user_note="这是我女朋友照片，在公园拍的",
        ),
    )

    assert result.slug == "personal-photos"
    assert result.source == "rule"
