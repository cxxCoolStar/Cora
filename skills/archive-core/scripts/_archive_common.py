"""Backward-compatible shim; implementation lives in archive_core."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(_SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(_SKILL_ROOT))

from archive_core.paths import (  # noqa: E402
    TOPICS_DIRNAME,
    ArchivePaths,
    archive_paths,
    ensure_archive_layout,
    generate_asset_id,
    normalize_asset_type,
    normalize_created_at,
    normalize_topic,
    unique_destination,
)


def read_index_records(index_path: Path) -> list[dict[str, Any]]:
    if not index_path.exists():
        return []
    records: list[dict[str, Any]] = []
    with index_path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def append_index_record(index_path: Path, record: dict[str, Any]) -> None:
    with index_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def summarize_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record.get("id"),
        "type": record.get("type"),
        "topic": record.get("topic"),
        "path": record.get("path"),
        "filename": record.get("filename"),
        "summary": record.get("summary"),
        "description": record.get("description"),
        "source": record.get("source"),
        "created_at": record.get("created_at"),
    }
