from __future__ import annotations

import asyncio
from io import BytesIO
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.api.app import create_app  # noqa: E402
from core.channels.wechat.service import WechatGatewayService  # noqa: E402
from core.channels.wechat.types import WechatInboundEvent  # noqa: E402
from core.clawbot import dependencies as deps  # noqa: E402
from core.clawbot.dependencies import ClawBotContainer  # noqa: E402
from core.config import CoreSettings  # noqa: E402
from core.clawbot.intent_llm import LLMIntentResult  # noqa: E402
from core.clawbot.intent_router import IntentRouter  # noqa: E402
from core.clawbot.service import ClawBotService  # noqa: E402
from core.clawbot.tools import ArchiveToolExecutor  # noqa: E402
from core.ingestion.service import IngestionService  # noqa: E402
from core.storage.db import DatabaseManager  # noqa: E402
from core.storage.repositories import ChannelEventRepository, ChannelSessionMapRepository, ClarificationRepository, ItemChunkRepository, ItemRepository, MessageRepository, SessionRepository, TopicActivityRepository, TopicItemRepository, TopicRepository, UserSignalRepository  # noqa: E402
from core.topics.classifier import TopicClassifier  # noqa: E402
from core.topics.service import TopicOrganizerService  # noqa: E402
from core.llm.base import ModelClient  # noqa: E402
from core.schemas.message import Message  # noqa: E402
from core.schemas.model import ModelResponse  # noqa: E402
from core.schemas.tool import ToolSpec  # noqa: E402


class FakeLLMIntentClassifier:
    def __init__(self, result: LLMIntentResult | None) -> None:
        self.result = result

    def classify(self, *, text: str):
        return self.result


class StubTopicModelClient(ModelClient):
    def generate(self, *, messages: list[Message], tools: list[ToolSpec]) -> ModelResponse:
        system_prompt = messages[0].content if messages else ""
        user_text = messages[-1].content if messages else ""
        if "topic-query-router" in (messages[0].session_id if messages else "") or "existing_topics" in user_text and "query" in user_text:
            if "网络配置" in user_text or "内网" in user_text:
                return ModelResponse(assistant_text='{"topic_slugs":["网络配置"],"reason":"Query is asking about network configuration."}')
            if "面试" in user_text:
                return ModelResponse(assistant_text='{"topic_slugs":["面试"],"reason":"Query is about interview materials."}')
            if "agent" in user_text.lower() or "react" in user_text.lower() or "memory" in user_text.lower():
                return ModelResponse(assistant_text='{"topic_slugs":["ai资料"],"reason":"Query is about AI learning materials."}')
            return ModelResponse(assistant_text='{"topic_slugs":[],"reason":"No matching topic."}')
        if "network" in user_text.lower() or "内网" in user_text or "dns" in user_text.lower() or "网关" in user_text:
            return ModelResponse(assistant_text='{"topic_name":"网络配置","slug":"网络配置","summary":"内网、DNS、网关等网络配置资料。","tags":["network"],"reason":"The item is about network configuration."}')
        if "面试" in user_text:
            return ModelResponse(assistant_text='{"topic_name":"面试","slug":"面试","summary":"面试题和面试宝典资料。","tags":["interview"],"reason":"The item is about interviews."}')
        if "agent" in user_text.lower() or "react" in user_text.lower() or "memory" in user_text.lower():
            return ModelResponse(assistant_text='{"topic_name":"AI资料","slug":"ai资料","summary":"Agent、ReAct、Memory 等 AI 学习资料。","tags":["agent","ai"],"reason":"The item is about AI learning materials."}')
        return ModelResponse(assistant_text='{"topic_name":"杂项资料","slug":"杂项资料","summary":"一般资料。","tags":[],"reason":"Generic archive item."}')


def build_test_container(tmp_path: Path) -> ClawBotContainer:
    settings = CoreSettings(
        clawbot_database_path=tmp_path / "clawbot.db",
        files_storage_dir=tmp_path / "files",
    )
    database = DatabaseManager(settings.clawbot_database_url)
    session_repository = SessionRepository(database)
    message_repository = MessageRepository(database)
    item_repository = ItemRepository(database)
    item_chunk_repository = ItemChunkRepository(database)
    clarification_repository = ClarificationRepository(database)
    user_signal_repository = UserSignalRepository(database)
    topic_repository = TopicRepository(database)
    topic_item_repository = TopicItemRepository(database)
    topic_activity_repository = TopicActivityRepository(database)
    topic_organizer = TopicOrganizerService(
        classifier=TopicClassifier(model_client=StubTopicModelClient()),
        topic_repository=topic_repository,
        topic_item_repository=topic_item_repository,
        topic_activity_repository=topic_activity_repository,
        item_repository=item_repository,
    )
    ingestion_service = IngestionService(
        item_repository=item_repository,
        item_chunk_repository=item_chunk_repository,
        message_repository=message_repository,
        user_signal_repository=user_signal_repository,
        storage_dir=settings.files_storage_dir,
        topic_organizer=topic_organizer,
    )
    tool_executor = ArchiveToolExecutor(
        ingestion_service=ingestion_service,
        item_repository=item_repository,
        clarification_repository=clarification_repository,
        topic_organizer=topic_organizer,
    )
    clawbot_service = ClawBotService(
        session_repository=session_repository,
        message_repository=message_repository,
        item_repository=item_repository,
        item_chunk_repository=item_chunk_repository,
        ingestion_service=ingestion_service,
        clarification_repository=clarification_repository,
        user_signal_repository=user_signal_repository,
        topic_repository=topic_repository,
        tool_executor=tool_executor,
        topic_organizer=topic_organizer,
    )
    container = ClawBotContainer(
        settings=settings,
        database=database,
        session_repository=session_repository,
        message_repository=message_repository,
        item_repository=item_repository,
        item_chunk_repository=item_chunk_repository,
        clarification_repository=clarification_repository,
        user_signal_repository=user_signal_repository,
        topic_repository=topic_repository,
        ingestion_service=ingestion_service,
        clawbot_service=clawbot_service,
        templates_dir=str(ROOT / "src" / "core" / "api" / "templates"),
        templates_static_dir=str(ROOT / "src" / "core" / "api" / "static"),
    )
    container.initialize()
    return container


async def api_request(app, method: str, path: str, **kwargs):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.request(method, path, **kwargs)


def test_text_ingest_flow(tmp_path):
    deps._container = build_test_container(tmp_path)
    app = create_app()

    session_response = asyncio.run(api_request(app, "POST", "/sessions"))
    session_id = session_response.json()["session_id"]

    ingest_response = asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            data={"text": "请帮我保存这段 Agent and RAG interview questions for later review."},
        )
    )

    assert ingest_response.status_code == 200
    payload = ingest_response.json()
    assert payload["action"] == "capture"
    assert payload["item_id"]
    assert "Saved" in payload["reply"]

    items_response = asyncio.run(api_request(app, "GET", f"/sessions/{session_id}/items"))
    assert items_response.status_code == 200
    items = items_response.json()
    assert len(items) == 1
    assert items[0]["item_type"] == "text_note"


def test_greeting_is_not_saved_as_item(tmp_path):
    deps._container = build_test_container(tmp_path)
    app = create_app()

    session_id = asyncio.run(api_request(app, "POST", "/sessions")).json()["session_id"]
    response = asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            data={"text": "你好"},
        )
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["action"] == "chat"
    assert "可以帮你保存文本" in payload["reply"]

    items = asyncio.run(api_request(app, "GET", f"/sessions/{session_id}/items")).json()
    assert items == []


def test_link_ingest_flow(tmp_path):
    deps._container = build_test_container(tmp_path)
    app = create_app()

    session_id = asyncio.run(api_request(app, "POST", "/sessions")).json()["session_id"]
    response = asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            data={"text": "https://github.com/guoguo-tju/agent_java_offer"},
        )
    )

    assert response.status_code == 200
    item_id = response.json()["item_id"]
    detail = asyncio.run(api_request(app, "GET", f"/sessions/{session_id}/items/{item_id}"))
    assert detail.status_code == 200
    assert detail.json()["item_type"] == "link"


def test_txt_file_ingest_flow(tmp_path):
    deps._container = build_test_container(tmp_path)
    app = create_app()

    session_id = asyncio.run(api_request(app, "POST", "/sessions")).json()["session_id"]
    response = asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            files={"file": ("note.txt", BytesIO(b"hello from a saved txt file"), "text/plain")},
        )
    )

    assert response.status_code == 200
    item_id = response.json()["item_id"]
    detail = asyncio.run(api_request(app, "GET", f"/sessions/{session_id}/items/{item_id}"))
    assert detail.status_code == 200
    assert detail.json()["item_type"] == "document"


def test_text_ingest_ignores_empty_upload_filename(tmp_path):
    deps._container = build_test_container(tmp_path)
    app = create_app()

    session_id = asyncio.run(api_request(app, "POST", "/sessions")).json()["session_id"]
    response = asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            data={"text": "请保存这条测试内容"},
            files={"file": ("", BytesIO(b""), "application/octet-stream")},
        )
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["action"] == "capture"
    assert payload["item_id"]


def test_ambiguous_long_text_triggers_clarification(tmp_path):
    deps._container = build_test_container(tmp_path)
    app = create_app()

    session_id = asyncio.run(api_request(app, "POST", "/sessions")).json()["session_id"]
    long_text = "最近一个月跟同事面试了上百人，分享一些 Agent 跟 RAG 的高频面试题。\n" * 4
    response = asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            data={"text": long_text},
        )
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["action"] == "clarify"
    assert payload["needs_clarification"] is True
    assert "先保存" in payload["reply"]

    items = asyncio.run(api_request(app, "GET", f"/sessions/{session_id}/items")).json()
    assert items == []


def test_clarification_reply_can_save_pending_text(tmp_path):
    deps._container = build_test_container(tmp_path)
    app = create_app()

    session_id = asyncio.run(api_request(app, "POST", "/sessions")).json()["session_id"]
    long_text = "这是一个很长的资料内容，里面有很多后面可能会用到的信息，我先发给你。\n" * 4
    asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            data={"text": long_text},
        )
    )
    response = asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            data={"text": "保存吧"},
        )
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["action"] == "capture"
    items = asyncio.run(api_request(app, "GET", f"/sessions/{session_id}/items")).json()
    assert len(items) == 1


def test_debug_page_renders_saved_data(tmp_path):
    deps._container = build_test_container(tmp_path)
    app = create_app()

    session_id = asyncio.run(api_request(app, "POST", "/sessions")).json()["session_id"]
    asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            data={"text": "请保存 A saved debug note"},
        )
    )

    response = asyncio.run(api_request(app, "GET", f"/debug?session_id={session_id}"))

    assert response.status_code == 200
    assert "Debug Explorer" in response.text
    assert "A saved debug note" in response.text
    assert "User Signals" in response.text


def test_ingest_records_user_signals(tmp_path):
    deps._container = build_test_container(tmp_path)
    app = create_app()

    session_id = asyncio.run(api_request(app, "POST", "/sessions")).json()["session_id"]
    asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            data={"text": "请保存这段 Agent 和 RAG 面试题资料"},
        )
    )

    response = asyncio.run(api_request(app, "GET", f"/debug?session_id={session_id}"))

    assert response.status_code == 200
    assert "interest_topic" in response.text
    assert "agent" in response.text.lower()


def test_debug_page_shows_aggregated_user_profile(tmp_path):
    deps._container = build_test_container(tmp_path)
    app = create_app()

    session_id = asyncio.run(api_request(app, "POST", "/sessions")).json()["session_id"]
    asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            data={"text": "请保存 Agent 资料，后续我还会继续研究 Agent 和 RAG"},
        )
    )
    asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            data={"text": "请保存另一段 Agent 学习资料"},
        )
    )

    response = asyncio.run(api_request(app, "GET", f"/debug?session_id={session_id}"))

    assert response.status_code == 200
    assert "User Profile" in response.text
    assert "Recent Interest Topics" in response.text
    assert "Likely Ongoing Focus" in response.text


def test_retrieve_returns_saved_material(tmp_path):
    deps._container = build_test_container(tmp_path)
    app = create_app()

    session_id = asyncio.run(api_request(app, "POST", "/sessions")).json()["session_id"]
    asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            data={"text": "请保存内网地址：10.30.1.127，网关是10.30.0.1"},
        )
    )

    response = asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            data={"text": "请告诉我内网地址"},
        )
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["action"] == "retrieve"
    assert "10.30.1.127" in payload["reply"]


def test_retrieve_short_material_returns_full_text(tmp_path):
    deps._container = build_test_container(tmp_path)
    app = create_app()

    session_id = asyncio.run(api_request(app, "POST", "/sessions")).json()["session_id"]
    asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            data={"text": "请保存：内网地址10.30.1.127 网关10.30.0.1"},
        )
    )

    response = asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            data={"text": "告诉我内网地址"},
        )
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["action"] == "retrieve"
    assert "全文" in payload["reply"]
    assert "10.30.1.127" in payload["reply"]


def test_retrieve_can_search_across_sessions(tmp_path):
    deps._container = build_test_container(tmp_path)
    app = create_app()

    session_a = asyncio.run(api_request(app, "POST", "/sessions")).json()["session_id"]
    asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_a}/ingest",
            data={"text": "请保存售前报价Agent系统的面试宝典，里面提到效率提升24倍。"},
        )
    )

    session_b = asyncio.run(api_request(app, "POST", "/sessions")).json()["session_id"]
    response = asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_b}/ingest",
            data={"text": "帮我查一下我之前存的一份面试宝典"},
        )
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["action"] == "retrieve"
    assert "面试宝典" in payload["reply"]


def test_natural_language_find_request_routes_to_retrieve(tmp_path):
    deps._container = build_test_container(tmp_path)
    app = create_app()

    session_id = asyncio.run(api_request(app, "POST", "/sessions")).json()["session_id"]
    asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            data={"text": "请保存这些 Agent 面试题，后面我还要复习"},
        )
    )

    response = asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            data={"text": "我之前保存的面试题你能帮我找一下吗"},
        )
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["action"] == "retrieve"
    assert "面试题" in payload["reply"]


def test_follow_up_question_uses_focus_item_context(tmp_path):
    deps._container = build_test_container(tmp_path)
    app = create_app()

    session_id = asyncio.run(api_request(app, "POST", "/sessions")).json()["session_id"]
    asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            data={"text": "请保存售前报价Agent系统的面试资料，里面提到核心数据是效率提升24倍。"},
        )
    )

    first = asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            data={"text": "帮我找一下售前报价Agent系统"},
        )
    )
    assert first.status_code == 200
    assert first.json()["action"] == "retrieve"

    second = asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            data={"text": "这里面写了什么"},
        )
    )

    assert second.status_code == 200
    payload = second.json()
    assert payload["action"] == "organize"
    assert "售前报价Agent系统" in payload["reply"]
    assert "24倍" in payload["reply"]


def test_working_set_can_resolve_second_result_full_text(tmp_path):
    deps._container = build_test_container(tmp_path)
    app = create_app()

    session_id = asyncio.run(api_request(app, "POST", "/sessions")).json()["session_id"]
    asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            data={"text": "请保存第一份 Agent 学习资料，主要内容是关于 ReAct。"},
        )
    )
    asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            data={"text": "请保存第二份 Agent 学习资料，主要内容是关于 Memory 设计。"},
        )
    )

    search = asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            data={"text": "帮我找一下 Agent 学习资料"},
        )
    )
    assert search.status_code == 200
    search_reply = search.json()["reply"]
    assert "2." in search_reply
    second_line = next(line for line in search_reply.splitlines() if line.startswith("2. "))
    second_title = second_line.split(" - ", 1)[0].removeprefix("2. ").strip()

    follow_up = asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            data={"text": "第二个给我全文"},
        )
    )

    assert follow_up.status_code == 200
    payload = follow_up.json()
    assert payload["action"] == "retrieve"
    assert "全文" in payload["reply"]
    assert second_title in payload["reply"]


def test_upload_doc_is_parsed_as_document_when_possible(tmp_path):
    deps._container = build_test_container(tmp_path)
    app = create_app()

    session_id = asyncio.run(api_request(app, "POST", "/sessions")).json()["session_id"]

    # .doc should be accepted; depending on the environment it may be parsed to text or saved as file upload.
    files = {"file": ("resume.doc", BytesIO(b"fake doc bytes"), "application/msword")}
    response = asyncio.run(api_request(app, "POST", f"/sessions/{session_id}/ingest", files=files))

    assert response.status_code == 200
    payload = response.json()
    assert payload["action"] == "capture"
    assert payload["item_id"]
    assert "Saved" in payload["reply"]


def test_upload_md_is_parsed_as_document(tmp_path):
    deps._container = build_test_container(tmp_path)
    app = create_app()

    session_id = asyncio.run(api_request(app, "POST", "/sessions")).json()["session_id"]
    files = {"file": ("note.md", BytesIO(b"# Title\n\nSome content about Agent and RAG."), "text/markdown")}
    response = asyncio.run(api_request(app, "POST", f"/sessions/{session_id}/ingest", files=files))

    assert response.status_code == 200
    payload = response.json()
    assert payload["action"] == "capture"

    items_response = asyncio.run(api_request(app, "GET", f"/sessions/{session_id}/items"))
    items = items_response.json()
    assert len(items) == 1
    assert items[0]["item_type"] == "document"
    assert items[0]["title"] == "note"


def test_reupload_same_document_creates_new_version_and_hides_old_from_default_listing(tmp_path):
    deps._container = build_test_container(tmp_path)
    app = create_app()

    session_id = asyncio.run(api_request(app, "POST", "/sessions")).json()["session_id"]

    first = asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            files={"file": ("resume_v1.md", BytesIO(b"# Resume\n\nfirst version"), "text/markdown")},
        )
    )
    second = asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            files={"file": ("resume_v2.md", BytesIO(b"# Resume\n\nsecond version updated"), "text/markdown")},
        )
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert "Updated" in second.json()["reply"]
    assert "v2" in second.json()["reply"]

    items_response = asyncio.run(api_request(app, "GET", f"/sessions/{session_id}/items"))
    items = items_response.json()
    assert len(items) == 1
    assert items[0]["title"] == "resume_v2"


def test_ingest_creates_topic_assignment_visible_in_debug(tmp_path):
    deps._container = build_test_container(tmp_path)
    app = create_app()

    session_id = asyncio.run(api_request(app, "POST", "/sessions")).json()["session_id"]
    response = asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            data={"text": "请保存内网地址：10.30.1.127，网关10.30.0.1，DNS 114.114.114.114"},
        )
    )

    assert response.status_code == 200
    assert "Topic:" in response.json()["reply"]

    debug = deps._container.clawbot_service.get_session_debug(session_id=session_id)
    assert debug.topics
    assert any(topic.name in {"网络配置", "AI资料", "杂项资料"} for topic in debug.topics)


def test_search_can_use_topic_first(tmp_path):
    deps._container = build_test_container(tmp_path)
    app = create_app()

    session_id = asyncio.run(api_request(app, "POST", "/sessions")).json()["session_id"]
    asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            data={"text": "请保存这份内网配置：地址10.30.1.127，网关10.30.0.1"},
        )
    )

    response = asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            data={"text": "帮我找一下网络配置"},
        )
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["action"] == "retrieve"
    assert "10.30.1.127" in payload["reply"]


def test_knowledge_overview_routes_to_overview_tool(tmp_path):
    deps._container = build_test_container(tmp_path)
    app = create_app()

    session_id = asyncio.run(api_request(app, "POST", "/sessions")).json()["session_id"]
    asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            data={"text": "请保存这份内网配置：地址10.30.1.127，网关10.30.0.1"},
        )
    )

    response = asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            data={"text": "现在知识库中有什么"},
        )
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["action"] == "retrieve"
    assert "知识库里共有" in payload["reply"]
    assert "topic" in payload["reply"].lower()


def test_list_topics_routes_to_topic_listing(tmp_path):
    deps._container = build_test_container(tmp_path)
    app = create_app()

    session_id = asyncio.run(api_request(app, "POST", "/sessions")).json()["session_id"]
    asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            data={"text": "请保存这份内网配置：地址10.30.1.127，网关10.30.0.1"},
        )
    )

    response = asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            data={"text": "有哪些主题"},
        )
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["action"] == "retrieve"
    assert "当前共有" in payload["reply"]

def test_llm_router_can_promote_ambiguous_text_to_capture():
    router = IntentRouter(
        llm_classifier=FakeLLMIntentClassifier(
            LLMIntentResult(
                intent="capture",
                confidence="medium",
                reason="Looks like reference material.",
                should_clarify=False,
            )
        )
    )

    decision = router.decide(
        text="这是一段模糊但可能需要保留的资料内容，后面大概率还会用到，不过目前没有明确动作提示。",
        has_upload=False,
    )

    assert decision.intent == "capture"
    assert decision.reason == "Looks like reference material."


def test_llm_router_can_request_clarification():
    router = IntentRouter(
        llm_classifier=FakeLLMIntentClassifier(
            LLMIntentResult(
                intent="organize",
                confidence="low",
                reason="Ambiguous material.",
                should_clarify=True,
                clarification_question="你是想保存还是总结？",
            )
        )
    )

    decision = router.decide(
        text="这一段内容我之后可能还会用到，你先看看。",
        has_upload=False,
    )

    assert decision.intent == "clarify"
    assert decision.needs_clarification is True


def test_wechat_gateway_reuses_session_for_same_user(tmp_path):
    container = build_test_container(tmp_path)
    gateway = WechatGatewayService(
        clawbot_service=container.clawbot_service,
        event_repository=ChannelEventRepository(container.database),
        session_map_repository=ChannelSessionMapRepository(container.database),
    )

    first = asyncio.run(
        gateway.handle_inbound_event(
            event=WechatInboundEvent(event_id="wx-evt-1", user_id="wx-user-a", text="请保存：这是第一条微信消息")
        )
    )
    second = asyncio.run(
        gateway.handle_inbound_event(
            event=WechatInboundEvent(event_id="wx-evt-2", user_id="wx-user-a", text="请告诉我第一条微信消息是什么")
        )
    )

    assert first.session_id == second.session_id
    assert second.action in {"retrieve", "organize", "chat"}


def test_wechat_gateway_deduplicates_same_event_id(tmp_path):
    container = build_test_container(tmp_path)
    gateway = WechatGatewayService(
        clawbot_service=container.clawbot_service,
        event_repository=ChannelEventRepository(container.database),
        session_map_repository=ChannelSessionMapRepository(container.database),
    )

    first = asyncio.run(
        gateway.handle_inbound_event(
            event=WechatInboundEvent(event_id="wx-dup-1", user_id="wx-user-b", text="请保存：重复事件测试")
        )
    )
    second = asyncio.run(
        gateway.handle_inbound_event(
            event=WechatInboundEvent(event_id="wx-dup-1", user_id="wx-user-b", text="请保存：重复事件测试")
        )
    )

    assert first.deduplicated is False
    assert second.deduplicated is True


def test_wechat_gateway_ingests_file_event(tmp_path):
    container = build_test_container(tmp_path)
    gateway = WechatGatewayService(
        clawbot_service=container.clawbot_service,
        event_repository=ChannelEventRepository(container.database),
        session_map_repository=ChannelSessionMapRepository(container.database),
    )
    local_file = tmp_path / "wechat_note.md"
    local_file.write_text("# 微信资料\n\n这是从微信发来的文档内容。", encoding="utf-8")

    result = asyncio.run(
        gateway.handle_inbound_event(
            event=WechatInboundEvent(
                event_id="wx-file-1",
                user_id="wx-user-file",
                text="请保存这个文件",
                file_name="wechat_note.md",
                file_path=str(local_file),
                file_mime="text/markdown",
            )
        )
    )

    assert result.deduplicated is False
    assert result.action == "capture"
