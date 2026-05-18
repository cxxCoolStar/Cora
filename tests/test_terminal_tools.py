from __future__ import annotations

import asyncio
from pathlib import Path
import sys

from core.agent.runtime_manager import AgentRuntimeManager
from core.agent.runtime_state import RuntimeContextSnapshot
from core.clawbot import RuntimeToolExecutor
from core.clawbot.planner import ToolPlan
from core.tools.registry import ToolInvocation
from core.ingestion.service import IngestionService
from core.schemas.tool import ToolCall
from core.storage.db import DatabaseManager
from core.storage.repositories import ItemRepository, MessageRepository, PendingStateRepository, UserSignalRepository
from core.tools.terminal_tools import TerminalToolStore


def _python_command(source: str) -> str:
    return f'"{sys.executable}" -c "{source}"'


def test_terminal_tool_store_runs_command_in_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    target_dir = workspace / "src"
    target_dir.mkdir(parents=True)
    store = TerminalToolStore(workspace)

    result = store.run_command(
        command=_python_command("from pathlib import Path; print(Path.cwd().name)"),
        cwd="src",
    )

    assert result.status == "completed"
    assert result.exit_code == 0
    assert result.cwd == "src"
    assert result.stdout == "src"


def test_terminal_tool_store_reports_non_zero_exit_code(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = TerminalToolStore(workspace)

    result = store.run_command(
        command=_python_command("import sys; sys.stderr.write('boom\\n'); sys.exit(3)"),
    )

    assert result.status == "failed"
    assert result.exit_code == 3
    assert result.stderr == "boom"


def test_runtime_tool_executor_shell_exec_returns_metadata(tmp_path: Path) -> None:
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

    result = executor._tool_shell_exec(
        ToolInvocation(
            session_id="session-1",
            source_message_id="msg-1",
            plan=ToolPlan(
                tool="shell_exec",
                arguments={"command": _python_command("print('terminal-ok')")},
                reason="test",
            ),
            text=None,
            upload=None,
            context={},
        )
    )

    assert result.action == "execute"
    assert result.status == "completed"
    assert result.metadata is not None
    assert result.metadata["exit_code"] == 0
    assert "terminal-ok" in result.reply


def test_runtime_tool_executor_executes_shell_exec_tool_call(tmp_path: Path) -> None:
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
        raw_text="run a command",
        upload=None,
    )

    result = asyncio.run(
        executor.execute_tool_call(
            session_id="session-1",
            tool_call=ToolCall(
                tool_name="shell_exec",
                arguments={"command": _python_command("print('tool-call-ok')")},
            ),
            runtime=runtime,
        )
    )

    assert result.success
    assert result.action == "execute"
    assert result.metadata["exit_code"] == 0
    assert "tool-call-ok" in result.content
