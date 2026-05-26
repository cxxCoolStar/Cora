from __future__ import annotations

from typing import Any

from adapters.cora.mirror import mirror_item_record


def on_item_saved(*, item: Any, parsed: Any | None = None) -> None:
    metadata = getattr(item, "metadata_json", None) or {}
    stored_path = str(metadata.get("stored_file_path") or "").strip()
    if not stored_path and parsed is not None:
        stored_path = str((getattr(parsed, "metadata", None) or {}).get("stored_file_path") or "").strip()
    mirror_item_record(item=item, stored_file_path=stored_path or None)


def register_cora_hooks() -> None:
    from core.skills.hooks import register_item_saved_hook

    register_item_saved_hook("archive-core", on_item_saved)
