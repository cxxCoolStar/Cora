from __future__ import annotations

from typing import Any

from core.skills.runner import ensure_archive_adapter_paths


def try_deliver_from_file_archive(
    *,
    payload: dict[str, Any],
    query: str = "",
    record_id: str = "",
) -> dict[str, Any] | None:
    ensure_archive_adapter_paths()
    from adapters.cora.file_fallback import try_deliver_from_file_archive as _try_deliver

    return _try_deliver(payload=payload, query=query, record_id=record_id)


def try_search_file_archive_response(
    *,
    payload: dict[str, Any],
    query: str,
) -> dict[str, Any] | None:
    ensure_archive_adapter_paths()
    from adapters.cora.file_fallback import try_search_file_archive_response as _try_search

    return _try_search(payload=payload, query=query)


def search_file_archive(*, query: str, limit: int = 5) -> list[dict[str, Any]]:
    ensure_archive_adapter_paths()
    from adapters.cora.settings import get_cora_archive_settings
    from archive_core.store.file_store import FileArchiveStore

    settings = get_cora_archive_settings()
    if not settings.archive_mirror_enabled:
        return []
    matches = FileArchiveStore(settings.archive_root_dir).search(query=query, limit=limit)
    return [item.to_summary() for item in matches]
