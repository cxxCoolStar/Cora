from __future__ import annotations

import re
from typing import Any, Literal

HitlCommand = Literal["approve", "reject"]


APPROVE_PATTERN = re.compile(r"^\s*(?:确认|同意|执行)\s*(?:[。.!！]?)?\s*$", re.IGNORECASE)
REJECT_PATTERN = re.compile(r"^\s*(?:拒绝|取消|不要)\s*(?:[。.!！]?)?\s*$", re.IGNORECASE)

TOOL_DISPLAY_NAMES: dict[str, str] = {
    "scheduled_tasks": "定时提醒",
    "write_file": "写入文件",
    "shell_exec": "运行命令",
    "web_search": "联网搜索",
    "web_fetch": "打开网页",
}


def parse_hitl_command(text: str | None) -> HitlCommand | None:
    normalized = str(text or "").strip()
    if not normalized:
        return None
    if APPROVE_PATTERN.match(normalized):
        return "approve"
    if REJECT_PATTERN.match(normalized):
        return "reject"
    return None


def tool_display_name(tool_name: str) -> str:
    name = str(tool_name or "").strip()
    return TOOL_DISPLAY_NAMES.get(name, name or "工具操作")


def summarize_tool_arguments(tool_name: str, arguments: dict[str, Any] | None) -> str:
    payload = dict(arguments or {})
    if not payload:
        return "无额外参数"
    if tool_name == "scheduled_tasks":
        action = str(payload.get("action") or "").strip()
        if action == "list":
            return "查看当前提醒列表"
        if action == "create":
            return "创建新的提醒"
        if action in {"update", "delete", "pause", "resume"}:
            return f"对提醒执行操作：{action}"
    if tool_name == "write_file":
        path = str(payload.get("path") or "").strip()
        return f"写入文件：{path or '（未指定路径）'}"
    if tool_name == "shell_exec":
        command = str(payload.get("command") or "").strip()
        preview = command[:80] + ("…" if len(command) > 80 else "")
        return f"运行命令：{preview or '（未指定命令）'}"
    return "参数：" + ", ".join(f"{key}={value}" for key, value in payload.items())


def build_wechat_hitl_confirmation_message(
    *,
    tool_name: str,
    tool_arguments: dict[str, Any] | None = None,
    requires_sandbox: bool = False,
) -> str:
    display = tool_display_name(tool_name)
    detail = summarize_tool_arguments(tool_name, tool_arguments)
    sandbox_line = "· 环境：仅在隔离沙箱中执行，不会改动你资料库里的文件\n" if requires_sandbox else ""
    return (
        "需要你确认后我才会继续：\n\n"
        f"· 操作：{display}（{tool_name}）\n"
        f"· 说明：{detail}\n"
        f"{sandbox_line}\n"
        "请直接回复：\n"
        "  确认 — 执行\n"
        "  拒绝 — 取消\n\n"
        "（约 10 分钟内有效；超时请重新发起请求）"
    )


def build_wechat_hitl_pending_reminder(*, tool_name: str) -> str:
    display = tool_display_name(tool_name)
    return (
        f"你还有一项待确认操作（{display}）。\n"
        "请先回复「确认」或「拒绝」，再发送其他请求。"
    )


def build_wechat_hitl_no_pending_message() -> str:
    return "当前没有待确认的操作。"


def build_wechat_hitl_expired_message(*, tool_name: str) -> str:
    display = tool_display_name(tool_name)
    return (
        f"待确认操作「{display}」已超时（约 10 分钟）。\n"
        "未执行该操作。如需继续，请重新发起一次请求。"
    )


def build_wechat_hitl_rejected_message(*, tool_name: str) -> str:
    display = tool_display_name(tool_name)
    return f"已取消，未执行「{display}」。"


def build_wechat_hitl_approved_prefix(*, tool_name: str) -> str:
    display = tool_display_name(tool_name)
    return f"已按你的确认执行「{display}」。\n\n"


__all__ = [
    "HitlCommand",
    "build_wechat_hitl_approved_prefix",
    "build_wechat_hitl_confirmation_message",
    "build_wechat_hitl_expired_message",
    "build_wechat_hitl_no_pending_message",
    "build_wechat_hitl_pending_reminder",
    "build_wechat_hitl_rejected_message",
    "parse_hitl_command",
    "tool_display_name",
]
