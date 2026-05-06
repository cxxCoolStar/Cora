from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.ingestion.parsers.base import FileSource
from core.ingestion.parsers.image_parser import ImageFileParser
import re


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
        if scripts_root is None:
            repo_root = Path(__file__).resolve().parents[3]
            scripts_root = repo_root / "skills" / "archive-core" / "scripts"
        self.archive_root = Path(archive_root).expanduser().resolve()
        self.scripts_root = Path(scripts_root).expanduser().resolve()
        self.python_executable = python_executable or sys.executable

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
        args = [
            "--archive-root",
            str(self.archive_root),
            "--source-file",
            str(Path(source_file).expanduser().resolve()),
            "--topic",
            topic,
            "--type",
            asset_type,
            "--summary",
            summary,
            "--description",
            description,
            "--source",
            source,
            "--user-note",
            user_note,
        ]
        if created_at:
            args.extend(["--created-at", created_at])
        if move:
            args.append("--move")
        payload = self._run_json_script("save_asset.py", args)
        return ArchiveSaveResult(
            topic=str(payload["topic"]),
            stored_path=Path(payload["stored_path"]).resolve(),
            relative_path=str(payload["relative_path"]),
            record=ArchiveAssetRecord.from_dict(payload["record"]),
        )

    def find_assets(
        self,
        *,
        query: str = "",
        topic: str = "",
        record_id: str = "",
        limit: int = 5,
    ) -> ArchiveLookupResult:
        args = [
            "--archive-root",
            str(self.archive_root),
            "--query",
            query,
            "--topic",
            topic,
            "--id",
            record_id,
            "--limit",
            str(limit),
        ]
        payload = self._run_json_script("find_asset.py", args)
        return ArchiveLookupResult(
            count=int(payload.get("count") or 0),
            results=list(payload.get("results") or []),
        )

    def _run_json_script(self, script_name: str, args: list[str]) -> dict[str, Any]:
        script_path = self.scripts_root / script_name
        completed = subprocess.run(
            [self.python_executable, str(script_path), *args],
            capture_output=True,
            text=True,
            check=True,
        )
        payload = json.loads(completed.stdout)
        if not isinstance(payload, dict):
            raise ValueError(f"{script_name} did not return a JSON object")
        return payload


class ArchiveImageWorkflow:
    def __init__(
        self,
        *,
        image_parser: ImageFileParser,
        archive_runner: ArchiveSkillScriptRunner,
    ) -> None:
        self.image_parser = image_parser
        self.archive_runner = archive_runner

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
        selected_topic = topic or self._choose_topic(parsed.title, parsed.raw_content)
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

    def _choose_topic(self, title: str, description: str) -> str:
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
