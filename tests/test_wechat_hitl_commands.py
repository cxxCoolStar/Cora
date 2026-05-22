from __future__ import annotations

import pytest

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
