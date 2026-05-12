from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import UploadFile

from core.agent.loop import AgentLoop
from core.agent.context_manager import SessionContextManager
from core.agent.context_budget import ContextBudgetManager
from core.agent.orchestrator import AgentOrchestrator
from core.agent.prompt_builder import AgentPromptBuilder
from core.agent.runtime_manager import AgentRuntimeManager
from core.agent.runtime_state import RuntimeContextSnapshot
from core.agent.session_runtime import SessionRuntimeSnapshotLoader
from core.clawbot.schemas import (
    DeleteItemResponse,
    ItemDetailResponse,
    ItemSummaryResponse,
    SessionDebugResponse,
    TurnResponse,
)
from core.clawbot.service_runtime import (
    AssistantTurnOutcome,
    ClawBotSessionShell,
    SessionDebugAssembler,
)
from core.agent.skill_loader import SkillLoader
from core.agent.turn_runner import AgentTurnRunner
from core.clawbot.source_events import SourceEventManager
from core.clawbot.tools import RuntimeToolExecutor
from core.clawbot.user_profile import UserProfileAggregator
from core.ingestion.service import IngestionService
from core.llm.base import ModelClient
from core.schemas.message import Message
from core.schemas.tool import ToolSpec as ModelToolSpec
from core.storage.models import SessionRecord
from core.storage.repositories import (
    ItemRepository,
    MessageRepository,
    PendingStateRepository,
    SessionRepository,
    SessionSummaryRepository,
    SourceEventRepository,
    TopicRepository,
    UserSignalRepository,
)
from core.tools import ToolManager
from core.topics.service import TopicOrganizerService

logger = logging.getLogger(__name__)


class ClawBotService:
    def __init__(
        self,
        *,
        session_repository: SessionRepository,
        message_repository: MessageRepository,
        session_summary_repository: SessionSummaryRepository,
        source_event_repository: SourceEventRepository,
        item_repository: ItemRepository,
        ingestion_service: IngestionService,
        pending_state_repository: PendingStateRepository,
        user_signal_repository: UserSignalRepository,
        topic_repository: TopicRepository,
        model_client: ModelClient,
        tool_executor: RuntimeToolExecutor | None = None,
        topic_organizer: TopicOrganizerService | None = None,
        context_budget_manager: ContextBudgetManager | None = None,
        user_memory_path: Path | None = None,
        file_tool_root: Path | None = None,
        tool_manager: ToolManager | None = None,
    ) -> None:
        self.session_repository = session_repository
        self.message_repository = message_repository
        self.session_summary_repository = session_summary_repository
        self.source_event_repository = source_event_repository
        self.item_repository = item_repository
        self.ingestion_service = ingestion_service
        self.pending_state_repository = pending_state_repository
        self.user_signal_repository = user_signal_repository
        self.topic_repository = topic_repository
        self.model_client = model_client
        self.user_memory_path = user_memory_path or Path("user-memory/USER.md")
        self.file_tool_root = file_tool_root or Path(".")
        self.tool_manager = tool_manager or ToolManager()
        self.skill_loader = SkillLoader()
        self.user_profile_aggregator = UserProfileAggregator()
        self.runtime_manager = AgentRuntimeManager(
            pending_state_repository=pending_state_repository,
        )
        self.tool_executor = tool_executor or RuntimeToolExecutor(
            ingestion_service=ingestion_service,
            item_repository=item_repository,
            pending_state_repository=pending_state_repository,
            user_memory_path=self.user_memory_path,
            file_tool_root=self.file_tool_root,
            skill_roots=self.skill_loader.skill_roots,
            runtime_manager=self.runtime_manager,
        )
        self.topic_organizer = topic_organizer
        self._tool_specs = self._build_tool_specs()
        self._agent_loop = AgentLoop(
            model_client=self.model_client,
            tool_executor=self.tool_executor,
            tool_specs=self._tool_specs,
            context_budget_manager=context_budget_manager,
        )
        self._agent_orchestrator = AgentOrchestrator(
            loop=self._agent_loop,
            prompt_builder=AgentPromptBuilder(user_memory_path=self.user_memory_path),
            skill_loader=self.skill_loader,
        )
        self._context_manager = SessionContextManager(
            message_repository=message_repository,
            summary_repository=session_summary_repository,
            model_client=model_client,
            budget_manager=context_budget_manager,
        )
        self._runtime_snapshot_loader = SessionRuntimeSnapshotLoader(
            message_repository=message_repository,
            source_event_repository=source_event_repository,
        )
        self._source_event_manager = SourceEventManager(
            source_event_repository=source_event_repository,
        )
        self._session_shell = ClawBotSessionShell(
            message_repository=message_repository,
            pending_state_repository=pending_state_repository,
            tool_executor=self.tool_executor,
            source_event_manager=self._source_event_manager,
        )
        self._debug_assembler = SessionDebugAssembler(
            session_repository=session_repository,
            message_repository=message_repository,
            item_repository=item_repository,
            user_signal_repository=user_signal_repository,
            topic_repository=topic_repository,
            user_profile_aggregator=self.user_profile_aggregator,
        )
        self._agent_turn_runner = AgentTurnRunner(
            orchestrator=self._agent_orchestrator,
            loop=self._agent_loop,
            runtime_manager=self.runtime_manager,
            skill_loader=self.skill_loader,
            history_loader=self._load_agent_history,
            delivery_available=self.tool_executor.can_send_files_to_user,
            media_kind_resolver=self._source_event_manager.detect_media_kind,
        )

    def create_session(self) -> SessionRecord:
        return self.session_repository.create()

    def _build_tool_specs(self) -> list[ModelToolSpec]:
        toolsets = ["user_memory", "file", "skills", "skills_execute"]
        return self.tool_manager.build_model_tool_specs(toolsets=toolsets)

    def refresh_tool_specs(self) -> None:
        self._tool_specs = self._build_tool_specs()
        self._agent_loop.tool_specs = self._tool_specs

    def build_agent_messages(
        self,
        *,
        session_id: str,
        user_text: str,
        context_snapshot: RuntimeContextSnapshot,
        tool_messages: list[Message],
    ) -> list[Message]:
        return self._agent_turn_runner.build_agent_messages(
            session_id=session_id,
            user_text=user_text,
            context_snapshot=context_snapshot,
            tool_messages=tool_messages,
        )

    async def run_agent_loop(
        self,
        *,
        session_id: str,
        source_message_id: str,
        user_text: str,
        raw_text: str | None,
        upload: UploadFile | None,
        context_snapshot: RuntimeContextSnapshot,
    ):
        self._agent_turn_runner.sync_model_client(self.model_client)
        return await self._agent_turn_runner.run_turn(
            session_id=session_id,
            source_message_id=source_message_id,
            user_text=user_text,
            raw_text=raw_text,
            upload=upload,
            context_snapshot=context_snapshot,
        )

    def _load_agent_history(self, *, session_id: str, user_text: str) -> list[Message]:
        return self._context_manager.build_history(session_id=session_id, current_user_text=user_text).as_messages()

    async def ingest(
        self,
        *,
        session_id: str,
        text: str | None,
        upload: UploadFile | None,
        source_metadata: dict[str, Any] | None = None,
    ) -> TurnResponse:
        logger.info(
            "clawbot ingest_start session_id=%s has_text=%s has_upload=%s",
            session_id,
            bool(text and text.strip()),
            bool(upload and (upload.filename or "").strip()),
        )
        self.session_repository.get(session_id)
        inbound_turn = await self._session_shell.record_inbound_turn(
            session_id=session_id,
            text=text,
            upload=upload,
            source_metadata=source_metadata,
        )
        if inbound_turn.buffered_response is not None:
            logger.info(
                "clawbot upload_buffered session_id=%s source_event_id=%s filename=%s",
                session_id,
                inbound_turn.source_event_id,
                upload.filename if upload is not None else "",
            )
            return inbound_turn.buffered_response

        context_snapshot = self.load_context_snapshot(session_id=session_id)
        context_snapshot.current_source_event_id = inbound_turn.source_event_id
        turn_result = await self.run_agent_loop(
            session_id=session_id,
            source_message_id=inbound_turn.source_message_id,
            user_text=inbound_turn.model_text,
            raw_text=text,
            upload=upload,
            context_snapshot=context_snapshot,
        )
        outcome = self._session_shell.outcome_from_turn_result(turn_result)
        self._session_shell.persist_assistant_turn(
            session_id=session_id,
            outcome=outcome,
        )
        logger.info(
            "clawbot execution_done session_id=%s action=%s disposition=%s tool=%s",
            session_id,
            outcome.action,
            outcome.disposition,
            outcome.tool_name,
        )
        return self._session_shell.to_turn_response(outcome=outcome)

    async def reply(self, *, session_id: str, text: str) -> TurnResponse:
        return await self.ingest(session_id=session_id, text=text, upload=None)

    def list_items(self, *, session_id: str) -> list[ItemSummaryResponse]:
        self.session_repository.get(session_id)
        items = self.item_repository.list_by_session(session_id=session_id, current_only=True)
        return [
            ItemSummaryResponse(
                id=item.id,
                item_type=item.item_type,
                title=item.title,
                summary=item.summary,
                created_at=item.created_at,
            )
            for item in items
        ]

    def get_item(self, *, session_id: str, item_id: str) -> ItemDetailResponse:
        self.session_repository.get(session_id)
        item = self.item_repository.get(item_id=item_id, session_id=session_id)
        return ItemDetailResponse(
            id=item.id,
            item_type=item.item_type,
            title=item.title,
            summary=item.summary,
            normalized_text=item.normalized_text,
            locator_hint=item.locator_hint,
            created_at=item.created_at,
        )

    def delete_item(self, *, session_id: str, item_id: str) -> DeleteItemResponse:
        self.session_repository.get(session_id)
        item = self.item_repository.soft_delete(item_id=item_id, session_id=session_id)
        return DeleteItemResponse(
            reply=f"已删除资料 `{item.title}`。它不会再出现在默认列表和检索结果里。",
            item_id=item.id,
        )

    def list_sessions(self) -> list[SessionRecord]:
        return self.session_repository.list_recent()

    def get_session_debug(self, *, session_id: str) -> SessionDebugResponse:
        return self._debug_assembler.build(session_id=session_id)

    def load_context_snapshot(self, *, session_id: str) -> RuntimeContextSnapshot:
        return self._runtime_snapshot_loader.load_context_snapshot(session_id=session_id)
