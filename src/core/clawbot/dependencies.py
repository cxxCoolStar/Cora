from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import Request

from core.agent.context_budget import ContextBudgetManager
from core.config import CoreSettings
from core.clawbot.service import ClawBotService
from core.clawbot.tools import RuntimeToolExecutor
from core.ingestion.parsers.image_parser import ImageFileParser
from core.ingestion.service import IngestionService
from core.llm.dev_client import DevelopmentModelClient
from core.llm.openai_client import OpenAIChatModelClient
from core.llm.openai_vision_client import OpenAIVisionClient
from core.storage.db import DatabaseManager
from core.storage.repositories import (
    ChannelSessionMapRepository,
    ItemRepository,
    MessageRepository,
    PendingStateRepository,
    ScheduledTaskRepository,
    SessionRepository,
    SessionSummaryRepository,
    SqlAgentRunRecordRepository,
    SourceEventRepository,
    TopicActivityRepository,
    TopicItemRepository,
    TopicRepository,
    UserSignalRepository,
)
from core.topics.classifier import TopicClassifier
from core.topics.selector import TopicSelector
from core.topics.service import TopicOrganizerService


@dataclass
class ClawBotContainer:
    settings: CoreSettings
    database: DatabaseManager
    session_repository: SessionRepository
    session_summary_repository: SessionSummaryRepository
    message_repository: MessageRepository
    source_event_repository: SourceEventRepository
    item_repository: ItemRepository
    pending_state_repository: PendingStateRepository
    user_signal_repository: UserSignalRepository
    topic_repository: TopicRepository
    agent_run_record_repository: SqlAgentRunRecordRepository
    scheduled_task_repository: ScheduledTaskRepository
    ingestion_service: IngestionService
    clawbot_service: ClawBotService
    tool_executor: RuntimeToolExecutor

    def initialize(self) -> None:
        self.database.create_all()

    def configure_gateway(self, gateway_service: Any, session_map_repository: ChannelSessionMapRepository | None = None) -> None:
        """Configure gateway service for file sending capabilities."""
        self.tool_executor.gateway_service = gateway_service
        self.tool_executor.session_map_repository = session_map_repository
        self.tool_executor.channel_name = "wechat"
        self.clawbot_service.refresh_tool_specs()


_container: ClawBotContainer | None = None


def build_clawbot_container(*, settings: CoreSettings | None = None) -> ClawBotContainer:
    active_settings = settings or CoreSettings()
    database = DatabaseManager(active_settings.clawbot_database_url)
    session_repository = SessionRepository(database)
    session_summary_repository = SessionSummaryRepository(database)
    message_repository = MessageRepository(database)
    source_event_repository = SourceEventRepository(database)
    item_repository = ItemRepository(database)
    pending_state_repository = PendingStateRepository(database)
    user_signal_repository = UserSignalRepository(database)
    topic_repository = TopicRepository(database)
    agent_run_record_repository = SqlAgentRunRecordRepository(database)
    scheduled_task_repository = ScheduledTaskRepository(database)
    session_map_repository = ChannelSessionMapRepository(database)
    topic_item_repository = TopicItemRepository(database)
    topic_activity_repository = TopicActivityRepository(database)
    image_parser = ImageFileParser(describer=None)
    vision_model = (active_settings.auxiliary_vision_model or "").strip()
    if vision_model:
        provider = (active_settings.auxiliary_vision_provider or "openai").strip().lower()
        if provider in {"openai", "openai_compatible"}:
            vision_api_key = (active_settings.auxiliary_vision_api_key or active_settings.openai_api_key or "").strip()
            if vision_api_key:
                vision_client = OpenAIVisionClient(
                    api_key=vision_api_key,
                    model=vision_model,
                    base_url=(active_settings.auxiliary_vision_base_url or active_settings.openai_base_url),
                    timeout=float(max(10, active_settings.auxiliary_vision_timeout_seconds)),
                )
                image_parser = ImageFileParser(describer=vision_client)
    ingestion_service = IngestionService(
        item_repository=item_repository,
        message_repository=message_repository,
        user_signal_repository=user_signal_repository,
        storage_dir=active_settings.files_storage_dir,
        image_parser=image_parser,
    )
    model_client = None
    model_provider = (active_settings.model_provider or "dev").strip().lower()
    if model_provider in {"dev", "development"}:
        model_client = DevelopmentModelClient()
    elif model_provider == "openai" or (active_settings.openai_api_key and active_settings.model):
        model_client = OpenAIChatModelClient(
            api_key=active_settings.openai_api_key or "",
            model=active_settings.model or "",
            base_url=active_settings.openai_base_url,
            timeout=float(max(10, active_settings.openai_timeout_seconds)),
        )
    if model_client is None:
        raise RuntimeError("Cora requires a configured model client; heuristic routing and planning have been removed.")

    topic_classifier = TopicClassifier(model_client=model_client)
    topic_selector = TopicSelector(
        classifier=topic_classifier,
        topic_repository=topic_repository,
        archive_root=active_settings.archive_root_dir,
    )
    topic_organizer = TopicOrganizerService(
        classifier=topic_classifier,
        topic_repository=topic_repository,
        topic_item_repository=topic_item_repository,
        topic_activity_repository=topic_activity_repository,
        item_repository=item_repository,
        selector=topic_selector,
    )
    ingestion_service.topic_organizer = topic_organizer

    tool_executor = RuntimeToolExecutor(
        ingestion_service=ingestion_service,
        item_repository=item_repository,
        pending_state_repository=pending_state_repository,
        message_repository=message_repository,
        session_repository=session_repository,
        session_summary_repository=session_summary_repository,
        scheduled_task_repository=scheduled_task_repository,
        source_event_repository=source_event_repository,
        session_map_repository=session_map_repository,
        user_memory_path=active_settings.user_memory_path,
        file_tool_root=active_settings.file_tool_root,
        web_tavily_api_key=active_settings.tavily_api_key,
        web_tavily_base_url=active_settings.tavily_base_url,
        scheduled_task_default_timezone=(
            active_settings.scheduler_timezone
            or active_settings.wechat_session_timezone
            or "Asia/Shanghai"
        ),
    )
    context_budget_manager = ContextBudgetManager(
        context_length=active_settings.context_length,
        compression_threshold=active_settings.context_compression_threshold,
        summary_target_ratio=active_settings.context_summary_target_ratio,
        protect_last_n_min=active_settings.context_protect_last_n_min,
    )
    clawbot_service = ClawBotService(
        session_repository=session_repository,
        session_summary_repository=session_summary_repository,
        message_repository=message_repository,
        source_event_repository=source_event_repository,
        item_repository=item_repository,
        ingestion_service=ingestion_service,
        pending_state_repository=pending_state_repository,
        user_signal_repository=user_signal_repository,
        topic_repository=topic_repository,
        model_client=model_client,
        tool_executor=tool_executor,
        topic_organizer=topic_organizer,
        context_budget_manager=context_budget_manager,
        user_memory_path=active_settings.user_memory_path,
        file_tool_root=active_settings.file_tool_root,
        toolset_preset=active_settings.toolset_preset,
        harness_policy_profile=active_settings.harness_policy_profile,
        wechat_harness_policy_profile=active_settings.wechat_harness_policy_profile,
        job_harness_policy_profile=active_settings.job_harness_policy_profile,
        agent_run_record_repository=agent_run_record_repository,
    )
    return ClawBotContainer(
        settings=active_settings,
        database=database,
        session_repository=session_repository,
        session_summary_repository=session_summary_repository,
        message_repository=message_repository,
        source_event_repository=source_event_repository,
        item_repository=item_repository,
        pending_state_repository=pending_state_repository,
        user_signal_repository=user_signal_repository,
        topic_repository=topic_repository,
        agent_run_record_repository=agent_run_record_repository,
        scheduled_task_repository=scheduled_task_repository,
        ingestion_service=ingestion_service,
        clawbot_service=clawbot_service,
        tool_executor=tool_executor,
    )


def get_clawbot_container() -> ClawBotContainer:
    global _container
    if _container is None:
        _container = build_clawbot_container()
    return _container


def get_container_from_request(request: Request) -> ClawBotContainer:
    return request.app.state.container
