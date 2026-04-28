from __future__ import annotations

from core.storage.sqlite.repositories import MessageRepository


class HistoryMemory:
    def __init__(self, *, message_repository: MessageRepository, max_messages: int) -> None:
        self.message_repository = message_repository
        self.max_messages = max_messages

    def load(self, *, session_id: str):
        return self.message_repository.list_for_session(session_id=session_id, limit=self.max_messages)
