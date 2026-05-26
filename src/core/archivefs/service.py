from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.ingestion.parsers.base import FileSource
from core.ingestion.parsers.image_parser import ImageFileParser
from core.topics.selector import TopicSelector, TopicSelectorInput


def _ensure_archive_core_on_path() -> None:
    skill_root = Path(__file__).resolve().parents[3] / "skills" / "archive-core"
    path = str(skill_root)
    if path not in sys.path:
        sys.path.insert(0, path)


_ensure_archive_core_on_path()
from archive_core.store.file_store import FileArchiveStore  # noqa: E402


@dataclass(slots=True)
class ArchiveAssetRecord:
    id: str
    asset_type: str
    topic: str
    path: str
    filename: str
    summary: str
    description: str
    source: str
    user_note: str
    created_at: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ArchiveAssetRecord":
        return cls(
            id=str(data.get("id") or ""),
            asset_type=str(data.get("type") or ""),
            topic=str(data.get("topic") or ""),
            path=str(data.get("path") or ""),
            filename=str(data.get("filename") or ""),
            summary=str(data.get("summary") or ""),
            description=str(data.get("description") or ""),
            source=str(data.get("source") or ""),
            user_note=str(data.get("user_note") or ""),
            created_at=str(data.get("created_at") or ""),
        )


@dataclass(slots=True)
class ArchiveSaveResult:
    topic: str
    stored_path: Path
    relative_path: str
    record: ArchiveAssetRecord


@dataclass(slots=True)
class ArchiveLookupResult:
    count: int
    results: list[dict[str, Any]]


class ArchiveSkillScriptRunner:
    def __init__(
        self,
        *,
        archive_root: Path,
        scripts_root: Path | None = None,
        python_executable: str | None = None,
    ) -> None:
        self.archive_root = Path(archive_root).expanduser().resolve()
        self._store = FileArchiveStore(self.archive_root)
        # scripts_root / python_executable kept for backward-compatible constructor signature
        _ = scripts_root
        _ = python_executable

    def save_asset(
        self,
        *,
        source_file: Path,
        topic: str,
        asset_type: str,
        summary: str = "",
        description: str = "",
        source: str = "unknown",
        user_note: str = "",
        created_at: str = "",
        move: bool = False,
    ) -> ArchiveSaveResult:
        record = self._store.save_asset(
            source_path=source_file,
            topic=topic,
            asset_type=asset_type,
            summary=summary,
            description=description,
            source=source,
            user_note=user_note,
            created_at=created_at,
            move=move,
        )
        stored_path = self._store.resolve_path(record)
        return ArchiveSaveResult(
            topic=record.topic,
            stored_path=stored_path,
            relative_path=record.path,
            record=ArchiveAssetRecord.from_dict(record.to_dict()),
        )

    def find_assets(
        self,
        *,
        query: str = "",
        topic: str = "",
        record_id: str = "",
        limit: int = 5,
    ) -> ArchiveLookupResult:
        matches = self._store.search(
            query=query,
            topic=topic,
            record_id=record_id,
            limit=limit,
        )
        results = [item.to_summary() for item in matches]
        return ArchiveLookupResult(count=len(results), results=results)


class ArchiveImageWorkflow:
    def __init__(
        self,
        *,
        image_parser: ImageFileParser,
        archive_runner: ArchiveSkillScriptRunner,
        topic_selector: TopicSelector | None = None,
    ) -> None:
        self.image_parser = image_parser
        self.archive_runner = archive_runner
        self.topic_selector = topic_selector

    def save_image(
        self,
        *,
        image_path: Path,
        topic: str | None = None,
        source: str = "unknown",
        user_note: str = "",
        created_at: str = "",
        move: bool = False,
    ) -> ArchiveSaveResult:
        source_file = Path(image_path).expanduser().resolve()
        try:
            parsed = self.image_parser.parse(
                FileSource(path=source_file, filename=source_file.name)
            )
        except Exception as exc:
            title = source_file.stem or source_file.name
            description = (
                "Image archived without a vision description because image analysis was unavailable. "
                f"Original parser error: {type(exc).__name__}: {exc}"
            )
            parsed = type("ParsedStub", (), {
                "item_type": "image",
                "title": title,
                "raw_content": description,
            })()
        selected_topic = topic or self._choose_topic(
            item_type=parsed.item_type,
            title=parsed.title,
            description=parsed.raw_content,
            user_note=user_note,
        )
        return self.archive_runner.save_asset(
            source_file=source_file,
            topic=selected_topic,
            asset_type=parsed.item_type,
            summary=parsed.title,
            description=parsed.raw_content,
            source=source,
            user_note=user_note,
            created_at=created_at,
            move=move,
        )

    def _choose_topic(self, *, item_type: str, title: str, description: str, user_note: str) -> str:
        if self.topic_selector is not None:
            selection = self.topic_selector.select(
                session_id="archivefs",
                item_input=TopicSelectorInput(
                    item_type=item_type,
                    title=title,
                    summary=title,
                    description=description,
                    user_note=user_note,
                ),
            )
            return selection.slug
        topics_root = self.archive_runner.archive_root / "topics"
        candidates = [path.name for path in topics_root.iterdir() if path.is_dir()] if topics_root.exists() else []
        if not candidates:
            return "personal-photos"
        haystack = f"{title} {description}".lower()
        scored: list[tuple[int, str]] = []
        for slug in candidates:
            tokens = [token for token in re.split(r"[-_.]+", slug.lower()) if token]
            score = 0
            for token in tokens:
                if token and token in haystack:
                    score += len(token) ** 2
            if "photo" in slug or "image" in slug or "picture" in slug:
                score += 4
            if score > 0:
                scored.append((score, slug))
        if scored:
            scored.sort(key=lambda item: item[0], reverse=True)
            return scored[0][1]
        if "personal-photos" in candidates:
            return "personal-photos"
        return sorted(candidates)[0]
