from __future__ import annotations

from pathlib import Path

from core.clawbot.tools import ArchiveToolExecutor, ToolInvocation
from core.clawbot.planner import ToolPlan
from core.ingestion.service import IngestionService
from core.storage.db import DatabaseManager
from core.storage.repositories import ClarificationRepository, ItemRepository, MessageRepository, UserSignalRepository
from core.user_memory import UserMemoryStore


def test_user_memory_store_add_replace_remove(tmp_path: Path) -> None:
    store = UserMemoryStore(tmp_path / "user-memory" / "USER.md")

    created = store.add("用户常买的布洛芬品牌是芬必得。")
    updated = store.replace("布洛芬", "用户常买的布洛芬品牌是美林。")
    removed = store.remove("美林")

    assert "已记住" in created
    assert "已更新" in updated
    assert "已删除" in removed
    assert store.read_entries() == []


def test_user_memory_store_render_creates_template(tmp_path: Path) -> None:
    store = UserMemoryStore(tmp_path / "user-memory" / "USER.md")

    rendered = store.render()

    assert "# User Memory" in rendered
    assert store.path.exists()


def test_archive_tool_executor_user_memory_tool(tmp_path: Path) -> None:
    database = DatabaseManager(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    database.create_all()
    item_repository = ItemRepository(database)
    message_repository = MessageRepository(database)
    user_signal_repository = UserSignalRepository(database)
    clarification_repository = ClarificationRepository(database)
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
        user_memory_path=tmp_path / "user-memory" / "USER.md",
    )

    add_result = executor._tool_user_memory(
        ToolInvocation(
            session_id="session-1",
            source_message_id="msg-1",
            plan=ToolPlan(tool="user_memory", arguments={"action": "add", "content": "用户偏好简洁回答。"}, reason="test"),
            text=None,
            upload=None,
            context={},
        )
    )
    read_result = executor._tool_user_memory(
        ToolInvocation(
            session_id="session-1",
            source_message_id="msg-2",
            plan=ToolPlan(tool="user_memory", arguments={"action": "read"}, reason="test"),
            text=None,
            upload=None,
            context={},
        )
    )

    assert "已记住" in add_result.reply
    assert "用户偏好简洁回答" in read_result.reply
