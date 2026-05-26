from __future__ import annotations

from pathlib import Path
from typing import Protocol

from archive_core.models import ArchiveRecord, ScoredRecord


class ArchiveStore(Protocol):
    def save_asset(
        self,
        *,
        source_path: Path,
        topic: str,
        asset_type: str,
        summary: str = "",
        description: str = "",
        source: str = "unknown",
        user_note: str = "",
        created_at: str = "",
        move: bool = False,
    ) -> ArchiveRecord: ...

    def search(
        self,
        *,
        query: str = "",
        topic: str = "",
        record_id: str = "",
        limit: int = 5,
    ) -> list[ScoredRecord]: ...

    def get(self, record_id: str) -> ArchiveRecord | None: ...

    def delete(self, record_id: str) -> ArchiveRecord | None: ...

    def list_topics(self) -> list[str]: ...

    def count_records(self, *, include_deleted: bool = False) -> int: ...
