from __future__ import annotations

import asyncio
import json
from io import BytesIO
import sys
from pathlib import Path

import httpx
from fastapi import UploadFile

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.api.app import create_app  # noqa: E402
from core.agent.context_budget import ContextBudgetManager  # noqa: E402
from core.agent.runtime_state import RuntimeContextSnapshot  # noqa: E402
from core.cli.main import _build_wechat_runtime  # noqa: E402
from core.channels.wechat.poller import WechatPoller  # noqa: E402
from core.channels.wechat.service import WechatGatewayService  # noqa: E402
from core.channels.wechat.types import WechatHandleResult, WechatInboundEvent  # noqa: E402
from core.clawbot import dependencies as deps  # noqa: E402
from core.clawbot.dependencies import ClawBotContainer  # noqa: E402
from core.config import CoreSettings  # noqa: E402
from core.clawbot.intent_llm import LLMIntentClassifier  # noqa: E402
from core.clawbot.intent_llm import LLMIntentResult  # noqa: E402
from core.clawbot.intent_router import IntentRouter  # noqa: E402
from core.clawbot.planner import ToolPlan  # noqa: E402
from core.clawbot.service import ClawBotService  # noqa: E402
from core.clawbot import RuntimeToolExecutor  # noqa: E402
from core.ingestion.parsers.image_parser import ImageFileParser  # noqa: E402
from core.ingestion.service import IngestionService  # noqa: E402
from core.storage.db import DatabaseManager  # noqa: E402
from core.storage.repositories import ChannelEventRepository, ChannelSessionMapRepository, ItemRepository, MessageRepository, PendingStateRepository, SessionRepository, SessionSummaryRepository, SourceEventRepository, TopicActivityRepository, TopicItemRepository, TopicRepository, UserSignalRepository  # noqa: E402
from core.topics.classifier import TopicClassifier  # noqa: E402
from core.topics.service import TopicOrganizerService  # noqa: E402
from core.llm.base import ModelClient  # noqa: E402
from core.schemas.message import Message  # noqa: E402
from core.schemas.model import ModelResponse  # noqa: E402
from core.schemas.tool import ToolCall, ToolSpec  # noqa: E402
from core.tools.registry import ToolInvocation  # noqa: E402


def archive_skill_call(intent: str, **arguments) -> ToolCall:
    return ToolCall(
        tool_name="skill_run",
        arguments={
            "name": "archive-core",
            "script_path": "scripts/archive_dispatch.py",
            "input": {"intent": intent, **arguments},
        },
    )


class FakeLLMIntentClassifier:
    def __init__(self, result: LLMIntentResult | None) -> None:
        self.result = result

    def classify(self, *, text: str):
        return self.result


class StubTopicModelClient(ModelClient):
    @staticmethod
    def _pending_state(messages: list[Message]) -> dict:
        if not messages:
            return {}
        system_message = messages[0]
        if system_message.role != "system":
            return {}
        marker = "Conversation state:\n"
        if marker not in system_message.content:
            return {}
        state_text = system_message.content.split(marker, 1)[1].strip()
        try:
            payload = json.loads(state_text)
        except json.JSONDecodeError:
            return {}
        pending = payload.get("pending_state")
        return pending if isinstance(pending, dict) else {}

    @staticmethod
    def _tool_reply_text(content: str) -> str:
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            return content
        if isinstance(payload, dict):
            reply = payload.get("reply")
            if isinstance(reply, str) and reply.strip():
                return reply
            tool_content = payload.get("content")
            if isinstance(tool_content, str) and tool_content.strip():
                return tool_content
        return content

    def generate(self, *, messages: list[Message], tools: list[ToolSpec]) -> ModelResponse:
        session_id = messages[0].session_id if messages else ""
        if session_id == "session-summary-writer":
            return ModelResponse(
                assistant_text=json.dumps(
                    {
                        "active_task": "none",
                        "user_facts": ["User is archiving conversation artifacts."],
                        "open_loops": [],
                        "resolved_requests": ["Historical turns were compacted into a structured summary."],
                        "recent_decisions": ["Use the archive-core skill workflow as the main tool surface."],
                        "critical_context": ["Preserve item references and pending state when relevant."],
                    },
                    ensure_ascii=False,
                )
            )
        latest_user = next((message for message in reversed(messages) if message.role == "user"), None)
        latest_tool = messages[-1] if messages and messages[-1].role == "tool" else None
        user_text = latest_user.content if latest_user else ""

        if tools:
            if latest_tool is not None:
                return ModelResponse(assistant_text=self._tool_reply_text(latest_tool.content))

            lowered = user_text.lower()
            pending = self._pending_state(messages)
            pending_type = str(pending.get("type") or "").strip()
            if pending_type == "upload_save":
                if any(token in user_text for token in ["取消", "不用", "算了"]):
                    return ModelResponse(tool_calls=[archive_skill_call("resolve_pending", resolution="cancel")])
                note = "" if any(token in user_text for token in ["保存", "记一下", "记住"]) and len(user_text.strip()) <= 8 else user_text
                arguments = {"resolution": "save"}
                if note.strip():
                    arguments["note"] = note
                return ModelResponse(tool_calls=[archive_skill_call("resolve_pending", **arguments)])
            if pending_type == "save_decision":
                if "总结" in user_text or "整理" in user_text:
                    return ModelResponse(tool_calls=[archive_skill_call("resolve_pending", resolution="summarize")])
                if any(token in user_text for token in ["取消", "不用", "算了"]):
                    return ModelResponse(tool_calls=[archive_skill_call("resolve_pending", resolution="cancel")])
                return ModelResponse(tool_calls=[archive_skill_call("resolve_pending", resolution="save")])
            if pending_type == "item_selection":
                if "第二个" in user_text:
                    return ModelResponse(tool_calls=[archive_skill_call("resolve_pending", resolution="select", target={"type": "working_set_rank", "value": 2}, mode="full_text")])
                return ModelResponse(tool_calls=[archive_skill_call("resolve_pending", resolution="select", target={"type": "working_set_rank", "value": 1}, mode="full_text")])
            if user_text == "[file upload: note.txt]" or user_text.startswith("[file upload:"):
                return ModelResponse(tool_calls=[archive_skill_call("clarify")])
            if "知识库" in user_text and ("有什么" in user_text or "概览" in user_text):
                return ModelResponse(tool_calls=[archive_skill_call("overview")])
            if "主题" in user_text or "topic" in lowered:
                return ModelResponse(tool_calls=[archive_skill_call("list_topics")])
            if "删除" in user_text or "删掉" in user_text or "移除" in user_text:
                if "第" in user_text:
                    return ModelResponse(tool_calls=[archive_skill_call("clarify", question="请告诉我你想删哪一条，或者给我更具体一点的描述。")])
                return ModelResponse(tool_calls=[archive_skill_call("delete", query=user_text)])
            if "第二个" in user_text and ("全文" in user_text or "给我" in user_text):
                return ModelResponse(tool_calls=[archive_skill_call("clarify", question="请告诉我你想看哪一条，或者给我更具体一点的描述。")])
            if "第一个" in user_text and "面试宝典" in user_text:
                return ModelResponse(tool_calls=[archive_skill_call("clarify", question="请告诉我你想看哪一条，或者给我更具体一点的描述。")])
            if "这里面写了什么" in user_text or "展开讲讲" in user_text:
                return ModelResponse(tool_calls=[archive_skill_call("clarify", question="请告诉我你想看哪一条，或者给我更具体一点的描述。")])
            if user_text.startswith("http://") or user_text.startswith("https://"):
                return ModelResponse(tool_calls=[archive_skill_call("save", text=user_text)])
            if "帮我找" in user_text or "帮我查" in user_text or "查一下" in user_text or "告诉我" in user_text:
                return ModelResponse(tool_calls=[archive_skill_call("search", query=user_text)])
            if "总结" in user_text:
                return ModelResponse(tool_calls=[archive_skill_call("resolve_pending", resolution="summarize")])
            if user_text in {"你好", "您好", "hi", "hello"}:
                return ModelResponse(assistant_text="你好，我是Cora,可以帮你保存文本、链接和文件，也可以帮你查找之前发过的资料。")
            if len(user_text) >= 120 or ("\n" in user_text and len(user_text) >= 40):
                return ModelResponse(tool_calls=[archive_skill_call("clarify", question="这段内容你是想让我先保存，还是先帮你总结一下？")])
            return ModelResponse(tool_calls=[archive_skill_call("save", text=user_text)])

        if "topic-query-router" in session_id or "existing_topics" in user_text and "query" in user_text:
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


class StubSendFileFailureModelClient(ModelClient):
    def generate(self, *, messages: list[Message], tools: list[ToolSpec]) -> ModelResponse:
        latest_tool = messages[-1] if messages and messages[-1].role == "tool" else None
        latest_user = next((message for message in reversed(messages) if message.role == "user"), None)
        if latest_tool is not None:
            return ModelResponse(assistant_text="已经发送，请查收。")
        return ModelResponse(
            tool_calls=[
                archive_skill_call("deliver", query=latest_user.content if latest_user else "")
            ]
        )


class StubToollessDeliveryRetryModelClient(ModelClient):
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, *, messages: list[Message], tools: list[ToolSpec]) -> ModelResponse:
        self.calls += 1
        latest_tool = messages[-1] if messages and messages[-1].role == "tool" else None
        latest_user = next((message for message in reversed(messages) if message.role == "user"), None)
        if latest_tool is not None:
            return ModelResponse(assistant_text="已经发送，请查收。")
        correction_present = any(
            message.role == "system" and "Tool-use correction:" in message.content
            for message in messages
        )
        if correction_present:
            return ModelResponse(
                tool_calls=[
                    archive_skill_call("deliver", query=latest_user.content if latest_user else "")
                ]
            )
        return ModelResponse(assistant_text="抱歉，我无法直接转发这张照片给你。")


class FakeVisionDescriber:
    def describe_image(self, *, image_path: Path, mime_type: str) -> str:
        return (
            f"scene: synthetic test image\n"
            f"mime: {mime_type}\n"
            f"filename: {image_path.name}\n"
            "keywords: test, diagram, screenshot"
        )


def build_test_container(
    tmp_path: Path,
    *,
    enable_image_vision: bool = False,
    context_length: int = 128000,
    context_compression_threshold: float = 0.50,
    context_summary_target_ratio: float = 0.20,
    context_protect_last_n_min: int = 8,
) -> ClawBotContainer:
    settings = CoreSettings(
        clawbot_database_path=tmp_path / "clawbot.db",
        files_storage_dir=tmp_path / "files",
        archive_root_dir=tmp_path / "archive",
        context_length=context_length,
        context_compression_threshold=context_compression_threshold,
        context_summary_target_ratio=context_summary_target_ratio,
        context_protect_last_n_min=context_protect_last_n_min,
    )
    database = DatabaseManager(settings.clawbot_database_url)
    session_repository = SessionRepository(database)
    session_summary_repository = SessionSummaryRepository(database)
    message_repository = MessageRepository(database)
    source_event_repository = SourceEventRepository(database)
    item_repository = ItemRepository(database)
    pending_state_repository = PendingStateRepository(database)
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
    image_parser = ImageFileParser(describer=FakeVisionDescriber() if enable_image_vision else None)
    ingestion_service = IngestionService(
        item_repository=item_repository,
        message_repository=message_repository,
        user_signal_repository=user_signal_repository,
        storage_dir=settings.files_storage_dir,
        image_parser=image_parser,
        topic_organizer=topic_organizer,
    )
    tool_executor = RuntimeToolExecutor(
        ingestion_service=ingestion_service,
        item_repository=item_repository,
        pending_state_repository=pending_state_repository,
    )
    model_client = StubTopicModelClient()
    context_budget_manager = ContextBudgetManager(
        context_length=settings.context_length,
        compression_threshold=settings.context_compression_threshold,
        summary_target_ratio=settings.context_summary_target_ratio,
        protect_last_n_min=settings.context_protect_last_n_min,
    )
    clawbot_service = ClawBotService(
        session_repository=session_repository,
        session_summary_repository=session_summary_repository,
        message_repository=message_repository,
        source_event_repository=source_event_repository,
        item_repository=item_repository,
        ingestion_service=ingestion_service,
        pending_state_repository=pending_state_repository,
        user_signal_repository=user_signal_repository,
        topic_repository=topic_repository,
        model_client=model_client,
        tool_executor=tool_executor,
        topic_organizer=topic_organizer,
        context_budget_manager=context_budget_manager,
    )
    container = ClawBotContainer(
        settings=settings,
        database=database,
        session_repository=session_repository,
        session_summary_repository=session_summary_repository,
        message_repository=message_repository,
        source_event_repository=source_event_repository,
        item_repository=item_repository,
        pending_state_repository=pending_state_repository,
        user_signal_repository=user_signal_repository,
        topic_repository=topic_repository,
        ingestion_service=ingestion_service,
        clawbot_service=clawbot_service,
        tool_executor=tool_executor,
    )
    container.initialize()
    return container


def test_tool_loop_prompt_includes_archive_core_skill(tmp_path):
    container = build_test_container(tmp_path)
    session = container.session_repository.create()

    messages = container.clawbot_service.build_agent_messages(
        session_id=session.id,
        user_text="帮我保存这张照片",
        context_snapshot=RuntimeContextSnapshot(),
        tool_messages=[],
    )

    assert messages[0].role == "system"
    assert "archive-core" in messages[0].content
    assert "Shared skills summary:" in messages[0].content


def test_agent_history_uses_recent_12_messages_plus_summary(tmp_path):
    container = build_test_container(
        tmp_path,
        context_length=4096,
        context_compression_threshold=0.25,
        context_summary_target_ratio=0.08,
        context_protect_last_n_min=4,
    )
    session = container.session_repository.create()
    long_text = "历史消息" * 220

    for index in range(10):
        container.message_repository.add_user_message(
            session_id=session.id,
            content=f"user message {index} {long_text}",
        )
        container.message_repository.add_assistant_message(
            session_id=session.id,
            content=f"assistant message {index} {long_text}",
            metadata={"action": "chat", "tool": "archive"},
        )

    history = container.clawbot_service._load_agent_history(
        session_id=session.id,
        user_text="fresh user input",
    )

    assert history[0].role == "system"
    assert "[SESSION SUMMARY — REFERENCE ONLY]" in history[0].content
    assert "User Facts:" in history[0].content
    assert "Recent Decisions:" in history[0].content
    assert 2 <= len(history) < 13
    assert history[-1].role == "assistant"
    assert history[-1].content.startswith("assistant message 9")
    summary_record = container.session_summary_repository.get_by_session(session_id=session.id)
    assert summary_record is not None
    payload = summary_record.summary_json
    assert 0 < payload["covered_message_count"] < 20
    assert payload["summary"]["active_task"] == "none"


def test_runtime_context_snapshot_keeps_operational_state_separate_from_history(tmp_path):
    container = build_test_container(tmp_path)
    session = container.session_repository.create()
    source_event = container.source_event_repository.create(
        session_id=session.id,
        source_message_id=None,
        channel="wechat",
        event_type="text",
        raw_text="帮我找简历",
        metadata={},
    )
    container.message_repository.add_assistant_message(
        session_id=session.id,
        content="我来帮你找。",
        metadata={
            "context": {
                "current_source_event_id": source_event.id,
                "last_action": "retrieve",
                "recent_events": [],
            }
        },
    )

    snapshot = container.clawbot_service.load_context_snapshot(session_id=session.id)

    assert snapshot.current_source_event_id == source_event.id
    assert snapshot.last_action == "retrieve"
    assert snapshot.recent_events


def test_session_summary_is_not_refreshed_for_tiny_delta(tmp_path):
    container = build_test_container(
        tmp_path,
        context_length=4096,
        context_compression_threshold=0.25,
        context_summary_target_ratio=0.08,
        context_protect_last_n_min=4,
    )
    session = container.session_repository.create()
    long_text = "历史消息" * 220

    for index in range(10):
        container.message_repository.add_user_message(
            session_id=session.id,
            content=f"user message {index} {long_text}",
        )
        container.message_repository.add_assistant_message(
            session_id=session.id,
            content=f"assistant message {index} {long_text}",
            metadata={"action": "chat", "tool": "archive"},
        )

    container.clawbot_service._load_agent_history(
        session_id=session.id,
        user_text="fresh user input",
    )
    first_summary = container.session_summary_repository.get_by_session(session_id=session.id)
    assert first_summary is not None
    first_payload = dict(first_summary.summary_json or {})

    for index in range(2):
        container.message_repository.add_user_message(
            session_id=session.id,
            content=f"small delta user {index}",
        )
        container.message_repository.add_assistant_message(
            session_id=session.id,
            content=f"small delta assistant {index}",
            metadata={"action": "chat", "tool": "archive"},
        )

    container.clawbot_service._load_agent_history(
        session_id=session.id,
        user_text="follow-up input",
    )
    second_summary = container.session_summary_repository.get_by_session(session_id=session.id)
    assert second_summary is not None
    second_payload = dict(second_summary.summary_json or {})

    assert second_payload["summary"] == first_payload["summary"]
    assert second_payload["covered_message_count"] > first_payload["covered_message_count"]


def test_agent_history_uses_token_budget_not_fixed_message_count(tmp_path):
    container = build_test_container(
        tmp_path,
        context_length=4096,
        context_compression_threshold=0.25,
        context_summary_target_ratio=0.08,
        context_protect_last_n_min=2,
    )
    session = container.session_repository.create()
    long_text = "长内容" * 220

    for index in range(12):
        container.message_repository.add_user_message(
            session_id=session.id,
            content=f"user {index} {long_text}",
        )
        container.message_repository.add_assistant_message(
            session_id=session.id,
            content=f"assistant {index} {long_text}",
            metadata={"action": "chat", "tool": "archive"},
        )

    history = container.clawbot_service._load_agent_history(
        session_id=session.id,
        user_text="new request",
    )

    assert history[0].role == "system"
    assert "[SESSION SUMMARY — REFERENCE ONLY]" in history[0].content
    # Token-budget mode should keep fewer than the default 12 raw messages here.
    assert len(history) < 13


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
    first = asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            files={"file": ("note.txt", BytesIO(b"hello from a saved txt file"), "text/plain")},
        )
    )
    assert first.status_code == 200
    assert first.json()["action"] == "clarify"
    response = asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            data={"text": "保存"},
        )
    )

    assert response.status_code == 200
    item_id = response.json()["item_id"]
    detail = asyncio.run(api_request(app, "GET", f"/sessions/{session_id}/items/{item_id}"))
    assert detail.status_code == 200
    assert detail.json()["item_type"] == "document"


def test_code_file_ingest_flow_uses_plain_text_parsing(tmp_path):
    deps._container = build_test_container(tmp_path)
    app = create_app()

    session_id = asyncio.run(api_request(app, "POST", "/sessions")).json()["session_id"]
    first = asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            files={"file": ("main.py", BytesIO("# Agent helper\nprint('hello')\n".encode("utf-8")), "text/x-python")},
        )
    )
    assert first.status_code == 200
    assert first.json()["action"] == "clarify"

    response = asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            data={"text": "保存"},
        )
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["action"] == "capture"
    item = deps._container.item_repository.get_any(item_id=payload["item_id"])
    assert item.item_type == "document"
    assert item.title == "main"
    assert "print('hello')" in item.normalized_text
    assert item.metadata_json.get("file_suffix") == ".py"


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


def test_bare_file_upload_triggers_clarification(tmp_path):
    deps._container = build_test_container(tmp_path)
    app = create_app()

    session_id = asyncio.run(api_request(app, "POST", "/sessions")).json()["session_id"]
    response = asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            files={"file": ("resume.txt", BytesIO(b"resume content"), "text/plain")},
        )
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["action"] == "clarify"
    assert payload["needs_clarification"] is True
    assert "文件" in payload["reply"]


def test_bare_file_upload_reply_can_save_with_note(tmp_path):
    deps._container = build_test_container(tmp_path)
    app = create_app()

    session_id = asyncio.run(api_request(app, "POST", "/sessions")).json()["session_id"]
    first = asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            files={"file": ("resume.txt", BytesIO(b"resume content"), "text/plain")},
        )
    )
    assert first.json()["action"] == "clarify"

    second = asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            data={"text": "这是我的简历"},
        )
    )

    assert second.status_code == 200
    payload = second.json()
    assert payload["action"] == "capture"
    item_id = payload["item_id"]
    assert item_id
    detail = asyncio.run(api_request(app, "GET", f"/sessions/{session_id}/items/{item_id}"))
    assert detail.status_code == 200
    assert "这是我的简历" in detail.json()["normalized_text"]


def test_bare_image_uploads_are_buffered_into_one_pending_batch(tmp_path):
    deps._container = build_test_container(tmp_path)
    app = create_app()

    session_id = asyncio.run(api_request(app, "POST", "/sessions")).json()["session_id"]
    first = asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            files={"file": ("wechat_image.jpg", BytesIO(b"fake image bytes 1"), "image/jpeg")},
        )
    )
    assert first.status_code == 200
    assert first.json()["action"] == "clarify"

    second = asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            files={"file": ("wechat_image.jpg", BytesIO(b"fake image bytes 2"), "image/jpeg")},
        )
    )
    assert second.status_code == 200
    assert second.json()["action"] == "buffered"
    assert second.json()["reply"] == ""

    saved = asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            data={"text": "这些照片都保存起来"},
        )
    )
    assert saved.status_code == 200
    assert saved.json()["action"] == "capture"

    items = asyncio.run(api_request(app, "GET", f"/sessions/{session_id}/items")).json()
    image_items = [item for item in items if item["item_type"] == "image"]
    assert len(image_items) == 2


def test_ingest_records_user_signals(tmp_path):
    deps._container = build_test_container(tmp_path)
    session = deps._container.session_repository.create()
    source_message = deps._container.message_repository.add_user_message(
        session_id=session.id,
        content="请保存这段 Agent 和 RAG 面试题资料",
    )

    saved = asyncio.run(
        deps._container.ingestion_service.ingest(
            session_id=session.id,
            source_message_id=source_message.id,
            source_event_id=None,
            text="请保存这段 Agent 和 RAG 面试题资料",
            upload=None,
        )
    )

    signals = deps._container.user_signal_repository.list_by_session(session_id=session.id)

    assert saved.item_id
    assert any(signal.signal_type == "interest_topic" and signal.signal_value == "agent" for signal in signals)
    assert any(signal.signal_type == "interest_topic" and signal.signal_value == "rag" for signal in signals)


def test_debug_page_shows_aggregated_user_profile(tmp_path):
    deps._container = build_test_container(tmp_path)
    session = deps._container.session_repository.create()

    first_message = deps._container.message_repository.add_user_message(
        session_id=session.id,
        content="请保存 Agent 资料，后续我还会继续研究 Agent 和 RAG",
    )
    asyncio.run(
        deps._container.ingestion_service.ingest(
            session_id=session.id,
            source_message_id=first_message.id,
            source_event_id=None,
            text="请保存 Agent 资料，后续我还会继续研究 Agent 和 RAG",
            upload=None,
        )
    )

    second_message = deps._container.message_repository.add_user_message(
        session_id=session.id,
        content="请保存另一段 Agent 学习资料",
    )
    asyncio.run(
        deps._container.ingestion_service.ingest(
            session_id=session.id,
            source_message_id=second_message.id,
            source_event_id=None,
            text="请保存另一段 Agent 学习资料",
            upload=None,
        )
    )

    signals = deps._container.user_signal_repository.list_by_session(session_id=session.id)
    sections = deps._container.clawbot_service.user_profile_aggregator.build(signals=signals)
    section_names = {section.name for section in sections}

    assert "Recent Interest Topics" in section_names
    assert "Likely Ongoing Focus" in section_names


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


def test_follow_up_question_without_explicit_target_requests_clarification(tmp_path):
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
    assert payload["action"] == "clarify"
    assert "请告诉我" in payload["reply"] or "更具体" in payload["reply"]


def test_rank_based_follow_up_now_requests_clarification(tmp_path):
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
    assert payload["action"] == "clarify"
    assert second_title not in payload["reply"]


def test_upload_doc_is_parsed_as_document_when_possible(tmp_path):
    deps._container = build_test_container(tmp_path)
    app = create_app()

    session_id = asyncio.run(api_request(app, "POST", "/sessions")).json()["session_id"]

    # .doc should be accepted; depending on the environment it may be parsed to text or saved as file upload.
    files = {"file": ("resume.doc", BytesIO(b"fake doc bytes"), "application/msword")}
    first = asyncio.run(api_request(app, "POST", f"/sessions/{session_id}/ingest", files=files))
    assert first.status_code == 200
    assert first.json()["action"] == "clarify"
    response = asyncio.run(api_request(app, "POST", f"/sessions/{session_id}/ingest", data={"text": "保存"}))

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
    first = asyncio.run(api_request(app, "POST", f"/sessions/{session_id}/ingest", files=files))
    assert first.status_code == 200
    assert first.json()["action"] == "clarify"
    response = asyncio.run(api_request(app, "POST", f"/sessions/{session_id}/ingest", data={"text": "保存"}))

    assert response.status_code == 200
    payload = response.json()
    assert payload["action"] == "capture"

    items_response = asyncio.run(api_request(app, "GET", f"/sessions/{session_id}/items"))
    items = items_response.json()
    assert len(items) == 1
    assert items[0]["item_type"] == "document"
    assert items[0]["title"] == "note"


def test_unknown_text_file_uses_plain_text_fallback(tmp_path):
    deps._container = build_test_container(tmp_path)
    app = create_app()

    session_id = asyncio.run(api_request(app, "POST", "/sessions")).json()["session_id"]
    files = {"file": ("notes.custom", BytesIO(b"first line\nsecond line"), "application/octet-stream")}
    first = asyncio.run(api_request(app, "POST", f"/sessions/{session_id}/ingest", files=files))
    assert first.status_code == 200
    assert first.json()["action"] == "clarify"

    response = asyncio.run(api_request(app, "POST", f"/sessions/{session_id}/ingest", data={"text": "保存"}))

    assert response.status_code == 200
    payload = response.json()
    assert payload["action"] == "capture"
    item = deps._container.item_repository.get_any(item_id=payload["item_id"])
    assert item.item_type == "document"
    assert item.title == "notes"
    assert "second line" in item.normalized_text


def test_unknown_binary_file_stays_unsupported(tmp_path):
    deps._container = build_test_container(tmp_path)
    app = create_app()

    session_id = asyncio.run(api_request(app, "POST", "/sessions")).json()["session_id"]
    files = {"file": ("archive.weird", BytesIO(b"\x00\x01\x02\x03PK\x03\x04"), "application/octet-stream")}
    first = asyncio.run(api_request(app, "POST", f"/sessions/{session_id}/ingest", files=files))
    assert first.status_code == 200
    assert first.json()["action"] == "clarify"

    response = asyncio.run(api_request(app, "POST", f"/sessions/{session_id}/ingest", data={"text": "保存"}))

    assert response.status_code == 200
    payload = response.json()
    assert payload["action"] == "capture"
    item = deps._container.item_repository.get_any(item_id=payload["item_id"])
    assert item.item_type == "file_upload"
    assert item.metadata_json.get("parse_status") == "unsupported"
    assert item.metadata_json.get("file_suffix") == ".weird"


def test_upload_pdf_is_routed_through_docling_not_marked_unsupported(tmp_path):
    deps._container = build_test_container(tmp_path)
    app = create_app()

    session_id = asyncio.run(api_request(app, "POST", "/sessions")).json()["session_id"]
    files = {"file": ("note.pdf", BytesIO(b"%PDF-1.4\nfake pdf bytes"), "application/pdf")}
    first = asyncio.run(api_request(app, "POST", f"/sessions/{session_id}/ingest", files=files))
    assert first.status_code == 200
    assert first.json()["action"] == "clarify"
    response = asyncio.run(api_request(app, "POST", f"/sessions/{session_id}/ingest", data={"text": "保存"}))

    assert response.status_code == 200
    payload = response.json()
    assert payload["action"] == "capture"
    assert payload["item_id"]

    item = deps._container.item_repository.get_any(item_id=payload["item_id"])
    if item.item_type == "file_upload":
        assert item.title == "note.pdf"
        assert item.metadata_json.get("parse_status") == "failed"
        assert item.metadata_json.get("file_suffix") == ".pdf"
    else:
        assert item.item_type == "document"
        assert item.title == "note"


def test_upload_png_is_parsed_as_image_with_aux_vision(tmp_path):
    deps._container = build_test_container(tmp_path, enable_image_vision=True)
    app = create_app()

    session_id = asyncio.run(api_request(app, "POST", "/sessions")).json()["session_id"]
    files = {"file": ("diagram.png", BytesIO(b"\x89PNG\r\n\x1a\nfakepngbytes"), "image/png")}
    first = asyncio.run(api_request(app, "POST", f"/sessions/{session_id}/ingest", files=files))
    assert first.status_code == 200
    assert first.json()["action"] == "clarify"
    response = asyncio.run(api_request(app, "POST", f"/sessions/{session_id}/ingest", data={"text": "保存"}))

    assert response.status_code == 200
    payload = response.json()
    assert payload["action"] == "capture"
    assert payload["item_id"]

    item = deps._container.item_repository.get_any(item_id=payload["item_id"])
    assert item.item_type == "image"
    assert "Visual description:" in item.normalized_text
    assert "keywords: test, diagram, screenshot" in item.normalized_text


def test_upload_png_without_aux_vision_still_saves_image_to_files_storage(tmp_path):
    deps._container = build_test_container(tmp_path)
    app = create_app()

    session_id = asyncio.run(api_request(app, "POST", "/sessions")).json()["session_id"]
    files = {"file": ("diagram.png", BytesIO(b"\x89PNG\r\n\x1a\nfakepngbytes"), "image/png")}
    first = asyncio.run(api_request(app, "POST", f"/sessions/{session_id}/ingest", files=files))
    assert first.status_code == 200
    assert first.json()["action"] == "clarify"
    response = asyncio.run(api_request(app, "POST", f"/sessions/{session_id}/ingest", data={"text": "保存"}))

    assert response.status_code == 200
    payload = response.json()
    assert payload["action"] == "capture"
    assert payload["item_id"]

    item = deps._container.item_repository.get_any(item_id=payload["item_id"])
    assert item.item_type == "image"
    assert item.metadata_json.get("stored_file_path")
    assert not item.metadata_json.get("archive_record_id")
    assert "image analysis was unavailable" in item.raw_content.lower()


def test_reupload_same_document_creates_new_version_and_hides_old_from_default_listing(tmp_path):
    deps._container = build_test_container(tmp_path)
    app = create_app()

    session_id = asyncio.run(api_request(app, "POST", "/sessions")).json()["session_id"]

    first_prompt = asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            files={"file": ("resume_v1.md", BytesIO(b"# Resume\n\nfirst version"), "text/markdown")},
        )
    )
    assert first_prompt.status_code == 200
    assert first_prompt.json()["action"] == "clarify"
    first = asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            data={"text": "保存"},
        )
    )
    second_prompt = asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            files={"file": ("resume_v2.md", BytesIO(b"# Resume\n\nsecond version updated"), "text/markdown")},
        )
    )
    assert second_prompt.status_code == 200
    assert second_prompt.json()["action"] == "clarify"
    second = asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            data={"text": "保存"},
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


def test_delete_item_api_hides_deleted_item_from_listing_and_detail(tmp_path):
    deps._container = build_test_container(tmp_path)
    app = create_app()

    session_id = asyncio.run(api_request(app, "POST", "/sessions")).json()["session_id"]
    source_message = deps._container.message_repository.add_user_message(
        session_id=session_id,
        content="测试删除资料",
    )
    saved = asyncio.run(
        deps._container.ingestion_service.ingest(
            session_id=session_id,
            source_message_id=source_message.id,
            source_event_id=None,
            text="测试删除资料",
            upload=None,
        )
    )
    item_id = saved.item_id
    assert item_id

    deleted = asyncio.run(api_request(app, "DELETE", f"/sessions/{session_id}/items/{item_id}"))
    assert deleted.status_code == 200
    assert deleted.json()["action"] == "delete"
    assert deleted.json()["item_id"] == item_id

    items = asyncio.run(api_request(app, "GET", f"/sessions/{session_id}/items")).json()
    assert items == []

    detail = asyncio.run(api_request(app, "GET", f"/sessions/{session_id}/items/{item_id}"))
    assert detail.status_code == 404


def test_rank_based_delete_now_requests_clarification(tmp_path):
    deps._container = build_test_container(tmp_path)
    app = create_app()

    session_id = asyncio.run(api_request(app, "POST", "/sessions")).json()["session_id"]
    asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            data={"text": "请保存内网配置 A：地址10.30.1.127，网关10.30.0.1"},
        )
    )
    asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            data={"text": "请保存内网配置 B：地址10.30.1.128，网关10.30.0.1"},
        )
    )

    opened = asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            data={"text": "帮我找一下内网配置"},
        )
    )
    assert opened.status_code == 200
    assert opened.json()["action"] == "retrieve"

    deleted = asyncio.run(
        api_request(
            app,
            "POST",
            f"/sessions/{session_id}/ingest",
            data={"text": "删除第二个"},
        )
    )
    assert deleted.status_code == 200
    assert deleted.json()["action"] == "clarify"
    assert "更具体" in deleted.json()["reply"] or "文件名" in deleted.json()["reply"]

    items = asyncio.run(api_request(app, "GET", f"/sessions/{session_id}/items")).json()
    assert len(items) == 2


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


def test_wechat_gateway_overrides_generic_reply_when_media_download_failed(tmp_path):
    class _FallbackClawBotService:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def ingest(self, *, session_id: str, text: str | None, upload: UploadFile | None, source_metadata: dict[str, object] | None = None):
            self.calls.append(
                {
                    "session_id": session_id,
                    "text": text,
                    "upload": upload,
                    "source_metadata": source_metadata or {},
                }
            )
            from core.clawbot.schemas import TurnResponse

            return TurnResponse(
                reply="I do not have a final answer yet.",
                status="completed",
                disposition="respond",
                action="chat",
                item_id=None,
                needs_clarification=False,
                artifacts=[],
                trace=[],
                decision_source="llm_tool_call",
            )

        def create_session(self):
            class _Session:
                id = "session-wechat-media-fail"

            return _Session()

    database = DatabaseManager(f"sqlite:///{(tmp_path / 'clawbot.db').as_posix()}")
    database.create_all()
    gateway = WechatGatewayService(
        clawbot_service=_FallbackClawBotService(),
        event_repository=ChannelEventRepository(database),
        session_map_repository=ChannelSessionMapRepository(database),
    )

    result = asyncio.run(
        gateway.handle_inbound_event(
            event=WechatInboundEvent(
                event_id="wx-media-fail-1",
                user_id="wx-user-media-fail",
                text="这是我跟对象一起去玩的《污秽》的密室逃脱",
                media_download_failed=True,
                media_download_error="httpx.ReadTimeout('timed out')",
            )
        )
    )

    assert "图片下载失败" in result.reply
    assert "还没有成功保存图片" in result.reply


def test_wechat_poller_merges_nearby_text_and_media_failure_events():
    class _FakeClient:
        def __init__(self) -> None:
            self.sent: list[dict[str, str | None]] = []

        async def send_text(self, *, peer_user_id: str, text: str, context_token: str | None = None):
            self.sent.append({"peer_user_id": peer_user_id, "text": text, "context_token": context_token})
            return {"ret": 0, "errcode": 0}

    class _FakeGateway:
        def __init__(self) -> None:
            self.events: list[WechatInboundEvent] = []

        async def handle_inbound_event(self, *, event: WechatInboundEvent):
            self.events.append(event)
            return WechatHandleResult(
                deduplicated=False,
                session_id="session-1",
                reply="merged",
                action="chat",
            )

    client = _FakeClient()
    gateway = _FakeGateway()
    poller = WechatPoller(client=client, gateway_service=gateway, aggregation_window_seconds=10.0, late_media_window_ms=30000)

    text_event = WechatInboundEvent(
        event_id="text-1",
        user_id="wx-user-1",
        text="这是我跟对象一起去玩的《污秽》的密室逃脱",
        create_time_ms=1000,
        conversation_id="conv-1",
    )
    media_event = WechatInboundEvent(
        event_id="media-1",
        user_id="wx-user-1",
        media_download_failed=True,
        media_download_error="ConnectError('boom')",
        create_time_ms=1500,
        conversation_id="conv-1",
    )

    asyncio.run(poller._handle_event(text_event))
    assert gateway.events == []
    asyncio.run(poller._handle_event(media_event))

    assert len(gateway.events) == 1
    merged = gateway.events[0]
    assert merged.text == text_event.text
    assert merged.media_download_failed is True
    assert merged.media_download_error == "ConnectError('boom')"
    assert client.sent and client.sent[0]["text"] == "merged"


def test_build_wechat_runtime_wires_file_delivery_runtime(tmp_path):
    class _SpyWechatIlinkClient:
        def __init__(self, config) -> None:
            self.config = config

        async def aclose(self) -> None:
            return None

    container = build_test_container(tmp_path)
    settings = CoreSettings(
        clawbot_database_path=tmp_path / "clawbot.db",
        files_storage_dir=tmp_path / "files",
        wechat_enabled=True,
        wechat_token="test-token",
    )

    original_client_cls = sys.modules["core.cli.main"].WechatIlinkClient
    sys.modules["core.cli.main"].WechatIlinkClient = _SpyWechatIlinkClient
    try:
        _, client, gateway, _, _ = _build_wechat_runtime(settings=settings, container=container)
    finally:
        sys.modules["core.cli.main"].WechatIlinkClient = original_client_cls

    assert gateway._ilink_client is client
    assert container.tool_executor.gateway_service is gateway
    assert container.tool_executor.session_map_repository is not None


def test_archive_is_exposed_via_skill_run_tooling(tmp_path):
    container = build_test_container(tmp_path)

    base_specs = {spec.name: spec for spec in container.clawbot_service._build_tool_specs()}
    assert "archive" not in base_specs
    assert "skill_run" in base_specs

    class _Gateway:
        async def send_file_to_user(self, **kwargs):
            return {"ret": 0, "errcode": 0}

    session_map_repository = ChannelSessionMapRepository(container.database)
    container.configure_gateway(_Gateway(), session_map_repository)

    visible_specs = {spec.name: spec for spec in container.clawbot_service._build_tool_specs()}
    assert "archive" not in visible_specs
    assert "skill_run" in visible_specs


def test_skill_payload_uses_absolute_sqlite_database_url(tmp_path):
    container = build_test_container(tmp_path)
    session = container.session_repository.create()
    source_message = container.message_repository.add_user_message(session_id=session.id, content="deliver something")
    runtime = container.clawbot_service._agent_turn_runner.prepare_turn(
        session_id=session.id,
        user_text="把照片发我",
        source_message_id=source_message.id,
        raw_text="把照片发我",
        upload=None,
        context_snapshot=container.clawbot_service.load_context_snapshot(session_id=session.id),
    ).runtime
    payload = container.tool_executor._build_skill_payload(
        invocation=ToolInvocation(
            session_id=session.id,
            source_message_id=source_message.id,
            plan=ToolPlan(tool="skill_run", arguments={}, reason="test", source="test"),
            text="把照片发我",
            upload=None,
            context=container.tool_executor.runtime_manager.runtime_to_context(runtime),
        ),
        input_payload={"intent": "deliver"},
    )

    assert payload["database_url"].startswith("sqlite:///")
    assert str(tmp_path.resolve().as_posix()) in payload["database_url"]


def test_archive_skill_run_infers_missing_deliver_intent(tmp_path):
    container = build_test_container(tmp_path)
    session = container.session_repository.create()
    source_message = container.message_repository.add_user_message(session_id=session.id, content="upload photo")
    saved = asyncio.run(
        container.ingestion_service.ingest(
            session_id=session.id,
            source_message_id=source_message.id,
            source_event_id=None,
            text=None,
            upload=UploadFile(filename="wechat_image.jpg", file=BytesIO(b"fake image bytes")),
        )
    )
    item = container.item_repository.get_any(item_id=saved.item_id)

    class _Gateway:
        def __init__(self) -> None:
            self.calls = []

        async def send_file_to_user(self, **kwargs):
            self.calls.append(kwargs)
            return {"ret": 0, "errcode": 0}

    gateway = _Gateway()
    session_map_repository = ChannelSessionMapRepository(container.database)
    session_map_repository.upsert(channel="wechat", external_user_id="wx-user-1", session_id=session.id)
    container.configure_gateway(gateway, session_map_repository)

    runtime = container.clawbot_service._agent_turn_runner.prepare_turn(
        session_id=session.id,
        user_text="把这张照片发我",
        source_message_id=source_message.id,
        raw_text="把这张照片发我",
        upload=None,
        context_snapshot=container.clawbot_service.load_context_snapshot(session_id=session.id),
    ).runtime
    result = asyncio.run(
        container.tool_executor.execute_tool_call(
            session_id=session.id,
            tool_call=ToolCall(
                tool_name="skill_run",
                arguments={
                    "name": "archive-core",
                    "script_path": "scripts/archive_dispatch.py",
                    "input": {"query": item.title},
                },
            ),
            runtime=runtime,
        )
    )

    assert result.action == "retrieve"
    assert gateway.calls
    assert gateway.calls[0]["file_name"] == item.title


def test_send_file_tool_can_resolve_title_hint_and_deliver(tmp_path):
    class _Gateway:
        def __init__(self) -> None:
            self.calls = []

        async def send_file_to_user(self, **kwargs):
            self.calls.append(kwargs)
            return {"ret": 0, "errcode": 0}

    container = build_test_container(tmp_path)
    session = container.session_repository.create()
    source_message = container.message_repository.add_user_message(session_id=session.id, content="upload resume")
    saved = asyncio.run(
        container.ingestion_service.ingest(
            session_id=session.id,
            source_message_id=source_message.id,
            source_event_id=None,
            text=None,
            upload=UploadFile(filename="resume.pdf", file=BytesIO(b"%PDF-1.4 fake bytes")),
        )
    )
    item = container.item_repository.get_any(item_id=saved.item_id)

    gateway = _Gateway()
    session_map_repository = ChannelSessionMapRepository(container.database)
    session_map_repository.upsert(channel="wechat", external_user_id="wx-user-1", session_id=session.id)
    container.configure_gateway(gateway, session_map_repository)

    runtime = container.clawbot_service._agent_turn_runner.prepare_turn(
        session_id=session.id,
        user_text="把 resume 发给我",
        source_message_id=source_message.id,
        raw_text="把 resume 发给我",
        upload=None,
        context_snapshot=container.clawbot_service.load_context_snapshot(session_id=session.id),
    ).runtime
    result = asyncio.run(
        container.tool_executor.execute_tool_call(
            session_id=session.id,
            tool_call=archive_skill_call("deliver", query="resume"),
            runtime=runtime,
        )
    )

    assert result.action == "retrieve"
    assert result.metadata["item_id"] == item.id
    assert gateway.calls
    assert gateway.calls[0]["user_id"] == "wx-user-1"
    assert gateway.calls[0]["file_path"] == item.metadata_json["stored_file_path"]


def test_send_file_failure_reply_is_not_overridden_by_model_text(tmp_path):
    class _Gateway:
        async def send_file_to_user(self, **kwargs):
            raise httpx.ConnectError("network down")

    container = build_test_container(tmp_path)
    container.clawbot_service.model_client = StubSendFileFailureModelClient()
    session = container.session_repository.create()
    source_message = container.message_repository.add_user_message(session_id=session.id, content="upload photo")
    saved = asyncio.run(
        container.ingestion_service.ingest(
            session_id=session.id,
            source_message_id=source_message.id,
            source_event_id=None,
            text=None,
            upload=UploadFile(filename="wechat_image.jpg", file=BytesIO(b"fake image bytes")),
        )
    )
    item = container.item_repository.get_any(item_id=saved.item_id)

    session_map_repository = ChannelSessionMapRepository(container.database)
    session_map_repository.upsert(channel="wechat", external_user_id="wx-user-1", session_id=session.id)
    container.configure_gateway(_Gateway(), session_map_repository)

    user_message = container.message_repository.add_user_message(session_id=session.id, content=f"把 {item.title} 发给我")
    context_snapshot = container.clawbot_service.load_context_snapshot(session_id=session.id)
    result = asyncio.run(
        container.clawbot_service.run_agent_loop(
            session_id=session.id,
            source_message_id=user_message.id,
            user_text=f"把 {item.title} 发给我",
            raw_text=f"把 {item.title} 发给我",
            upload=None,
            context_snapshot=context_snapshot,
        )
    )

    assert item is not None
    assert result.primary_tool is not None
    assert result.primary_tool.tool_name == "skill_run"
    assert result.primary_tool.action == "chat"
    assert "network down" in result.reply
    assert result.reply != "已经发送，请查收。"


def test_toolless_delivery_request_forces_tool_enforcement(tmp_path):
    class _Gateway:
        def __init__(self) -> None:
            self.calls = []

        async def send_file_to_user(self, **kwargs):
            self.calls.append(kwargs)
            return {"ret": 0, "errcode": 0}

    container = build_test_container(tmp_path)
    retry_model = StubToollessDeliveryRetryModelClient()
    container.clawbot_service.model_client = retry_model
    session = container.session_repository.create()
    source_message = container.message_repository.add_user_message(session_id=session.id, content="upload photo")
    saved = asyncio.run(
        container.ingestion_service.ingest(
            session_id=session.id,
            source_message_id=source_message.id,
            source_event_id=None,
            text=None,
            upload=UploadFile(filename="wechat_image.jpg", file=BytesIO(b"fake image bytes")),
        )
    )
    item = container.item_repository.get_any(item_id=saved.item_id)

    gateway = _Gateway()
    session_map_repository = ChannelSessionMapRepository(container.database)
    session_map_repository.upsert(channel="wechat", external_user_id="wx-user-1", session_id=session.id)
    container.configure_gateway(gateway, session_map_repository)

    request_text = f"可以把 {item.title} 这张照片发我吗"
    user_message = container.message_repository.add_user_message(session_id=session.id, content=request_text)
    context_snapshot = container.clawbot_service.load_context_snapshot(session_id=session.id)
    result = asyncio.run(
        container.clawbot_service.run_agent_loop(
            session_id=session.id,
            source_message_id=user_message.id,
            user_text=request_text,
            raw_text=request_text,
            upload=None,
            context_snapshot=context_snapshot,
        )
    )

    assert item is not None
    assert retry_model.calls == 1
    assert result.primary_tool is not None
    assert result.primary_tool.tool_name == "skill_run"
    assert result.primary_tool.action == "retrieve"
    assert gateway.calls
    assert gateway.calls[0]["user_id"] == "wx-user-1"


def test_toolless_delivery_request_forces_archive_skill_run(tmp_path):
    class _Gateway:
        def __init__(self) -> None:
            self.calls = []

        async def send_file_to_user(self, **kwargs):
            self.calls.append(kwargs)
            return {"ret": 0, "errcode": 0}

    class _AlwaysChatModel(ModelClient):
        def generate(self, *, messages: list[Message], tools: list[ToolSpec]) -> ModelResponse:
            return ModelResponse(assistant_text="我先帮你看看。")

    container = build_test_container(tmp_path)
    container.clawbot_service.model_client = _AlwaysChatModel()
    session = container.session_repository.create()
    source_message = container.message_repository.add_user_message(session_id=session.id, content="upload photo")
    saved = asyncio.run(
        container.ingestion_service.ingest(
            session_id=session.id,
            source_message_id=source_message.id,
            source_event_id=None,
            text=None,
            upload=UploadFile(filename="wechat_image.jpg", file=BytesIO(b"fake image bytes")),
        )
    )
    item = container.item_repository.get_any(item_id=saved.item_id)

    gateway = _Gateway()
    session_map_repository = ChannelSessionMapRepository(container.database)
    session_map_repository.upsert(channel="wechat", external_user_id="wx-user-1", session_id=session.id)
    container.configure_gateway(gateway, session_map_repository)

    request_text = f"把 {item.title} 发给我"
    user_message = container.message_repository.add_user_message(session_id=session.id, content=request_text)
    context_snapshot = container.clawbot_service.load_context_snapshot(session_id=session.id)
    result = asyncio.run(
        container.clawbot_service.run_agent_loop(
            session_id=session.id,
            source_message_id=user_message.id,
            user_text=request_text,
            raw_text=request_text,
            upload=None,
            context_snapshot=context_snapshot,
        )
    )

    assert result.primary_tool is not None
    assert result.primary_tool.tool_name == "skill_run"
    assert result.primary_tool.arguments["name"] == "archive-core"
    assert result.primary_tool.arguments["script_path"] == "scripts/archive_dispatch.py"
    assert result.primary_tool.action == "retrieve"
    assert gateway.calls


def test_record_inbound_turn_persists_upload_reference(tmp_path):
    container = build_test_container(tmp_path)
    session = container.session_repository.create()
    upload = UploadFile(filename="wechat_image.jpg", file=BytesIO(b"fake image bytes"))

    recorded = asyncio.run(
        container.clawbot_service._session_shell.record_inbound_turn(
            session_id=session.id,
            text=None,
            upload=upload,
            source_metadata={"channel": "wechat", "external_user_id": "wx-user-1"},
        )
    )

    event = container.source_event_repository.get_any(event_id=recorded.source_event_id)
    assert event.stored_file_path
    assert Path(event.stored_file_path).exists()
    assert event.original_file_name == "wechat_image.jpg"


def test_archive_save_text_after_recent_upload_saves_asset_with_note(tmp_path):
    container = build_test_container(tmp_path)
    session = container.session_repository.create()
    upload = UploadFile(filename="wechat_image.jpg", file=BytesIO(b"fake image bytes"))

    recorded_upload = asyncio.run(
        container.clawbot_service._session_shell.record_inbound_turn(
            session_id=session.id,
            text=None,
            upload=upload,
            source_metadata={"channel": "wechat", "external_user_id": "wx-user-1"},
        )
    )
    source_event = container.source_event_repository.get_any(event_id=recorded_upload.source_event_id)
    assert source_event.stored_file_path

    note_text = "这是我和女朋友一起去玩密室逃脱拍的照片，请你保存"
    note_message = container.message_repository.add_user_message(session_id=session.id, content=note_text)
    runtime = container.clawbot_service._agent_turn_runner.prepare_turn(
        session_id=session.id,
        user_text=note_text,
        source_message_id=note_message.id,
        raw_text=note_text,
        upload=None,
        context_snapshot=container.clawbot_service.load_context_snapshot(session_id=session.id),
    ).runtime
    result = asyncio.run(
        container.tool_executor.execute_tool_call(
            session_id=session.id,
            tool_call=archive_skill_call("save", text=note_text),
            runtime=runtime,
        )
    )

    assert result.action == "capture"
    items = container.item_repository.list_by_session(session_id=session.id, current_only=True)
    assert len(items) == 1
    saved_item = items[0]
    assert saved_item.item_type == "image"
    assert saved_item.metadata_json.get("user_note") == note_text
    assert saved_item.metadata_json.get("stored_file_path")


def test_send_file_tool_clarifies_when_descriptor_matches_multiple_files(tmp_path):
    class _Gateway:
        def __init__(self) -> None:
            self.calls = []

        async def send_file_to_user(self, **kwargs):
            self.calls.append(kwargs)
            return {"ret": 0, "errcode": 0}

    container = build_test_container(tmp_path)
    session = container.session_repository.create()
    source_message = container.message_repository.add_user_message(session_id=session.id, content="upload holiday photos")

    first_path = tmp_path / "holiday-1.jpg"
    second_path = tmp_path / "holiday-2.jpg"
    first_path.write_bytes(b"fake image bytes 1")
    second_path.write_bytes(b"fake image bytes 2")
    note = "五一假期与女朋友一起出去玩拍摄的照片"

    first_saved = asyncio.run(
        container.ingestion_service.ingest_saved_upload(
            session_id=session.id,
            source_message_id=source_message.id,
            source_event_id=None,
            file_path=first_path,
            filename="holiday-1.jpg",
            user_note=note,
        )
    )
    second_saved = asyncio.run(
        container.ingestion_service.ingest_saved_upload(
            session_id=session.id,
            source_message_id=source_message.id,
            source_event_id=None,
            file_path=second_path,
            filename="holiday-2.jpg",
            user_note=note,
        )
    )
    assert first_saved.item_id and second_saved.item_id

    note_item = container.item_repository.create(
        session_id=session.id,
        source_message_id=source_message.id,
        source_event_id=None,
        item_type="text_note",
        title=note,
        raw_content=note,
        normalized_text=note,
        summary=note,
        metadata={},
        locator_hint="Look for the file message named `wechat_image.jpg` on the saved date.",
    )

    gateway = _Gateway()
    session_map_repository = ChannelSessionMapRepository(container.database)
    session_map_repository.upsert(channel="wechat", external_user_id="wx-user-1", session_id=session.id)
    container.configure_gateway(gateway, session_map_repository)

    runtime = container.clawbot_service._agent_turn_runner.prepare_turn(
        session_id=session.id,
        user_text="把这些照片发给我",
        source_message_id=source_message.id,
        raw_text="把这些照片发给我",
        upload=None,
        context_snapshot=container.clawbot_service.load_context_snapshot(session_id=session.id),
    ).runtime
    result = asyncio.run(
        container.tool_executor.execute_tool_call(
            session_id=session.id,
            tool_call=archive_skill_call("deliver", item_id=note_item.id),
            runtime=runtime,
        )
    )

    assert result.action == "clarify"
    assert result.disposition == "clarify"
    assert gateway.calls == []
