from __future__ import annotations

from pathlib import Path
from typing import Any

from core.skills.runner import ensure_archive_adapter_paths


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
    ensure_archive_adapter_paths()
    from adapters.cora.mirror import mirror_upload_file as _mirror_upload_file

    return _mirror_upload_file(
        file_path=file_path,
        topic=topic,
        asset_type=asset_type,
        summary=summary,
        description=description,
        source=source,
        user_note=user_note,
    )


def mirror_item_record(*, item: Any, stored_file_path: str | None = None) -> dict[str, Any] | None:
    ensure_archive_adapter_paths()
    from adapters.cora.mirror import mirror_item_record as _mirror_item_record

    return _mirror_item_record(item=item, stored_file_path=stored_file_path)
