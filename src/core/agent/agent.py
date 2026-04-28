from __future__ import annotations

from core.agent.config import CoreSettings
from core.agent.runtime import AgentRuntime
from core.llm.base import ModelClient
from core.memory.history import HistoryMemory
from core.memory.summary import SummaryMemoryManager
from core.prompts.system import build_system_prompt
from core.storage.sqlite.database import SQLiteDatabase
from core.storage.sqlite.repositories import EventRepository, MemoryRepository, MessageRepository, SessionRepository
from core.tools.registry import ToolRegistry


class CoreAgent:
    """Public entrypoint for creating and running the core agent."""

    def __init__(
        self,
        settings: CoreSettings,
        model_client: ModelClient,
        tool_registry: ToolRegistry,
    ) -> None:
        self.settings = settings
        self.database = SQLiteDatabase(settings.database_path)
        self.session_repository = SessionRepository(self.database)
        self.message_repository = MessageRepository(self.database)
        self.memory_repository = MemoryRepository(self.database)
        self.event_repository = EventRepository(self.database)
        self.history_memory = HistoryMemory(
            message_repository=self.message_repository,
            max_messages=settings.max_history_messages,
        )
        self.summary_memory = SummaryMemoryManager(
            message_repository=self.message_repository,
            memory_repository=self.memory_repository,
            summary_trigger_messages=settings.summary_trigger_messages,
        )
        self.runtime = AgentRuntime(
            settings=settings,
            model_client=model_client,
            tool_registry=tool_registry,
            session_repository=self.session_repository,
            message_repository=self.message_repository,
            memory_repository=self.memory_repository,
            event_repository=self.event_repository,
            history_memory=self.history_memory,
            summary_memory=self.summary_memory,
            system_prompt=build_system_prompt(settings.agent_name),
        )

    def start_session(self, metadata: dict | None = None) -> str:
        session = self.session_repository.create(agent_name=self.settings.agent_name, metadata=metadata or {})
        return session.id

    def run_turn(self, session_id: str, user_input: str):
        return self.runtime.run_turn(session_id=session_id, user_input=user_input)
