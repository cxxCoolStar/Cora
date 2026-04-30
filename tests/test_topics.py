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
