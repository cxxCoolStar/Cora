from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from archive_core.models import ArchiveRecord, ScoredRecord
from archive_core.paths import (
    archive_paths,
    ensure_archive_layout,
    generate_asset_id,
    normalize_asset_type,
    normalize_created_at,
    normalize_topic,
    unique_destination,
)
from archive_core.search import score_record


class FileArchiveStore:
    """Filesystem archive: topics/ + logs/archive_index.jsonl."""

    def __init__(self, archive_root: str | Path) -> None:
        self.paths = archive_paths(archive_root)
        ensure_archive_layout(self.paths)

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
    ) -> ArchiveRecord:
        source_file = Path(source_path).expanduser().resolve()
        if not source_file.is_file():
            raise FileNotFoundError(f"source file not found: {source_file}")

        topic_slug = normalize_topic(topic)
        asset = normalize_asset_type(asset_type)
        topic_dir = self.paths.topics_root / topic_slug
        topic_dir.mkdir(parents=True, exist_ok=True)

        destination = unique_destination(topic_dir, source_file.name)
        if move:
            shutil.move(str(source_file), str(destination))
        else:
            shutil.copy2(source_file, destination)

        record = ArchiveRecord(
            id=generate_asset_id(asset),
            type=asset,
            topic=topic_slug,
            path=destination.relative_to(self.paths.archive_root).as_posix(),
            filename=destination.name,
            summary=(summary or "").strip(),
            description=(description or "").strip(),
            source=(source or "unknown").strip() or "unknown",
            user_note=(user_note or "").strip(),
            created_at=normalize_created_at(created_at or None),
            deleted=False,
        )
        self._append_record(record)
        return record

    def search(
        self,
        *,
        query: str = "",
        topic: str = "",
        record_id: str = "",
        limit: int = 5,
    ) -> list[ScoredRecord]:
        records = self._active_records()
        needle = (query or "").strip().lower()
        topic_filter = (topic or "").strip().lower()
        id_filter = (record_id or "").strip()
        limit = max(1, int(limit))

        scored: list[ScoredRecord] = []
        for record in records:
            if topic_filter and record.topic != topic_filter:
                continue
            if id_filter and record.id != id_filter:
                continue
            if id_filter:
                scored.append(self._to_scored(record, score=100))
                continue
            if needle:
                score = score_record(record=record, query=needle)
                if score <= 0:
                    continue
                scored.append(self._to_scored(record, score=score))
                continue
            scored.append(self._to_scored(record, score=0))

        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[:limit]

    def get(self, record_id: str) -> ArchiveRecord | None:
        matches = self.search(record_id=record_id, limit=1)
        if not matches:
            return None
        return matches[0].record

    def delete(self, record_id: str) -> ArchiveRecord | None:
        record = self.get(record_id)
        if record is None:
            return None
        asset_path = (self.paths.archive_root / Path(record.path)).resolve()
        if asset_path.exists() and asset_path.is_file():
            asset_path.unlink()
        tombstone = ArchiveRecord(
            id=record.id,
            type=record.type,
            topic=record.topic,
            path=record.path,
            filename=record.filename,
            summary=record.summary,
            description=record.description,
            source=record.source,
            user_note=record.user_note,
            created_at=record.created_at,
            deleted=True,
        )
        self._append_record(tombstone)
        return tombstone

    def list_topics(self) -> list[str]:
        topics = {record.topic for record in self._active_records() if record.topic}
        return sorted(topics)

    def count_records(self, *, include_deleted: bool = False) -> int:
        if include_deleted:
            return len(self._read_index_records())
        return len(self._active_records())

    def resolve_path(self, record: ArchiveRecord) -> Path:
        return (self.paths.archive_root / Path(record.path)).resolve()

    def _to_scored(self, record: ArchiveRecord, *, score: int) -> ScoredRecord:
        resolved = self.resolve_path(record)
        return ScoredRecord(
            record=record,
            score=score,
            resolved_path=str(resolved),
            file_exists=resolved.is_file(),
        )

    def _active_records(self) -> list[ArchiveRecord]:
        latest: dict[str, ArchiveRecord] = {}
        for payload in self._read_index_records():
            record = ArchiveRecord.from_dict(payload)
            if not record.id:
                continue
            latest[record.id] = record
        return [record for record in latest.values() if not record.deleted]

    def _read_index_records(self) -> list[dict[str, Any]]:
        if not self.paths.index_path.exists():
            return []
        records: list[dict[str, Any]] = []
        with self.paths.index_path.open("r", encoding="utf-8") as handle:
            for raw in handle:
                line = raw.strip()
                if not line:
                    continue
                records.append(json.loads(line))
        return records

    def _append_record(self, record: ArchiveRecord) -> None:
        with self.paths.index_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
