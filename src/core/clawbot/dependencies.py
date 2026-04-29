from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi import Request

from core.config import CoreSettings
from core.clawbot.intent_llm import LLMIntentClassifier
from core.clawbot.intent_router import IntentRouter
from core.clawbot.service import ClawBotService
from core.ingestion.service import IngestionService
from core.llm.dev_client import DevelopmentModelClient
from core.llm.openai_client import OpenAIChatModelClient
from core.retrieval.query_rewriter import QueryRewriter
from core.retrieval.service import RetrievalService
from core.storage.db import DatabaseManager
from core.storage.repositories import ClarificationRepository, ItemChunkRepository, ItemRepository, MessageRepository, SessionRepository, UserSignalRepository


@dataclass
class ClawBotContainer:
    settings: CoreSettings
    database: DatabaseManager
    session_repository: SessionRepository
    message_repository: MessageRepository
    item_repository: ItemRepository
    item_chunk_repository: ItemChunkRepository
    clarification_repository: ClarificationRepository
    user_signal_repository: UserSignalRepository
    ingestion_service: IngestionService
    retrieval_service: RetrievalService
    clawbot_service: ClawBotService
    templates_dir: str
    templates_static_dir: str

    def initialize(self) -> None:
        self.database.create_all()


_container: ClawBotContainer | None = None


def get_clawbot_container() -> ClawBotContainer:
    global _container
    if _container is None:
        settings = CoreSettings()
        database = DatabaseManager(settings.clawbot_database_url)
        session_repository = SessionRepository(database)
        message_repository = MessageRepository(database)
        item_repository = ItemRepository(database)
        item_chunk_repository = ItemChunkRepository(database)
        clarification_repository = ClarificationRepository(database)
        user_signal_repository = UserSignalRepository(database)
        ingestion_service = IngestionService(
            item_repository=item_repository,
            item_chunk_repository=item_chunk_repository,
            message_repository=message_repository,
            user_signal_repository=user_signal_repository,
            storage_dir=settings.files_storage_dir,
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

        query_rewriter = QueryRewriter(model_client=model_client) if model_client else None
        retrieval_service = RetrievalService(
            item_repository=item_repository,
            item_chunk_repository=item_chunk_repository,
            query_rewriter=query_rewriter,
        )

        llm_classifier = LLMIntentClassifier(model_client=model_client) if model_client else None
        intent_router = IntentRouter(llm_classifier=llm_classifier)
        clawbot_service = ClawBotService(
            session_repository=session_repository,
            message_repository=message_repository,
            item_repository=item_repository,
            item_chunk_repository=item_chunk_repository,
            ingestion_service=ingestion_service,
            clarification_repository=clarification_repository,
            user_signal_repository=user_signal_repository,
            retrieval_service=retrieval_service,
            intent_router=intent_router,
        )
        templates_dir = str(Path(__file__).resolve().parents[1] / "api" / "templates")
        static_dir = str(Path(__file__).resolve().parents[1] / "api" / "static")
        _container = ClawBotContainer(
            settings=settings,
            database=database,
            session_repository=session_repository,
            message_repository=message_repository,
            item_repository=item_repository,
            item_chunk_repository=item_chunk_repository,
            clarification_repository=clarification_repository,
            user_signal_repository=user_signal_repository,
            ingestion_service=ingestion_service,
            retrieval_service=retrieval_service,
            clawbot_service=clawbot_service,
            templates_dir=templates_dir,
            templates_static_dir=static_dir,
        )
    return _container


def get_container_from_request(request: Request) -> ClawBotContainer:
    return request.app.state.container
