from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from core.clawbot import RuntimeToolExecutor
from core.clawbot.planner import ToolPlan
from core.tools.registry import ToolInvocation
from core.ingestion.service import IngestionService
from core.storage.db import DatabaseManager
from core.storage.repositories import (
    ChannelSessionMapRepository,
    ItemRepository,
    MessageRepository,
    PendingStateRepository,
    SessionRepository,
    SessionSummaryRepository,
    UserSignalRepository,
)


def test_runtime_tool_executor_searches_current_and_prior_wechat_sessions(tmp_path: Path) -> None:
    database = DatabaseManager(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    database.create_all()
    session_repository = SessionRepository(database)
    session_summary_repository = SessionSummaryRepository(database)
    message_repository = MessageRepository(database)
    session_map_repository = ChannelSessionMapRepository(database)
    item_repository = ItemRepository(database)
    pending_state_repository = PendingStateRepository(database)
    user_signal_repository = UserSignalRepository(database)

    previous_session = session_repository.create()
    current_session = session_repository.create()

    previous_message = message_repository.add_user_message(
        session_id=previous_session.id,
        content="Please remember the office VPN profile is named corp-vpn and uses the tokyo gateway.",
    )
    message_repository.add_assistant_message(
        session_id=previous_session.id,
        content="Stored the VPN note for later reference.",
    )
    session_summary_repository.upsert(
        session_id=previous_session.id,
        summary={
            "version": 1,
            "covered_message_count": 2,
            "last_compacted_message_id": previous_message.id,
            "summary": {
                "active_task": "none",
                "user_facts": [],
                "open_loops": [],
                "resolved_requests": ["VPN profile corp-vpn uses the tokyo gateway."],
                "recent_decisions": ["Keep the office VPN settings handy for later recall."],
                "critical_context": ["The user may ask for the saved VPN config in a later session."],
            },
        },
    )

    base_time = datetime.now(UTC) - timedelta(days=1)
    session_map_repository.upsert(
        channel="wechat",
        external_user_id="wechat-user-1",
        session_id=previous_session.id,
        session_started_at=base_time,
        last_interaction_at=base_time,
        last_reset_reason="initial_bind",
    )
    session_map_repository.upsert(
        channel="wechat",
        external_user_id="wechat-user-1",
        session_id=current_session.id,
        session_started_at=base_time + timedelta(hours=1),
        last_interaction_at=base_time + timedelta(hours=1),
        last_reset_reason="daily_reset",
    )

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
        session_summary_repository=session_summary_repository,
        session_map_repository=session_map_repository,
        file_tool_root=tmp_path / "workspace",
        channel_name="wechat",
    )

    result = executor._tool_search_sessions(
        ToolInvocation(
            session_id=current_session.id,
            source_message_id="msg-1",
            plan=ToolPlan(
                tool="search_sessions",
                arguments={"query": "corp vpn tokyo", "limit": 4},
                reason="test",
            ),
            text="Find the earlier VPN conversation",
            upload=None,
            context={},
        )
    )

    assert result.status == "completed"
    assert result.action == "retrieve"
    assert "corp-vpn" in result.reply
    assert previous_session.id in result.reply
    assert result.metadata is not None
    assert previous_session.id in result.metadata["sessions_scanned"]


def test_runtime_tool_executor_search_sessions_fails_when_query_is_empty(tmp_path: Path) -> None:
    database = DatabaseManager(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    database.create_all()
    message_repository = MessageRepository(database)
    session_summary_repository = SessionSummaryRepository(database)
    session_map_repository = ChannelSessionMapRepository(database)
    item_repository = ItemRepository(database)
    pending_state_repository = PendingStateRepository(database)
    user_signal_repository = UserSignalRepository(database)
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
        session_summary_repository=session_summary_repository,
        session_map_repository=session_map_repository,
        file_tool_root=tmp_path / "workspace",
    )

    result = executor._tool_search_sessions(
        ToolInvocation(
            session_id="session-1",
            source_message_id="msg-1",
            plan=ToolPlan(tool="search_sessions", arguments={"query": ""}, reason="test"),
            text=None,
            upload=None,
            context={},
        )
    )

    assert result.status == "failed"
    assert "query cannot be empty" in result.reply
