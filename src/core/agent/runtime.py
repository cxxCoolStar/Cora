from __future__ import annotations

from dataclasses import dataclass

from core.channels.types import Channel, EventType
from core.llm.base import ModelClient
from core.memory.history import HistoryMemory
from core.memory.summary import SummaryMemoryManager
from core.schemas.message import Message
from core.schemas.model import ModelResponse
from core.storage.sqlite.repositories import EventRepository, MemoryRepository, MessageRepository, SessionRepository
from core.tools.registry import ToolRegistry


@dataclass(slots=True)
class TurnResult:
    session_id: str
    response_text: str
    tool_calls: int


class AgentRuntime:
    def __init__(
        self,
        *,
        settings,
        model_client: ModelClient,
        tool_registry: ToolRegistry,
        session_repository: SessionRepository,
        message_repository: MessageRepository,
        memory_repository: MemoryRepository,
        event_repository: EventRepository,
        history_memory: HistoryMemory,
        summary_memory: SummaryMemoryManager,
        system_prompt: str,
    ) -> None:
        self.settings = settings
        self.model_client = model_client
        self.tool_registry = tool_registry
        self.session_repository = session_repository
        self.message_repository = message_repository
        self.memory_repository = memory_repository
        self.event_repository = event_repository
        self.history_memory = history_memory
        self.summary_memory = summary_memory
        self.system_prompt = system_prompt

    def run_turn(self, *, session_id: str, user_input: str) -> TurnResult:
        self.session_repository.get(session_id)
        self.event_repository.append(
            session_id=session_id,
            event_type=EventType.TURN_STARTED,
            channel=Channel.EVENT,
            payload={"user_input": user_input},
        )
        self.message_repository.append(
            Message.user(session_id=session_id, content=user_input, channel=Channel.CHAT)
        )

        total_tool_calls = 0
        final_text = ""

        for _ in range(self.settings.max_tool_rounds + 1):
            model_response = self._call_model(session_id=session_id)
            if not model_response.tool_calls:
                final_text = model_response.assistant_text or ""
                self.message_repository.append(
                    Message.assistant(session_id=session_id, content=final_text, channel=Channel.CHAT)
                )
                self.event_repository.append(
                    session_id=session_id,
                    event_type=EventType.TURN_COMPLETED,
                    channel=Channel.EVENT,
                    payload={"tool_calls": total_tool_calls},
                )
                self.summary_memory.maybe_refresh_summary(session_id=session_id)
                return TurnResult(
                    session_id=session_id,
                    response_text=final_text,
                    tool_calls=total_tool_calls,
                )

            for tool_call in model_response.tool_calls:
                total_tool_calls += 1
                if total_tool_calls > self.settings.max_total_tool_calls:
                    raise RuntimeError("Exceeded maximum tool calls for a single turn.")
                self.event_repository.append(
                    session_id=session_id,
                    event_type=EventType.TOOL_REQUESTED,
                    channel=Channel.TOOL,
                    payload={"tool_call_id": tool_call.id, "tool_name": tool_call.tool_name},
                )
                tool_result = self.tool_registry.execute(tool_call)
                self.message_repository.append(
                    Message.tool(
                        session_id=session_id,
                        content=tool_result.content,
                        name=tool_call.tool_name,
                        tool_call_id=tool_call.id,
                        channel=Channel.TOOL,
                        metadata={"success": tool_result.success, "error": tool_result.error},
                    )
                )
                self.event_repository.append(
                    session_id=session_id,
                    event_type=EventType.TOOL_COMPLETED,
                    channel=Channel.TOOL,
                    payload={
                        "tool_call_id": tool_call.id,
                        "tool_name": tool_call.tool_name,
                        "success": tool_result.success,
                    },
                )

        raise RuntimeError("Agent runtime exceeded the configured tool round limit.")

    def _call_model(self, *, session_id: str) -> ModelResponse:
        summary = self.summary_memory.get_summary(session_id=session_id)
        messages = [Message.system(session_id=session_id, content=self.system_prompt, channel=Channel.SYSTEM)]
        if summary:
            messages.append(
                Message.system(
                    session_id=session_id,
                    content=f"Conversation summary:\n{summary}",
                    channel=Channel.MEMORY,
                )
            )
        messages.extend(self.history_memory.load(session_id=session_id))
        self.event_repository.append(
            session_id=session_id,
            event_type=EventType.MODEL_CALLED,
            channel=Channel.EVENT,
            payload={"message_count": len(messages)},
        )
        return self.model_client.generate(messages=messages, tools=self.tool_registry.list_specs())
