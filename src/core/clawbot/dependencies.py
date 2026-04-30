from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi import Request

from core.config import CoreSettings
from core.clawbot.service import ClawBotService
from core.clawbot.tools import ArchiveToolExecutor
from core.ingestion.parsers.image_parser import ImageFileParser
from core.ingestion.service import IngestionService
from core.llm.dev_client import DevelopmentModelClient
from core.llm.openai_client import OpenAIChatModelClient
from core.llm.openai_vision_client import OpenAIVisionClient
from core.storage.db import DatabaseManager
from core.storage.repositories import (
    ChannelSessionMapRepository,
    ClarificationRepository,
    ItemRepository,
    MessageRepository,
    SessionRepository,
    TopicActivityRepository,
    TopicItemRepository,
    TopicRepository,
    UserSignalRepository,
)
from core.topics.classifier import TopicClassifier
from core.topics.service import TopicOrganizerService


@dataclass
class ClawBotContainer:
    settings: CoreSettings
    database: DatabaseManager
    session_repository: SessionRepository
    message_repository: MessageRepository
    item_repository: ItemRepository
    clarification_repository: ClarificationRepository
    user_signal_repository: UserSignalRepository
    topic_repository: TopicRepository
    ingestion_service: IngestionService
    clawbot_service: ClawBotService
    tool_executor: ArchiveToolExecutor
    templates_dir: str
    templates_static_dir: str

    def initialize(self) -> None:
        self.database.create_all()

    def configure_gateway(self, gateway_service: Any, session_map_repository: ChannelSessionMapRepository | None = None) -> None:
        """Configure gateway service for file sending capabilities."""
        self.tool_executor.gateway_service = gateway_service
        self.tool_executor.session_map_repository = session_map_repository
        self.tool_executor.channel_name = "wechat"


_container: ClawBotContainer | None = None


def get_clawbot_container() -> ClawBotContainer:
    global _container
    if _container is None:
        settings = CoreSettings()
        database = DatabaseManager(settings.clawbot_database_url)
        session_repository = SessionRepository(database)
        message_repository = MessageRepository(database)
        item_repository = ItemRepository(database)
        clarification_repository = ClarificationRepository(database)
        user_signal_repository = UserSignalRepository(database)
        topic_repository = TopicRepository(database)
        topic_item_repository = TopicItemRepository(database)
        topic_activity_repository = TopicActivityRepository(database)
        image_parser = ImageFileParser(describer=None)
        vision_model = (settings.auxiliary_vision_model or "").strip()
        if vision_model:
            provider = (settings.auxiliary_vision_provider or "openai").strip().lower()
            if provider in {"openai", "openai_compatible"}:
                vision_api_key = (settings.auxiliary_vision_api_key or settings.openai_api_key or "").strip()
                if vision_api_key:
                    vision_client = OpenAIVisionClient(
                        api_key=vision_api_key,
                        model=vision_model,
                        base_url=(settings.auxiliary_vision_base_url or settings.openai_base_url),
                        timeout=float(max(10, settings.auxiliary_vision_timeout_seconds)),
                    )
                    image_parser = ImageFileParser(describer=vision_client)
        ingestion_service = IngestionService(
            item_repository=item_repository,
            message_repository=message_repository,
            user_signal_repository=user_signal_repository,
            storage_dir=settings.files_storage_dir,
            image_parser=image_parser,
        )
        model_client = None
        if settings.model_provider == "openai" or (settings.openai_api_key and settings.model):
            model_client = OpenAIChatModelClient(
                api_key=settings.openai_api_key or "",
                model=settings.model or "",
                base_url=settings.openai_base_url,
            )
        elif settings.debug:
            model_client = DevelopmentModelClient()
        if model_client is None:
            raise RuntimeError("Cora requires a configured model client; heuristic routing and planning have been removed.")

        topic_classifier = TopicClassifier(model_client=model_client)
        topic_organizer = TopicOrganizerService(
            classifier=topic_classifier,
            topic_repository=topic_repository,
            topic_item_repository=topic_item_repository,
            topic_activity_repository=topic_activity_repository,
            item_repository=item_repository,
        )
        ingestion_service.topic_organizer = topic_organizer

        tool_executor = ArchiveToolExecutor(
            ingestion_service=ingestion_service,
            item_repository=item_repository,
            clarification_repository=clarification_repository,
            topic_organizer=topic_organizer,
        )
        clawbot_service = ClawBotService(
            session_repository=session_repository,
            message_repository=message_repository,
            item_repository=item_repository,
            ingestion_service=ingestion_service,
            clarification_repository=clarification_repository,
            user_signal_repository=user_signal_repository,
            topic_repository=topic_repository,
            model_client=model_client,
            tool_executor=tool_executor,
            topic_organizer=topic_organizer,
        )
        templates_dir = str(Path(__file__).resolve().parents[1] / "api" / "templates")
        static_dir = str(Path(__file__).resolve().parents[1] / "api" / "static")
        _container = ClawBotContainer(
            settings=settings,
            database=database,
            session_repository=session_repository,
            message_repository=message_repository,
            item_repository=item_repository,
            clarification_repository=clarification_repository,
            user_signal_repository=user_signal_repository,
            topic_repository=topic_repository,
            ingestion_service=ingestion_service,
            clawbot_service=clawbot_service,
            templates_dir=templates_dir,
            templates_static_dir=static_dir,
        )
    return _container


def get_container_from_request(request: Request) -> ClawBotContainer:
    return request.app.state.container
