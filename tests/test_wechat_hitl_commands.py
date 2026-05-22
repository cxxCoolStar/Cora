from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.agent.hitl_expiry import DEFAULT_HITL_TTL_MINUTES, is_hitl_expired
from core.agent.hitl_store import InMemoryHitlStore
from core.channels.wechat.hitl_commands import (
    build_wechat_hitl_confirmation_message,
    build_wechat_hitl_pending_reminder,
    parse_hitl_command,
)


def test_parse_hitl_command_recognizes_confirm_and_reject() -> None:
    assert parse_hitl_command("确认") == "approve"
    assert parse_hitl_command("确认。") == "approve"
    assert parse_hitl_command("拒绝") == "reject"
    assert parse_hitl_command("取消") == "reject"
    assert parse_hitl_command("帮我找简历") is None


def test_build_wechat_hitl_confirmation_message_includes_sandbox_note() -> None:
    message = build_wechat_hitl_confirmation_message(
        tool_name="write_file",
        tool_arguments={"path": "notes/todo.txt", "content": "hi"},
        requires_sandbox=True,
    )
    assert "需要你确认" in message
    assert "写入文件" in message
    assert "隔离沙箱" in message
    assert "确认" in message
    assert "拒绝" in message


def test_build_wechat_hitl_pending_reminder() -> None:
    reminder = build_wechat_hitl_pending_reminder(tool_name="scheduled_tasks")
    assert "定时提醒" in reminder
    assert "确认" in reminder


def test_hitl_expires_after_ttl() -> None:
    store = InMemoryHitlStore()
    request = store.create_pending(
        run_id="run-expire",
        session_id="session-expire",
        tool_name="scheduled_tasks",
        reason="confirmation_required",
    )
    assert request.expires_at is not None
    request.created_at = datetime.now(UTC) - timedelta(minutes=DEFAULT_HITL_TTL_MINUTES, seconds=30)
    request.expires_at = request.created_at + timedelta(minutes=DEFAULT_HITL_TTL_MINUTES)
    assert is_hitl_expired(request) is True
    assert store.get_latest_pending_for_session(session_id="session-expire") is None
    assert store.get(hitl_id=request.hitl_id).status == "expired"


def test_hitl_approve_raises_when_expired() -> None:
    store = InMemoryHitlStore()
    request = store.create_pending(
        run_id="run-expire-2",
        session_id="session-expire-2",
        tool_name="scheduled_tasks",
        reason="confirmation_required",
    )
    request.created_at = datetime.now(UTC) - timedelta(minutes=DEFAULT_HITL_TTL_MINUTES, seconds=30)
    request.expires_at = request.created_at + timedelta(minutes=DEFAULT_HITL_TTL_MINUTES)
    assert is_hitl_expired(request) is True
    with pytest.raises(ValueError, match="expired"):
        store.approve(hitl_id=request.hitl_id)
