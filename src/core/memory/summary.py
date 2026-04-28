from __future__ import annotations

from core.storage.sqlite.repositories import MemoryRepository, MessageRepository


class SummaryMemoryManager:
    def __init__(
        self,
        *,
        message_repository: MessageRepository,
        memory_repository: MemoryRepository,
        summary_trigger_messages: int,
    ) -> None:
        self.message_repository = message_repository
        self.memory_repository = memory_repository
        self.summary_trigger_messages = summary_trigger_messages

    def get_summary(self, *, session_id: str) -> str | None:
        memory = self.memory_repository.get_latest_by_type(session_id=session_id, memory_type="summary")
        return memory.content if memory else None

    def maybe_refresh_summary(self, *, session_id: str) -> None:
        messages = self.message_repository.list_for_session(session_id=session_id, limit=None)
        if len(messages) < self.summary_trigger_messages:
            return
        summary_lines = []
        for message in messages[-self.summary_trigger_messages :]:
            speaker = message.name or message.role
            summary_lines.append(f"{speaker}: {message.content}")
        content = "\n".join(summary_lines)
        self.memory_repository.upsert_summary(session_id=session_id, content=content)
