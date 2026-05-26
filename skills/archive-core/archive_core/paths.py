from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

INDEX_RELATIVE_PATH = Path("logs") / "archive_index.jsonl"
TOPICS_DIRNAME = "topics"
_TOPIC_SAFE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


@dataclass(slots=True)
class ArchivePaths:
    archive_root: Path
    topics_root: Path
    logs_root: Path
    index_path: Path


def archive_paths(archive_root: str | Path) -> ArchivePaths:
    root = Path(archive_root).expanduser().resolve()
    topics_root = root / TOPICS_DIRNAME
    logs_root = root / "logs"
    index_path = root / INDEX_RELATIVE_PATH
    return ArchivePaths(
        archive_root=root,
        topics_root=topics_root,
        logs_root=logs_root,
        index_path=index_path,
    )


def ensure_archive_layout(paths: ArchivePaths) -> None:
    paths.archive_root.mkdir(parents=True, exist_ok=True)
    paths.topics_root.mkdir(parents=True, exist_ok=True)
    paths.logs_root.mkdir(parents=True, exist_ok=True)
    paths.index_path.touch(exist_ok=True)


def normalize_topic(topic: str) -> str:
    value = (topic or "").strip().lower()
    if not value:
        raise ValueError("topic is required")
    if not _TOPIC_SAFE_RE.match(value):
        raise ValueError(
            "topic must use lowercase slug characters: letters, digits, dots, underscores, or hyphens"
        )
    return value


def normalize_asset_type(asset_type: str) -> str:
    value = (asset_type or "").strip().lower()
    if not value:
        raise ValueError("asset type is required")
    return value


def normalize_created_at(created_at: str | None) -> str:
    value = (created_at or "").strip()
    if not value:
        return datetime.now().astimezone().isoformat(timespec="seconds")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def generate_asset_id(asset_type: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    short = uuid4().hex[:8]
    prefix = (asset_type or "asset").strip().lower().replace(" ", "_")
    return f"{prefix}_{stamp}_{short}"


def unique_destination(topic_dir: Path, filename: str) -> Path:
    candidate = topic_dir / filename
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    counter = 2
    while True:
        next_candidate = topic_dir / f"{stem}_{counter}{suffix}"
        if not next_candidate.exists():
            return next_candidate
        counter += 1
