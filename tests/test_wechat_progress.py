from __future__ import annotations

import asyncio

from core.channels.wechat import progress as progress_module
from core.channels.wechat.poller import WechatPoller
from core.channels.wechat.progress import (
    ACK_MESSAGE,
    HEARTBEAT_MESSAGE,
    TOOL_DONE_CAPTURE_MESSAGE,
    WechatProgressSettings,
    should_send_progress_ack,
)
from core.channels.wechat.types import WechatHandleResult, WechatInboundEvent
from core.clawbot.tools import RuntimeToolExecutor


def test_should_send_progress_ack_skips_hitl_and_reset_only() -> None:
    assert should_send_progress_ack(
        WechatInboundEvent(event_id="1", user_id="u", text="确认")
    ) is False
    assert should_send_progress_ack(
        WechatInboundEvent(event_id="2", user_id="u", text="/new")
    ) is False
    assert should_send_progress_ack(
        WechatInboundEvent(
            event_id="3",
            user_id="u",
            text="你帮我记录一下今天的饮食",
        )
    ) is True
    assert should_send_progress_ack(
        WechatInboundEvent(
            event_id="4",
            user_id="u",
            text="",
            file_name="a.pdf",
            file_path="/tmp/a.pdf",
        )
    ) is True


def test_poller_sends_ack_before_final_reply() -> None:
    class _FakeClient:
        def __init__(self) -> None:
            self.sent: list[str] = []

        async def send_text(self, *, peer_user_id: str, text: str, context_token: str | None = None):
            self.sent.append(text)
            return {"ret": 0, "errcode": 0}

    class _SlowGateway:
        async def handle_inbound_event(self, *, event: WechatInboundEvent):
            await asyncio.sleep(0.05)
            return WechatHandleResult(
                deduplicated=False,
                session_id="session-1",
                reply="最终结果",
                action="capture",
            )

    client = _FakeClient()
    poller = WechatPoller(
        client=client,
        gateway_service=_SlowGateway(),
        progress_settings=WechatProgressSettings(
            enabled=True,
            heartbeat_seconds=0,
            tool_updates=False,
        ),
    )
    event = WechatInboundEvent(
        event_id="evt-1",
        user_id="wx-user",
        text="请帮我记录今天的饮食清单",
    )
    asyncio.run(poller._process_event(event))
    assert len(client.sent) >= 2
    assert client.sent[0] == ACK_MESSAGE
    assert client.sent[-1] == "最终结果"


def test_poller_heartbeat_while_processing() -> None:
    class _FakeClient:
        def __init__(self) -> None:
            self.sent: list[str] = []

        async def send_text(self, *, peer_user_id: str, text: str, context_token: str | None = None):
            self.sent.append(text)
            return {"ret": 0, "errcode": 0}

    class _SlowGateway:
        async def handle_inbound_event(self, *, event: WechatInboundEvent):
            await asyncio.sleep(0.15)
            return WechatHandleResult(
                deduplicated=False,
                session_id="session-1",
                reply="done",
                action="chat",
            )

    client = _FakeClient()
    poller = WechatPoller(
        client=client,
        gateway_service=_SlowGateway(),
        progress_settings=WechatProgressSettings(
            enabled=True,
            heartbeat_seconds=0.05,
            tool_updates=False,
            min_update_interval_seconds=0.0,
        ),
    )
    event = WechatInboundEvent(
        event_id="evt-2",
        user_id="wx-user",
        text="这是一条需要等待的处理请求",
    )
    asyncio.run(poller._process_event(event))
    assert ACK_MESSAGE in client.sent
    assert HEARTBEAT_MESSAGE in client.sent


def test_progress_session_tool_messages() -> None:
    class _FakeClient:
        def __init__(self) -> None:
            self.sent: list[str] = []

        async def send_text(self, *, peer_user_id: str, text: str, context_token: str | None = None):
            self.sent.append(text)
            return {"ret": 0}

    event = WechatInboundEvent(event_id="evt-3", user_id="wx-user", text="记录饮食")
    session = progress_module.WechatProgressSession(
        event=event,
        client=_FakeClient(),
        settings=WechatProgressSettings(
            enabled=True,
            heartbeat_seconds=0,
            tool_updates=True,
            min_update_interval_seconds=0.0,
        ),
    )
    async def _run() -> None:
        await session.on_tool_start("skill_run")
        await session.on_tool_done("skill_run", action="capture", status="completed")

    asyncio.run(_run())
    assert session.client.sent[0] == "正在归档处理…"
    assert TOOL_DONE_CAPTURE_MESSAGE in session.client.sent


def test_runtime_tool_executor_reads_active_progress_session() -> None:
    event = WechatInboundEvent(event_id="evt-4", user_id="wx-user", text="hello world test")
    session = progress_module.WechatProgressSession(
        event=event,
        client=object(),  # type: ignore[arg-type]
        settings=WechatProgressSettings(enabled=True, heartbeat_seconds=0, tool_updates=False),
    )
    token = progress_module._active_session.set(session)
    try:
        executor = RuntimeToolExecutor(
            ingestion_service=object(),  # type: ignore[arg-type]
            item_repository=object(),  # type: ignore[arg-type]
            pending_state_repository=object(),  # type: ignore[arg-type]
            channel_name="wechat",
        )
        assert executor._wechat_progress_session() is session
        executor_cli = RuntimeToolExecutor(
            ingestion_service=object(),  # type: ignore[arg-type]
            item_repository=object(),  # type: ignore[arg-type]
            pending_state_repository=object(),  # type: ignore[arg-type]
            channel_name="cli",
        )
        assert executor_cli._wechat_progress_session() is None
    finally:
        progress_module._active_session.reset(token)
