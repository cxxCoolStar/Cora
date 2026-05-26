from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def ensure_repo_src() -> None:
    src = repo_root() / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


ensure_repo_src()

from core.config import CoreSettings  # noqa: E402
from core.ingestion.service import IngestionService  # noqa: E402
from core.storage.db import DatabaseManager  # noqa: E402
from core.storage.repositories import ItemRepository, MessageRepository, UserSignalRepository  # noqa: E402


def build_database(database_url: str | None = None) -> DatabaseManager:
    if database_url:
        database = DatabaseManager(database_url)
    else:
        settings = CoreSettings()
        database = DatabaseManager(settings.clawbot_database_url)
    database.create_all()
    return database


def build_ingestion_service(
    database: DatabaseManager,
    storage_dir: str | Path | None = None,
) -> IngestionService:
    settings = CoreSettings()
    resolved_storage_dir = Path(storage_dir) if storage_dir else settings.files_storage_dir
    return IngestionService(
        item_repository=ItemRepository(database),
        message_repository=MessageRepository(database),
        user_signal_repository=UserSignalRepository(database),
        storage_dir=resolved_storage_dir,
    )


def item_to_summary(record: Any, *, score: int | None = None) -> dict[str, Any]:
    payload = {
        "item_id": record.id,
        "session_id": record.session_id,
        "item_type": record.item_type,
        "title": record.title,
        "summary": record.summary,
        "locator_hint": record.locator_hint,
        "created_at": record.created_at.isoformat(),
    }
    if score is not None:
        payload["score"] = score
    return payload


def print_json(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def candidate_match_score(*, record: Any, query: str) -> int:
    lowered_query = query.lower().strip()
    if not lowered_query:
        return 0
    haystacks = [
        record.title.lower(),
        record.summary.lower(),
        record.normalized_text.lower(),
        (record.locator_hint or "").lower(),
    ]
    metadata = record.metadata_json or {}
    haystacks.extend(
        [
            str(metadata.get("original_file_name") or "").lower(),
            str(metadata.get("user_note") or "").lower(),
            " ".join(str(value).lower() for value in metadata.values() if value is not None),
        ]
    )
    compact_query = "".join(lowered_query.split())
    score = 0
    for haystack in haystacks:
        if not haystack:
            continue
        compact_haystack = "".join(haystack.split())
        if compact_query and compact_query in compact_haystack:
            score += 35
        for token in [token for token in lowered_query.split() if token]:
            if token in haystack:
                score += 10
    return score
