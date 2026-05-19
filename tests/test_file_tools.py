from __future__ import annotations

import asyncio
from pathlib import Path

from core.clawbot import RuntimeToolExecutor
from core.clawbot.planner import ToolPlan
from core.clawbot.service import ClawBotService
from core.clawbot.tools import ToolInvocation
from core.agent.runtime_manager import AgentRuntimeManager
from core.agent.runtime_state import RuntimeContextSnapshot
from core.ingestion.service import IngestionService
from core.llm.base import ModelClient
from core.schemas.message import Message
from core.schemas.model import ModelResponse
from core.schemas.tool import ToolCall
from core.storage.db import DatabaseManager
from core.storage.repositories import (
    ItemRepository,
    MessageRepository,
    PendingStateRepository,
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
    assert "File `src/app.py` lines 1-2 of 5:" in read
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
        assert "workspace root" in str(exc)
    else:
        raise AssertionError("Expected workspace escape to be rejected")


def test_file_tool_store_writes_text_file(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = FileToolStore(workspace)

    result = store.write_file(
        path="src/new_module.py",
        content="def created_by_agent():\n    return True\n",
    )

    written = (workspace / "src" / "new_module.py").read_text(encoding="utf-8")
    assert "Wrote `src/new_module.py`" in result
    assert "created_by_agent" in written


def test_runtime_tool_executor_file_tools(tmp_path: Path) -> None:
    database = DatabaseManager(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    database.create_all()
    item_repository = ItemRepository(database)
    message_repository = MessageRepository(database)
    user_signal_repository = UserSignalRepository(database)
    pending_state_repository = PendingStateRepository(database)
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
    executor = RuntimeToolExecutor(
        ingestion_service=ingestion_service,
        item_repository=item_repository,
        pending_state_repository=pending_state_repository,
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


def test_runtime_tool_executor_write_file(tmp_path: Path) -> None:
    database = DatabaseManager(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    database.create_all()
    item_repository = ItemRepository(database)
    message_repository = MessageRepository(database)
    user_signal_repository = UserSignalRepository(database)
    pending_state_repository = PendingStateRepository(database)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
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
        file_tool_root=workspace,
    )

    result = executor._tool_write_file(
        ToolInvocation(
            session_id="session-1",
            source_message_id="msg-1",
            plan=ToolPlan(
                tool="write_file",
                arguments={"path": "src/generated.py", "content": "VALUE = 1\n"},
                reason="test",
            ),
            text=None,
            upload=None,
            context={},
        )
    )

    assert result.action == "edit"
    assert "Wrote `src/generated.py`" in result.reply
    assert (workspace / "src" / "generated.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def test_clawbot_service_exposes_file_tool_specs(tmp_path: Path) -> None:
    database = DatabaseManager(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    database.create_all()
    session_repository = SessionRepository(database)
    session_summary_repository = SessionSummaryRepository(database)
    message_repository = MessageRepository(database)
    source_event_repository = SourceEventRepository(database)
    item_repository = ItemRepository(database)
    pending_state_repository = PendingStateRepository(database)
    user_signal_repository = UserSignalRepository(database)
    topic_repository = TopicRepository(database)
    ingestion_service = IngestionService(
        item_repository=item_repository,
        message_repository=message_repository,
        user_signal_repository=user_signal_repository,
        storage_dir=tmp_path / "files",
    )
    tool_executor = RuntimeToolExecutor(
        ingestion_service=ingestion_service,
        item_repository=item_repository,
        pending_state_repository=pending_state_repository,
        file_tool_root=tmp_path / "workspace",
    )
    service = ClawBotService(
        session_repository=session_repository,
        session_summary_repository=session_summary_repository,
        message_repository=message_repository,
        source_event_repository=source_event_repository,
        item_repository=item_repository,
        ingestion_service=ingestion_service,
        pending_state_repository=pending_state_repository,
        user_signal_repository=user_signal_repository,
        topic_repository=topic_repository,
        model_client=DummyModelClient(),
        tool_executor=tool_executor,
        file_tool_root=tmp_path / "workspace",
    )

    specs = {spec.name for spec in service._build_tool_specs()}

    assert {"list_files", "search_files", "read_file", "write_file"}.issubset(specs)
    assert {"web_search", "web_fetch", "search_sessions"}.issubset(specs)


def test_clawbot_service_cli_preset_exposes_shell_exec(tmp_path: Path) -> None:
    database = DatabaseManager(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    database.create_all()
    session_repository = SessionRepository(database)
    session_summary_repository = SessionSummaryRepository(database)
    message_repository = MessageRepository(database)
    source_event_repository = SourceEventRepository(database)
    item_repository = ItemRepository(database)
    pending_state_repository = PendingStateRepository(database)
    user_signal_repository = UserSignalRepository(database)
    topic_repository = TopicRepository(database)
    ingestion_service = IngestionService(
        item_repository=item_repository,
        message_repository=message_repository,
        user_signal_repository=user_signal_repository,
        storage_dir=tmp_path / "files",
    )
    tool_executor = RuntimeToolExecutor(
        ingestion_service=ingestion_service,
        item_repository=item_repository,
        pending_state_repository=pending_state_repository,
        file_tool_root=tmp_path / "workspace",
    )
    service = ClawBotService(
        session_repository=session_repository,
        session_summary_repository=session_summary_repository,
        message_repository=message_repository,
        source_event_repository=source_event_repository,
        item_repository=item_repository,
        ingestion_service=ingestion_service,
        pending_state_repository=pending_state_repository,
        user_signal_repository=user_signal_repository,
        topic_repository=topic_repository,
        model_client=DummyModelClient(),
        tool_executor=tool_executor,
        file_tool_root=tmp_path / "workspace",
        toolset_preset="cora-cli",
    )

    specs = {spec.name for spec in service._build_tool_specs()}

    assert "shell_exec" in specs


def test_runtime_tool_executor_strips_tool_name_whitespace(tmp_path: Path) -> None:
    database = DatabaseManager(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    database.create_all()
    item_repository = ItemRepository(database)
    message_repository = MessageRepository(database)
    user_signal_repository = UserSignalRepository(database)
    pending_state_repository = PendingStateRepository(database)
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
    runtime_manager = AgentRuntimeManager(pending_state_repository=pending_state_repository)
    executor = RuntimeToolExecutor(
        ingestion_service=ingestion_service,
        item_repository=item_repository,
        pending_state_repository=pending_state_repository,
        file_tool_root=workspace,
        runtime_manager=runtime_manager,
    )
    runtime = runtime_manager.build_runtime_state(
        session_id="session-1",
        context_snapshot=RuntimeContextSnapshot(),
        source_message_id="msg-1",
        raw_text="find the memory constant",
        upload=None,
    )

    result = asyncio.run(
        executor.execute_tool_call(
            session_id="session-1",
            tool_call=ToolCall(
                tool_name=" search_files ",
                arguments={"query": "USER_MEMORY", "path": "src"},
            ),
            runtime=runtime,
        ),
    )

    assert result.success
    assert "src/memory.py:1" in result.content
