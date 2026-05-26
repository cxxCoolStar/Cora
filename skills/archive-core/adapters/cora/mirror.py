from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from adapters.cora.settings import get_cora_archive_settings

logger = logging.getLogger(__name__)


def _file_store():
    from archive_core.store.file_store import FileArchiveStore

    return FileArchiveStore(get_cora_archive_settings().archive_root_dir)


def mirror_upload_file(
    *,
    file_path: str | Path,
    topic: str,
    asset_type: str,
    summary: str = "",
    description: str = "",
    source: str = "wechat",
    user_note: str = "",
) -> dict[str, Any] | None:
    if not get_cora_archive_settings().archive_mirror_enabled:
        return None
    path = Path(file_path).expanduser().resolve()
    if not path.is_file():
        return None
    try:
        record = _file_store().save_asset(
            source_path=path,
            topic=topic or "inbox",
            asset_type=asset_type or "file",
            summary=summary,
            description=description,
            source=source,
            user_note=user_note,
            move=False,
        )
        logger.info("archive mirror record_id=%s topic=%s", record.id, record.topic)
        return record.to_dict()
    except Exception:
        logger.exception("archive mirror failed path=%s", path)
        return None


def mirror_item_record(*, item: Any, stored_file_path: str | None = None) -> dict[str, Any] | None:
    path = str(stored_file_path or (item.metadata_json or {}).get("stored_file_path") or "").strip()
    if not path:
        return None
    metadata = item.metadata_json or {}
    topic = str(metadata.get("topic_name") or metadata.get("topic_slug") or "inbox").strip()
    topic_slug = topic.lower().replace(" ", "-")[:64] if topic else "inbox"
    return mirror_upload_file(
        file_path=path,
        topic=topic_slug,
        asset_type=str(item.item_type or "file"),
        summary=str(item.title or ""),
        description=str(item.summary or item.normalized_text or "")[:2000],
        source=str(metadata.get("source") or "cora"),
        user_note=str(metadata.get("user_note") or ""),
    )
