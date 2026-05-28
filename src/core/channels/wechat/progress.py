from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, AsyncIterator

from core.channels.wechat.hitl_commands import parse_hitl_command
from core.channels.wechat.session_router import WechatSessionRouter
from core.channels.wechat.types import WechatInboundEvent

if TYPE_CHECKING:
    from core.channels.wechat.ilink_client import WechatIlinkClient

logger = logging.getLogger(__name__)

ACK_MESSAGE = "收到，正在处理你的请求…"
HEARTBEAT_MESSAGE = "还在处理中，请稍等…"
TOOL_DONE_CAPTURE_MESSAGE = "已写入资料库，正在整理回复…"

_TOOL_START_MESSAGES: dict[str, str] = {
    "skill_run": "正在归档处理…",
    "archive_run": "正在查询/更新资料库…",
    "skill_view": "正在加载技能说明…",
}

_active_session: ContextVar["WechatProgressSession | None"] = ContextVar(
    "wechat_progress_session",
    default=None,
)


@dataclass(slots=True)
class WechatProgressSettings:
    enabled: bool = True
    heartbeat_seconds: float = 90.0
    tool_updates: bool = True
    min_update_interval_seconds: float = 12.0


def get_active_wechat_progress() -> "WechatProgressSession | None":
    return _active_session.get()


def should_send_progress_ack(event: WechatInboundEvent) -> bool:
    if parse_hitl_command(event.text) is not None:
        return False
    if event.file_path and event.file_name:
        return True
    text = str(event.text or "").strip()
    if not text:
        return bool(event.file_path)
    match = WechatSessionRouter.MANUAL_RESET_PATTERN.match(text)
    if match is not None and not str(match.group("rest") or "").strip():
        return False
    return len(text) >= 8


@dataclass(slots=True)
class WechatProgressSession:
    event: WechatInboundEvent
    client: "WechatIlinkClient"
    settings: WechatProgressSettings
    _last_notify_monotonic: float = 0.0
    _heartbeat_task: asyncio.Task[None] | None = None

    async def send_ack_if_needed(self) -> None:
        if not self.settings.enabled or not should_send_progress_ack(self.event):
            return
        await self._send_text(ACK_MESSAGE, bypass_throttle=True, kind="ack")

    async def start_heartbeat(self) -> None:
        if not self.settings.enabled or self.settings.heartbeat_seconds <= 0:
            return
        if not should_send_progress_ack(self.event):
            return
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def stop_heartbeat(self) -> None:
        task = self._heartbeat_task
        self._heartbeat_task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def on_tool_start(self, tool_name: str) -> None:
        if not self.settings.enabled or not self.settings.tool_updates:
            return
        message = _TOOL_START_MESSAGES.get(tool_name)
        if not message:
            return
        await self._send_text(message, kind=f"tool_start:{tool_name}")

    async def on_tool_done(self, tool_name: str, *, action: str, status: str) -> None:
        if not self.settings.enabled or not self.settings.tool_updates:
            return
        if status != "completed":
            return
        if tool_name in {"skill_run", "archive_run"} and action in {"capture", "tool_completed"}:
            await self._send_text(TOOL_DONE_CAPTURE_MESSAGE, kind=f"tool_done:{tool_name}")

    async def _heartbeat_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.settings.heartbeat_seconds)
                await self._send_text(HEARTBEAT_MESSAGE, kind="heartbeat")
        except asyncio.CancelledError:
            raise

    async def _send_text(self, text: str, *, bypass_throttle: bool = False, kind: str = "update") -> None:
        if not text.strip():
            return
        now = time.monotonic()
        if (
            not bypass_throttle
            and self._last_notify_monotonic > 0
            and (now - self._last_notify_monotonic) < self.settings.min_update_interval_seconds
        ):
            logger.debug("wechat progress throttled kind=%s", kind)
            return
        self._last_notify_monotonic = now
        try:
            result = await self.client.send_text(
                peer_user_id=self.event.user_id,
                text=text,
                context_token=self.event.context_token,
            )
            logger.info(
                "wechat progress sent kind=%s user=%s event=%s ret=%s errcode=%s",
                kind,
                self.event.user_id,
                self.event.event_id,
                result.get("ret") if isinstance(result, dict) else None,
                result.get("errcode") if isinstance(result, dict) else None,
            )
        except Exception:
            logger.exception(
                "wechat progress send failed kind=%s user=%s event=%s",
                kind,
                self.event.user_id,
                self.event.event_id,
            )


@asynccontextmanager
async def wechat_progress_scope(
    *,
    event: WechatInboundEvent,
    client: "WechatIlinkClient",
    settings: WechatProgressSettings,
) -> AsyncIterator[WechatProgressSession | None]:
    if not settings.enabled:
        yield None
        return
    session = WechatProgressSession(event=event, client=client, settings=settings)
    token = _active_session.set(session)
    await session.send_ack_if_needed()
    await session.start_heartbeat()
    try:
        yield session
    finally:
        await session.stop_heartbeat()
        _active_session.reset(token)


def progress_settings_from_core(settings: Any) -> WechatProgressSettings:
    return WechatProgressSettings(
        enabled=bool(getattr(settings, "wechat_progress_enabled", True)),
        heartbeat_seconds=float(getattr(settings, "wechat_progress_heartbeat_seconds", 90.0)),
        tool_updates=bool(getattr(settings, "wechat_progress_tool_updates", True)),
        min_update_interval_seconds=float(
            getattr(settings, "wechat_progress_min_interval_seconds", 12.0)
        ),
    )


__all__ = [
    "ACK_MESSAGE",
    "HEARTBEAT_MESSAGE",
    "TOOL_DONE_CAPTURE_MESSAGE",
    "WechatProgressSession",
    "WechatProgressSettings",
    "get_active_wechat_progress",
    "progress_settings_from_core",
    "should_send_progress_ack",
    "wechat_progress_scope",
]
