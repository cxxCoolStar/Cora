from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from core.agent.loop import AgentLoop
from core.channels.wechat import progress as progress_module
from core.channels.wechat.poller import WechatPoller
from core.channels.wechat.progress import (
    HEARTBEAT_MESSAGE,
    TOOL_DONE_CAPTURE_MESSAGE,
    WechatProgressSettings,
    WechatProgressStage,
    infer_progress_route,
    should_send_progress_ack,
)
from core.channels.wechat.types import WechatHandleResult, WechatInboundEvent
from core.clawbot.tools import RuntimeToolExecutor
from core.schemas.execution import ExecutionHints
from core.schemas.message import Message
from core.schemas.model import ModelResponse
from core.schemas.tool import ToolCall, ToolResult
from core.tools.registry import ToolSpec


def test_infer_progress_route_save_and_find() -> None:
    assert infer_progress_route(
        WechatInboundEvent(event_id="1", user_id="u", text="帮我记录今天的饮食")
    ) == "save"
    assert infer_progress_route(
        WechatInboundEvent(event_id="2", user_id="u", text="我每日饮食是什么")
    ) == "find"
    assert infer_progress_route(
        WechatInboundEvent(
            event_id="3",
            user_id="u",
            text="",
            file_name="a.pdf",
            file_path="/tmp/a.pdf",
        )
    ) == "save"


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


def test_poller_sends_routed_ack_before_final_reply() -> None:
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
    assert client.sent[0] == "收到，正在帮你记入资料库…"
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
        text="这是一条需要等待的处理请求内容",
    )
    asyncio.run(poller._process_event(event))
    assert "收到" in client.sent[0]
    assert HEARTBEAT_MESSAGE in client.sent or any("请稍等" in line for line in client.sent)


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


def test_agent_loop_emits_llm_compose_after_tools() -> None:
    class _FakeClient:
        def __init__(self) -> None:
            self.sent: list[str] = []

        async def send_text(self, *, peer_user_id: str, text: str, context_token: str | None = None):
            self.sent.append(text)
            return {"ret": 0}

    @dataclass
    class _FakeModel:
        calls: int = 0

        def generate(self, *, messages, tools, response_format=None):
            self.calls += 1
            if self.calls == 1:
                return ModelResponse(
                    assistant_text="",
                    tool_calls=[
                        ToolCall(id="tc-1", tool_name="search_files", arguments={"query": "x", "path": "."}),
                    ],
                    usage={},
                )
            return ModelResponse(assistant_text="这是最终回复", tool_calls=[], usage={})

    class _FakeExecutor:
        async def execute_tool_call(self, *, session_id, tool_call, runtime):
            return ToolResult(
                success=True,
                content="ok",
                status="completed",
                disposition="continue",
                action="tool_completed",
                metadata={},
                hints=ExecutionHints(),
            )

    event = WechatInboundEvent(event_id="evt-5", user_id="wx-user", text="帮我找一下之前的配置")
    session = progress_module.WechatProgressSession(
        event=event,
        client=_FakeClient(),
        settings=WechatProgressSettings(
            enabled=True,
            heartbeat_seconds=0,
            tool_updates=False,
            min_update_interval_seconds=0.0,
        ),
    )
    token = progress_module._active_session.set(session)

    async def _run() -> None:
        loop = AgentLoop(
            model_client=_FakeModel(),
            tool_executor=_FakeExecutor(),
            tool_specs=[
                ToolSpec(
                    name="search_files",
                    toolset="files",
                    description="search",
                    schema={"type": "object", "properties": {}},
                    handler=lambda *a, **k: None,
                )
            ],
            max_steps=3,
        )
        await loop.run(
            session_id="s1",
            initial_messages=[Message.user(session_id="s1", content="找配置")],
            runtime=__import__(
                "core.agent.runtime_state",
                fromlist=["ConversationRuntimeState"],
            ).ConversationRuntimeState(session_id="s1"),
        )

    try:
        asyncio.run(_run())
        assert any("正在整理回复" in line for line in session.client.sent)
    finally:
        progress_module._active_session.reset(token)


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
