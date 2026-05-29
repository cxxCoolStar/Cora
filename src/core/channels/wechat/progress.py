from __future__ import annotations

import asyncio
import logging
import re
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, AsyncIterator

from core.channels.wechat.hitl_commands import parse_hitl_command
from core.channels.wechat.session_router import WechatSessionRouter
from core.channels.wechat.types import WechatInboundEvent

if TYPE_CHECKING:
    from core.channels.wechat.ilink_client import WechatIlinkClient

logger = logging.getLogger(__name__)


class WechatProgressStage(StrEnum):
    ACK_SAVE = "ack_save"
    ACK_FIND = "ack_find"
    ACK_DEFAULT = "ack_default"
    INGEST_STORE = "ingest_store"
    TOPIC_CLASSIFY = "topic_classify"
    TOOL_ARCHIVE = "tool_archive"
    TOOL_FIND = "tool_find"
    AFTER_CAPTURE = "after_capture"
    LLM_COMPOSE = "llm_compose"
    HEARTBEAT = "heartbeat"
    FAILED = "failed"


_STAGE_MESSAGES: dict[WechatProgressStage, str] = {
    WechatProgressStage.ACK_SAVE: "收到，正在帮你记入资料库…",
    WechatProgressStage.ACK_FIND: "收到，正在资料库里查找…",
    WechatProgressStage.ACK_DEFAULT: "收到，开始处理…",
    WechatProgressStage.INGEST_STORE: "已保存，正在打标签和归类…",
    WechatProgressStage.TOPIC_CLASSIFY: "正在分析主题（约 30 秒）…",
    WechatProgressStage.TOOL_ARCHIVE: "正在归档处理…",
    WechatProgressStage.TOOL_FIND: "正在资料库里检索…",
    WechatProgressStage.AFTER_CAPTURE: "已写入资料库，正在整理回复…",
    WechatProgressStage.LLM_COMPOSE: "正在整理回复（约 1–2 分钟）…",
    WechatProgressStage.HEARTBEAT: "还在处理中，请稍等…",
    WechatProgressStage.FAILED: "处理遇到问题，请稍后再试。",
}

_HEARTBEAT_BY_STAGE: dict[WechatProgressStage, str] = {
    WechatProgressStage.TOPIC_CLASSIFY: "仍在分析主题，请稍等…",
    WechatProgressStage.LLM_COMPOSE: "仍在整理回复，请稍等…",
    WechatProgressStage.TOOL_ARCHIVE: "仍在归档，请稍等…",
    WechatProgressStage.TOOL_FIND: "仍在检索，请稍等…",
}

_STAGE_PRIORITY: dict[WechatProgressStage, int] = {
    WechatProgressStage.ACK_SAVE: 10,
    WechatProgressStage.ACK_FIND: 10,
    WechatProgressStage.ACK_DEFAULT: 10,
    WechatProgressStage.TOOL_ARCHIVE: 20,
    WechatProgressStage.TOOL_FIND: 20,
    WechatProgressStage.INGEST_STORE: 30,
    WechatProgressStage.AFTER_CAPTURE: 35,
    WechatProgressStage.TOPIC_CLASSIFY: 40,
    WechatProgressStage.LLM_COMPOSE: 50,
    WechatProgressStage.HEARTBEAT: 5,
    WechatProgressStage.FAILED: 100,
}

_SAVE_HINT = re.compile(
    r"(记录|保存|归档|记下|存档|上传|存入)",
    re.IGNORECASE,
)
_FIND_HINT = re.compile(
    r"(找|查找|检索|搜索|发我|发给我|是什么|哪些|之前|刚才|上次|有没有)",
    re.IGNORECASE,
)

# Backward-compatible exports for tests/docs
ACK_MESSAGE = _STAGE_MESSAGES[WechatProgressStage.ACK_DEFAULT]
HEARTBEAT_MESSAGE = _STAGE_MESSAGES[WechatProgressStage.HEARTBEAT]
TOOL_DONE_CAPTURE_MESSAGE = _STAGE_MESSAGES[WechatProgressStage.AFTER_CAPTURE]

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
    max_messages: int = 5


def get_active_wechat_progress() -> "WechatProgressSession | None":
    return _active_session.get()


def infer_progress_route(event: WechatInboundEvent) -> str:
    if event.file_path and event.file_name:
        return "save"
    text = str(event.text or "").strip()
    if not text:
        return "default"
    lowered = text.lower()
    if "archive_run" in lowered and "save" in lowered:
        return "save"
    if "archive_run" in lowered and "search" in lowered:
        return "find"
    if _SAVE_HINT.search(text):
        return "save"
    if _FIND_HINT.search(text):
        return "find"
    return "default"


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


def _ack_stage_for_route(route: str) -> WechatProgressStage:
    if route == "save":
        return WechatProgressStage.ACK_SAVE
    if route == "find":
        return WechatProgressStage.ACK_FIND
    return WechatProgressStage.ACK_DEFAULT


async def notify_wechat_llm_compose() -> None:
    session = get_active_wechat_progress()
    if session is None:
        return
    await session.report_stage(WechatProgressStage.LLM_COMPOSE)


def schedule_wechat_progress_stage(stage: WechatProgressStage | str) -> None:
    session = get_active_wechat_progress()
    if session is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    resolved = WechatProgressStage(stage) if isinstance(stage, str) else stage
    loop.create_task(session.report_stage(resolved))


@dataclass(slots=True)
class WechatProgressSession:
    event: WechatInboundEvent
    client: "WechatIlinkClient"
    settings: WechatProgressSettings
    _route: str = field(default="default")
    _current_stage: WechatProgressStage | None = None
    _sent_count: int = 0
    _last_notify_monotonic: float = 0.0
    _heartbeat_task: asyncio.Task[None] | None = None

    async def send_ack_if_needed(self) -> None:
        if not self.settings.enabled or not should_send_progress_ack(self.event):
            return
        self._route = infer_progress_route(self.event)
        await self.report_stage(
            _ack_stage_for_route(self._route),
            bypass_throttle=True,
            bypass_max=True,
        )

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

    async def report_stage(
        self,
        stage: WechatProgressStage,
        *,
        bypass_throttle: bool = False,
        bypass_max: bool = False,
        force: bool = False,
    ) -> None:
        if not self.settings.enabled:
            return
        if (
            not force
            and self._current_stage == stage
            and stage
            not in {
                WechatProgressStage.HEARTBEAT,
            }
        ):
            return
        if (
            not bypass_max
            and self._sent_count >= self.settings.max_messages
            and stage
            not in {
                WechatProgressStage.FAILED,
            }
        ):
            logger.debug("wechat progress max_messages reached stage=%s", stage)
            return
        current_priority = _STAGE_PRIORITY.get(self._current_stage or stage, 0)
        new_priority = _STAGE_PRIORITY.get(stage, 0)
        if (
            not force
            and self._current_stage is not None
            and new_priority < current_priority
            and stage != WechatProgressStage.HEARTBEAT
        ):
            return
        forward_stage = self._current_stage is None or new_priority > current_priority
        text = _STAGE_MESSAGES.get(stage, "")
        if not text:
            return
        sent = await self._send_text(
            text,
            bypass_throttle=bypass_throttle or forward_stage or stage.value.startswith("ack_"),
            kind=stage.value,
        )
        if sent:
            self._current_stage = stage
            if stage != WechatProgressStage.HEARTBEAT:
                self._sent_count += 1

    async def on_tool_start(self, tool_name: str) -> None:
        if not self.settings.tool_updates:
            return
        if tool_name in {"skill_run", "skill_view"}:
            await self.report_stage(WechatProgressStage.TOOL_ARCHIVE)
        elif tool_name == "archive_run":
            stage = (
                WechatProgressStage.TOOL_FIND
                if self._route == "find"
                else WechatProgressStage.TOOL_ARCHIVE
            )
            await self.report_stage(stage)

    async def on_tool_done(self, tool_name: str, *, action: str, status: str) -> None:
        if not self.settings.tool_updates:
            return
        if status != "completed":
            return
        if tool_name in {"skill_run", "archive_run"} and action in {"capture", "tool_completed"}:
            await self.report_stage(WechatProgressStage.AFTER_CAPTURE)

    async def report_failed(self, *, detail: str | None = None) -> None:
        message = _STAGE_MESSAGES[WechatProgressStage.FAILED]
        if detail and detail.strip():
            message = f"{message}\n{detail.strip()}"
        await self.report_stage(
            WechatProgressStage.FAILED,
            bypass_throttle=True,
            bypass_max=True,
            force=True,
        )

    async def _heartbeat_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.settings.heartbeat_seconds)
                stage = self._current_stage or WechatProgressStage.ACK_DEFAULT
                text = _HEARTBEAT_BY_STAGE.get(stage, _STAGE_MESSAGES[WechatProgressStage.HEARTBEAT])
                await self._send_text(text, kind="heartbeat")
        except asyncio.CancelledError:
            raise

    async def _send_text(
        self,
        text: str,
        *,
        bypass_throttle: bool = False,
        kind: str = "update",
    ) -> bool:
        if not text.strip():
            return False
        now = time.monotonic()
        if (
            not bypass_throttle
            and self._last_notify_monotonic > 0
            and (now - self._last_notify_monotonic) < self.settings.min_update_interval_seconds
        ):
            logger.debug("wechat progress throttled kind=%s", kind)
            return False
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
            return True
        except Exception:
            logger.exception(
                "wechat progress send failed kind=%s user=%s event=%s",
                kind,
                self.event.user_id,
                self.event.event_id,
            )
            return False


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
        max_messages=int(getattr(settings, "wechat_progress_max_messages", 5)),
    )


__all__ = [
    "ACK_MESSAGE",
    "HEARTBEAT_MESSAGE",
    "TOOL_DONE_CAPTURE_MESSAGE",
    "WechatProgressSession",
    "WechatProgressSettings",
    "WechatProgressStage",
    "get_active_wechat_progress",
    "infer_progress_route",
    "notify_wechat_llm_compose",
    "progress_settings_from_core",
    "schedule_wechat_progress_stage",
    "should_send_progress_ack",
    "wechat_progress_scope",
]
