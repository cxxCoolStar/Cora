from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from core.clawbot.tools import ToolExecutionResult
    from core.tools import ToolInvocation


class ArchiveDomainHost(Protocol):
    async def _tool_save_file(self, invocation: "ToolInvocation") -> "ToolExecutionResult":
        ...

    async def _tool_save_content(self, invocation: "ToolInvocation") -> "ToolExecutionResult":
        ...

    def _tool_overview_knowledge_base(self, invocation: "ToolInvocation") -> "ToolExecutionResult":
        ...

    def _tool_list_topics(self, invocation: "ToolInvocation") -> "ToolExecutionResult":
        ...

    def _tool_open_topic(self, invocation: "ToolInvocation") -> "ToolExecutionResult":
        ...

    def _tool_read_item(self, invocation: "ToolInvocation") -> "ToolExecutionResult":
        ...

    def _tool_summarize_item(self, invocation: "ToolInvocation") -> "ToolExecutionResult":
        ...

    async def _tool_send_file_to_user(self, invocation: "ToolInvocation") -> "ToolExecutionResult":
        ...

    def _tool_delete_item(self, invocation: "ToolInvocation") -> "ToolExecutionResult":
        ...

    def _tool_clarify_reference(self, invocation: "ToolInvocation") -> "ToolExecutionResult":
        ...

    def _tool_clarify_capture_intent(self, invocation: "ToolInvocation") -> "ToolExecutionResult":
        ...

    async def _tool_resolve_pending(self, invocation: "ToolInvocation") -> "ToolExecutionResult":
        ...


@dataclass(slots=True)
class ArchiveCaptureHandler:
    host: ArchiveDomainHost

    async def execute(self, invocation: "ToolInvocation") -> "ToolExecutionResult":
        from core.clawbot.tools import ToolExecutionResult
        action = str(invocation.plan.arguments.get("action") or "").strip()
        if action == "save":
            if invocation.upload is not None:
                return await self.host._tool_save_file(invocation)
            return await self.host._tool_save_content(invocation)
        return ToolExecutionResult(reply="我暂时还不能处理这个 archive capture 动作。", action="chat")


@dataclass(slots=True)
class ArchiveRetrieveHandler:
    host: ArchiveDomainHost

    async def execute(self, invocation: "ToolInvocation") -> "ToolExecutionResult":
        from core.clawbot.tools import ToolExecutionResult
        action = str(invocation.plan.arguments.get("action") or "").strip()
        if action == "overview":
            return self.host._tool_overview_knowledge_base(invocation)
        if action == "list_topics":
            return self.host._tool_list_topics(invocation)
        if action == "open":
            return self.host._tool_open_topic(invocation)
        if action == "read":
            return self.host._tool_read_item(invocation)
        if action == "summarize":
            return self.host._tool_summarize_item(invocation)
        if action == "deliver":
            return await self.host._tool_send_file_to_user(invocation)
        if action == "delete":
            return self.host._tool_delete_item(invocation)
        return ToolExecutionResult(reply="我暂时还不能处理这个 archive retrieve 动作。", action="chat")


@dataclass(slots=True)
class ArchiveClarificationHandler:
    host: ArchiveDomainHost

    async def execute(self, invocation: "ToolInvocation") -> "ToolExecutionResult":
        from core.clawbot.tools import ToolExecutionResult
        action = str(invocation.plan.arguments.get("action") or "").strip()
        if action == "clarify_reference":
            return self.host._tool_clarify_reference(invocation)
        if action == "clarify_capture_intent":
            return self.host._tool_clarify_capture_intent(invocation)
        if action == "resolve_pending":
            return await self.host._tool_resolve_pending(invocation)
        from core.clawbot.tools import ToolExecutionResult
        return ToolExecutionResult(reply="我暂时还不能处理这个 archive_state 动作。", action="chat")


@dataclass(slots=True)
class ArchiveDomainHandler:
    host: ArchiveDomainHost
    capture: ArchiveCaptureHandler | None = None
    retrieve: ArchiveRetrieveHandler | None = None
    clarification: ArchiveClarificationHandler | None = None

    def __post_init__(self) -> None:
        self.capture = self.capture or ArchiveCaptureHandler(host=self.host)
        self.retrieve = self.retrieve or ArchiveRetrieveHandler(host=self.host)
        self.clarification = self.clarification or ArchiveClarificationHandler(host=self.host)

    async def execute_archive(self, invocation: "ToolInvocation") -> "ToolExecutionResult":
        action = str(invocation.plan.arguments.get("action") or "").strip()
        if action == "save":
            return await self.capture.execute(invocation)
        return await self.retrieve.execute(invocation)

    async def execute_archive_state(self, invocation: "ToolInvocation") -> "ToolExecutionResult":
        return await self.clarification.execute(invocation)
