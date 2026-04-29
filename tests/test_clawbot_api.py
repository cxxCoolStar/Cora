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
from core.clawbot import dependencies as deps  # noqa: E402
from core.clawbot.dependencies import ClawBotContainer  # noqa: E402
from core.agent.config import CoreSettings  # noqa: E402
from core.clawbot.intent_llm import LLMIntentResult  # noqa: E402
from core.clawbot.intent_router import IntentRouter  # noqa: E402
from core.clawbot.service import ClawBotService  # noqa: E402
from core.ingestion.service import IngestionService  # noqa: E402
from core.storage.db import DatabaseManager  # noqa: E402
from core.storage.repositories import ClarificationRepository, ItemChunkRepository, ItemRepository, MessageRepository, SessionRepository  # noqa: E402


class FakeLLMIntentClassifier:
    def __init__(self, result: LLMIntentResult | None) -> None:
        self.result = result

    def classify(self, *, text: str):
        return self.result


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
    ingestion_service = IngestionService(
        item_repository=item_repository,
        item_chunk_repository=item_chunk_repository,
        message_repository=message_repository,
        storage_dir=settings.files_storage_dir,
    )
    clawbot_service = ClawBotService(
        session_repository=session_repository,
        message_repository=message_repository,
        item_repository=item_repository,
        item_chunk_repository=item_chunk_repository,
        ingestion_service=ingestion_service,
        clarification_repository=clarification_repository,
    )
    container = ClawBotContainer(
        settings=settings,
        database=database,
        session_repository=session_repository,
        message_repository=message_repository,
        item_repository=item_repository,
        item_chunk_repository=item_chunk_repository,
        clarification_repository=clarification_repository,
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
    assert "我可以帮你保存文本" in payload["reply"]

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
    long_text = "这是一个很长的资料内容，需要你之后帮我查找和整理。\n" * 4
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
