from __future__ import annotations

from dataclasses import dataclass

import pytest

from core.clawbot.archive_domain import ArchiveDomainHandler
from core.clawbot.planner import ToolPlan
from core.clawbot.tools import ToolExecutionResult
from core.tools import ToolInvocation


@dataclass
class FakeArchiveHost:
    calls: list[str]

    async def _tool_save_file(self, invocation: ToolInvocation) -> ToolExecutionResult:
        self.calls.append("save_file")
        return ToolExecutionResult(reply="saved file", action="capture")

    async def _tool_save_content(self, invocation: ToolInvocation) -> ToolExecutionResult:
        self.calls.append("save_content")
        return ToolExecutionResult(reply="saved text", action="capture")

    def _tool_overview_knowledge_base(self, invocation: ToolInvocation) -> ToolExecutionResult:
        self.calls.append("overview")
        return ToolExecutionResult(reply="overview", action="retrieve")

    def _tool_list_topics(self, invocation: ToolInvocation) -> ToolExecutionResult:
        self.calls.append("list_topics")
        return ToolExecutionResult(reply="topics", action="retrieve")

    def _tool_open_topic(self, invocation: ToolInvocation) -> ToolExecutionResult:
        self.calls.append("open")
        return ToolExecutionResult(reply="open", action="retrieve")

    def _tool_read_item(self, invocation: ToolInvocation) -> ToolExecutionResult:
        self.calls.append("read")
        return ToolExecutionResult(reply="read", action="retrieve")

    def _tool_summarize_item(self, invocation: ToolInvocation) -> ToolExecutionResult:
        self.calls.append("summarize")
        return ToolExecutionResult(reply="summarize", action="organize")

    async def _tool_send_file_to_user(self, invocation: ToolInvocation) -> ToolExecutionResult:
        self.calls.append("deliver")
        return ToolExecutionResult(reply="deliver", action="retrieve")

    def _tool_delete_item(self, invocation: ToolInvocation) -> ToolExecutionResult:
        self.calls.append("delete")
        return ToolExecutionResult(reply="delete", action="delete")

    def _tool_clarify_reference(self, invocation: ToolInvocation) -> ToolExecutionResult:
        self.calls.append("clarify_reference")
        return ToolExecutionResult(reply="clarify ref", action="clarify")

    def _tool_clarify_capture_intent(self, invocation: ToolInvocation) -> ToolExecutionResult:
        self.calls.append("clarify_capture_intent")
        return ToolExecutionResult(reply="clarify capture", action="clarify")

    async def _tool_resolve_pending(self, invocation: ToolInvocation) -> ToolExecutionResult:
        self.calls.append("resolve_pending")
        return ToolExecutionResult(reply="resolve", action="capture")


def _invocation(tool: str, action: str) -> ToolInvocation:
    return ToolInvocation(
        session_id="session-1",
        source_message_id="msg-1",
        plan=ToolPlan(tool=tool, arguments={"action": action}, reason="test"),
        text=None,
        upload=None,
        context={},
    )


@pytest.mark.anyio
async def test_archive_domain_routes_capture_and_retrieve_actions() -> None:
    host = FakeArchiveHost(calls=[])
    handler = ArchiveDomainHandler(host=host)

    await handler.execute_archive(_invocation("archive", "save"))
    await handler.execute_archive(_invocation("archive", "open"))
    await handler.execute_archive(_invocation("archive", "delete"))

    assert host.calls == ["save_content", "open", "delete"]


@pytest.mark.anyio
async def test_archive_domain_routes_archive_state_actions() -> None:
    host = FakeArchiveHost(calls=[])
    handler = ArchiveDomainHandler(host=host)

    await handler.execute_archive_state(_invocation("archive_state", "clarify_reference"))
    await handler.execute_archive_state(_invocation("archive_state", "resolve_pending"))

    assert host.calls == ["clarify_reference", "resolve_pending"]
