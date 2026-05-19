from __future__ import annotations

import json
from pathlib import Path

import httpx

from core.clawbot import RuntimeToolExecutor
from core.clawbot.planner import ToolPlan
from core.tools.registry import ToolInvocation
from core.ingestion.service import IngestionService
from core.storage.db import DatabaseManager
from core.storage.repositories import ItemRepository, MessageRepository, PendingStateRepository, UserSignalRepository


def _build_web_client() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.duckduckgo.com":
            return httpx.Response(
                200,
                json={
                    "Results": [
                        {
                            "FirstURL": "https://example.com/guide",
                            "Text": "Example guide for the latest release",
                        }
                    ]
                },
                request=request,
            )
        if request.url.host == "html.duckduckgo.com":
            return httpx.Response(
                200,
                text=(
                    '<html><body><a class="result__a" '
                    'href="https://example.com/fallback">Fallback Result</a></body></html>'
                ),
                request=request,
            )
        if request.url.host == "example.com":
            return httpx.Response(
                200,
                text=(
                    "<html><head><title>Example Article</title></head>"
                    "<body><main><h1>Shipping Notes</h1>"
                    "<p>The release is now available with the new sync flow.</p>"
                    "</main></body></html>"
                ),
                headers={"content-type": "text/html; charset=utf-8"},
                request=request,
            )
        return httpx.Response(404, text="not found", request=request)

    return httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    )


def _build_web_client_with_invalid_json_api() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.duckduckgo.com":
            return httpx.Response(
                200,
                text="<html><body>temporary challenge page</body></html>",
                headers={"content-type": "text/html; charset=utf-8"},
                request=request,
            )
        if request.url.host == "html.duckduckgo.com":
            return httpx.Response(
                200,
                text=(
                    '<html><body><a class="result__a" '
                    'href="https://example.com/fallback">Fallback Result</a></body></html>'
                ),
                request=request,
            )
        return httpx.Response(404, text="not found", request=request)

    return httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    )


def _build_tavily_web_client() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.tavily.com" and request.url.path == "/search":
            assert request.headers.get("Authorization") == "Bearer tvly-test-key"
            payload = json.loads(request.content.decode("utf-8"))
            assert payload["query"] == "latest OpenAI releases"
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "title": "OpenAI Release Roundup",
                            "url": "https://example.com/openai-releases",
                            "content": "A compact list of recent OpenAI launches and updates.",
                        }
                    ]
                },
                request=request,
            )
        if request.url.host == "example.com":
            return httpx.Response(
                200,
                text=(
                    "<html><head><title>OpenAI Release Roundup</title></head>"
                    "<body><main><p>Recent OpenAI launches and updates.</p></main></body></html>"
                ),
                headers={"content-type": "text/html; charset=utf-8"},
                request=request,
            )
        return httpx.Response(404, text="not found", request=request)

    return httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    )


def _build_web_client_with_tavily_extract_fallback() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "example.com":
            raise httpx.ConnectError("blocked", request=request)
        if request.url.host == "api.tavily.com" and request.url.path == "/extract":
            assert request.headers.get("Authorization") == "Bearer tvly-test-key"
            payload = json.loads(request.content.decode("utf-8"))
            assert payload["urls"] == ["https://example.com/protected"]
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "url": "https://example.com/protected",
                            "raw_content": "Recovered content from Tavily extract for a blocked page.",
                        }
                    ]
                },
                request=request,
            )
        return httpx.Response(404, text="not found", request=request)

    return httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    )


def test_runtime_tool_executor_web_fetch_extracts_page_text(tmp_path: Path) -> None:
    database = DatabaseManager(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    database.create_all()
    item_repository = ItemRepository(database)
    message_repository = MessageRepository(database)
    user_signal_repository = UserSignalRepository(database)
    pending_state_repository = PendingStateRepository(database)
    ingestion_service = IngestionService(
        item_repository=item_repository,
        message_repository=message_repository,
        user_signal_repository=user_signal_repository,
        storage_dir=tmp_path / "files",
    )
    executor = RuntimeToolExecutor(
        ingestion_service=ingestion_service,
        item_repository=item_repository,
        pending_state_repository=pending_state_repository,
        message_repository=message_repository,
        session_summary_repository=None,
        file_tool_root=tmp_path / "workspace",
        web_http_client=_build_web_client(),
    )

    result = executor._tool_web_fetch(
        ToolInvocation(
            session_id="session-1",
            source_message_id="msg-1",
            plan=ToolPlan(
                tool="web_fetch",
                arguments={"url": "https://example.com/article"},
                reason="test",
            ),
            text="Check this article",
            upload=None,
            context={},
        )
    )

    assert result.status == "completed"
    assert result.action == "research"
    assert "Example Article" in result.reply
    assert "new sync flow" in result.reply


def test_runtime_tool_executor_web_search_returns_mock_results(tmp_path: Path) -> None:
    database = DatabaseManager(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    database.create_all()
    item_repository = ItemRepository(database)
    message_repository = MessageRepository(database)
    user_signal_repository = UserSignalRepository(database)
    pending_state_repository = PendingStateRepository(database)
    ingestion_service = IngestionService(
        item_repository=item_repository,
        message_repository=message_repository,
        user_signal_repository=user_signal_repository,
        storage_dir=tmp_path / "files",
    )
    executor = RuntimeToolExecutor(
        ingestion_service=ingestion_service,
        item_repository=item_repository,
        pending_state_repository=pending_state_repository,
        message_repository=message_repository,
        file_tool_root=tmp_path / "workspace",
        web_http_client=_build_web_client(),
    )

    result = executor._tool_web_search(
        ToolInvocation(
            session_id="session-1",
            source_message_id="msg-1",
            plan=ToolPlan(
                tool="web_search",
                arguments={"query": "latest release guide"},
                reason="test",
            ),
            text="Search the web",
            upload=None,
            context={},
        )
    )

    assert result.status == "completed"
    assert result.action == "research"
    assert "Example guide for the latest release" in result.reply
    assert "https://example.com/guide" in result.reply


def test_runtime_tool_executor_web_search_falls_back_when_json_api_is_invalid(tmp_path: Path) -> None:
    database = DatabaseManager(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    database.create_all()
    item_repository = ItemRepository(database)
    message_repository = MessageRepository(database)
    user_signal_repository = UserSignalRepository(database)
    pending_state_repository = PendingStateRepository(database)
    ingestion_service = IngestionService(
        item_repository=item_repository,
        message_repository=message_repository,
        user_signal_repository=user_signal_repository,
        storage_dir=tmp_path / "files",
    )
    executor = RuntimeToolExecutor(
        ingestion_service=ingestion_service,
        item_repository=item_repository,
        pending_state_repository=pending_state_repository,
        message_repository=message_repository,
        file_tool_root=tmp_path / "workspace",
        web_http_client=_build_web_client_with_invalid_json_api(),
    )

    result = executor._tool_web_search(
        ToolInvocation(
            session_id="session-1",
            source_message_id="msg-1",
            plan=ToolPlan(
                tool="web_search",
                arguments={"query": "fallback search"},
                reason="test",
            ),
            text="Search the web",
            upload=None,
            context={},
        )
    )

    assert result.status == "completed"
    assert result.action == "research"
    assert "Fallback Result" in result.reply
    assert "https://example.com/fallback" in result.reply


def test_runtime_tool_executor_web_search_uses_tavily_when_configured(tmp_path: Path) -> None:
    database = DatabaseManager(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    database.create_all()
    item_repository = ItemRepository(database)
    message_repository = MessageRepository(database)
    user_signal_repository = UserSignalRepository(database)
    pending_state_repository = PendingStateRepository(database)
    ingestion_service = IngestionService(
        item_repository=item_repository,
        message_repository=message_repository,
        user_signal_repository=user_signal_repository,
        storage_dir=tmp_path / "files",
    )
    executor = RuntimeToolExecutor(
        ingestion_service=ingestion_service,
        item_repository=item_repository,
        pending_state_repository=pending_state_repository,
        message_repository=message_repository,
        file_tool_root=tmp_path / "workspace",
        web_http_client=_build_tavily_web_client(),
        web_tavily_api_key="tvly-test-key",
    )

    result = executor._tool_web_search(
        ToolInvocation(
            session_id="session-1",
            source_message_id="msg-1",
            plan=ToolPlan(
                tool="web_search",
                arguments={"query": "latest OpenAI releases"},
                reason="test",
            ),
            text="Search the web",
            upload=None,
            context={},
        )
    )

    assert result.status == "completed"
    assert result.action == "research"
    assert "OpenAI Release Roundup" in result.reply
    assert "https://example.com/openai-releases" in result.reply
    assert result.metadata is not None
    assert result.metadata.get("provider") == "tavily"


def test_runtime_tool_executor_web_fetch_falls_back_to_tavily_extract(tmp_path: Path) -> None:
    database = DatabaseManager(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    database.create_all()
    item_repository = ItemRepository(database)
    message_repository = MessageRepository(database)
    user_signal_repository = UserSignalRepository(database)
    pending_state_repository = PendingStateRepository(database)
    ingestion_service = IngestionService(
        item_repository=item_repository,
        message_repository=message_repository,
        user_signal_repository=user_signal_repository,
        storage_dir=tmp_path / "files",
    )
    executor = RuntimeToolExecutor(
        ingestion_service=ingestion_service,
        item_repository=item_repository,
        pending_state_repository=pending_state_repository,
        message_repository=message_repository,
        file_tool_root=tmp_path / "workspace",
        web_http_client=_build_web_client_with_tavily_extract_fallback(),
        web_tavily_api_key="tvly-test-key",
    )

    result = executor._tool_web_fetch(
        ToolInvocation(
            session_id="session-1",
            source_message_id="msg-1",
            plan=ToolPlan(
                tool="web_fetch",
                arguments={"url": "https://example.com/protected"},
                reason="test",
            ),
            text="Fetch this page",
            upload=None,
            context={},
        )
    )

    assert result.status == "completed"
    assert result.action == "research"
    assert "Recovered content from Tavily extract" in result.reply
    assert "Provider: tavily_extract" in result.reply
    assert result.metadata is not None
    assert result.metadata.get("provider") == "tavily_extract"
