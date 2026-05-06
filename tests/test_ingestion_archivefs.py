from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import UploadFile

from core.archivefs.service import ArchiveImageWorkflow, ArchiveSkillScriptRunner
from core.ingestion.parsers.image_parser import ImageFileParser
from core.ingestion.service import IngestionService
from core.storage.db import DatabaseManager
from core.storage.repositories import (
    ItemRepository,
    MessageRepository,
    TopicActivityRepository,
    TopicItemRepository,
    TopicRepository,
    UserSignalRepository,
)
from core.topics.service import TopicOrganizerService


@dataclass
class StubDescriber:
    description: str

    def describe_image(self, *, image_path: Path, mime_type: str) -> str:
        return self.description


class NoopClassifier:
    def classify(self, *args, **kwargs):  # pragma: no cover - should not be used
        raise AssertionError("classify should not run for archivefs image ingestion")

    def resolve_query_to_topics(self, *args, **kwargs):  # pragma: no cover - not used here
        raise AssertionError("resolve_query_to_topics should not run in this test")


@pytest.mark.anyio
async def test_ingest_image_upload_uses_archivefs_and_links_topic(tmp_path: Path) -> None:
    database = DatabaseManager(f"sqlite:///{(tmp_path / 'clawbot.db').as_posix()}")
    database.create_all()

    item_repository = ItemRepository(database)
    message_repository = MessageRepository(database)
    user_signal_repository = UserSignalRepository(database)
    topic_repository = TopicRepository(database)
    topic_item_repository = TopicItemRepository(database)
    topic_activity_repository = TopicActivityRepository(database)

    image_parser = ImageFileParser(
        describer=StubDescriber(
            description="A portrait photo of a young woman standing in a garden scene."
        )
    )
    ingestion_service = IngestionService(
        item_repository=item_repository,
        message_repository=message_repository,
        user_signal_repository=user_signal_repository,
        storage_dir=tmp_path / "files",
    )
    ingestion_service.archive_image_workflow = ArchiveImageWorkflow(
        image_parser=image_parser,
        archive_runner=ArchiveSkillScriptRunner(archive_root=tmp_path / "archive"),
    )
    ingestion_service.topic_organizer = TopicOrganizerService(
        classifier=NoopClassifier(),
        topic_repository=topic_repository,
        topic_item_repository=topic_item_repository,
        topic_activity_repository=topic_activity_repository,
        item_repository=item_repository,
    )

    upload = UploadFile(filename="wechat_image.jpg", file=BytesIO(b"fake-image-bytes"))
    result = await ingestion_service.ingest(
        session_id="session-1",
        source_message_id="message-1",
        source_event_id="event-1",
        text=None,
        upload=upload,
        user_note="Taken at Zhujiang Park",
    )

    item = item_repository.get_any(item_id=result.item_id)
    metadata = item.metadata_json or {}
    stored_path = Path(metadata["stored_file_path"])

    assert result.topic_name == "Personal Photos"
    assert stored_path.is_file()
    assert "archive_record_id" in metadata
    assert metadata["archive_topic"] == "personal-photos"
    assert item.item_type == "image"
    assert "young woman" in item.raw_content.lower()

    index_path = tmp_path / "archive" / "logs" / "archive_index.jsonl"
    assert index_path.is_file()
