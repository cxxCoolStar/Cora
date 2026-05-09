from __future__ import annotations

from pathlib import Path

from core.clawbot.planner import ToolPlan
from core.clawbot.service import ClawBotService
from core.clawbot.tools import ArchiveToolExecutor, ToolInvocation
from core.ingestion.service import IngestionService
from core.llm.base import ModelClient
from core.schemas.message import Message
from core.schemas.model import ModelResponse
from core.storage.db import DatabaseManager
from core.storage.repositories import (
    ClarificationRepository,
    ItemRepository,
    MessageRepository,
    SessionRepository,
    SessionSummaryRepository,
    SourceEventRepository,
    TopicRepository,
    UserSignalRepository,
)
from core.tools.file_tools import FileToolStore


class DummyModelClient(ModelClient):
    def generate(self, *, messages: list[Message], tools: list[object]) -> ModelResponse:
        return ModelResponse(assistant_text="ok")


def test_file_tool_store_lists_reads_and_searches_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source_dir = workspace / "src"
    source_dir.mkdir(parents=True)
    app_file = source_dir / "app.py"
    app_file.write_text(
        "def hello_agent():\n"
        "    return 'hello_agent'\n"
        "\n"
        "def other():\n"
        "    return 'other'\n",
        encoding="utf-8",
    )

    store = FileToolStore(workspace)

    listed = store.list_files(path="src", recursive=True)
    searched = store.search_files(query="hello_agent", path="src")
    read = store.read_file(path="src/app.py", start_line=1, end_line=2)

    assert "[file] src/app.py" in listed
    assert "src/app.py:1" in searched or "src/app.py:2" in searched
    assert "文件 `src/app.py` 第 1-2 行" in read
    assert "1: def hello_agent()" in read


def test_file_tool_store_rejects_workspace_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("do not read", encoding="utf-8")

    store = FileToolStore(workspace)

    try:
        store.read_file(path="../secret.txt")
    except ValueError as exc:
        assert "工作区范围" in str(exc)
    else:
        raise AssertionError("Expected workspace escape to be rejected")


def test_archive_tool_executor_file_tools(tmp_path: Path) -> None:
    database = DatabaseManager(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    database.create_all()
    item_repository = ItemRepository(database)
    message_repository = MessageRepository(database)
    user_signal_repository = UserSignalRepository(database)
    clarification_repository = ClarificationRepository(database)
    workspace = tmp_path / "workspace"
    source_dir = workspace / "src"
    source_dir.mkdir(parents=True)
    (source_dir / "memory.py").write_text("USER_MEMORY = 'enabled'\n", encoding="utf-8")
    ingestion_service = IngestionService(
        item_repository=item_repository,
        message_repository=message_repository,
        user_signal_repository=user_signal_repository,
        storage_dir=tmp_path / "files",
    )
    executor = ArchiveToolExecutor(
        ingestion_service=ingestion_service,
        item_repository=item_repository,
        clarification_repository=clarification_repository,
        file_tool_root=workspace,
    )

    result = executor._tool_search_files(
        ToolInvocation(
            session_id="session-1",
            source_message_id="msg-1",
            plan=ToolPlan(tool="search_files", arguments={"query": "USER_MEMORY", "path": "src"}, reason="test"),
            text=None,
            upload=None,
            context={},
        )
    )

    assert result.action == "inspect"
    assert "src/memory.py:1" in result.reply


def test_clawbot_service_exposes_file_tool_specs(tmp_path: Path) -> None:
    database = DatabaseManager(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    database.create_all()
    session_repository = SessionRepository(database)
    session_summary_repository = SessionSummaryRepository(database)
    message_repository = MessageRepository(database)
    source_event_repository = SourceEventRepository(database)
    item_repository = ItemRepository(database)
    clarification_repository = ClarificationRepository(database)
    user_signal_repository = UserSignalRepository(database)
    topic_repository = TopicRepository(database)
    ingestion_service = IngestionService(
        item_repository=item_repository,
        message_repository=message_repository,
        user_signal_repository=user_signal_repository,
        storage_dir=tmp_path / "files",
    )
    tool_executor = ArchiveToolExecutor(
        ingestion_service=ingestion_service,
        item_repository=item_repository,
        clarification_repository=clarification_repository,
        file_tool_root=tmp_path / "workspace",
    )
    service = ClawBotService(
        session_repository=session_repository,
        session_summary_repository=session_summary_repository,
        message_repository=message_repository,
        source_event_repository=source_event_repository,
        item_repository=item_repository,
        ingestion_service=ingestion_service,
        clarification_repository=clarification_repository,
        user_signal_repository=user_signal_repository,
        topic_repository=topic_repository,
        model_client=DummyModelClient(),
        tool_executor=tool_executor,
        file_tool_root=tmp_path / "workspace",
    )

    specs = {spec.name for spec in service._build_tool_specs()}

    assert {"list_files", "search_files", "read_file"}.issubset(specs)
