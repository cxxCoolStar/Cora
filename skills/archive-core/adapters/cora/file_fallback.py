from __future__ import annotations

from pathlib import Path
from typing import Any

from adapters.cora.bridge import archive_result_to_skill_payload


def _resolve_query(arguments: dict[str, Any]) -> str:
    return str(arguments.get("query") or arguments.get("text") or arguments.get("title") or "").strip()


def _file_store():
    from archive_core.store.file_store import FileArchiveStore

    from adapters.cora.settings import get_cora_archive_settings

    settings = get_cora_archive_settings()
    return FileArchiveStore(settings.archive_root_dir)


def try_deliver_from_file_archive(
    *,
    payload: dict[str, Any],
    query: str = "",
    record_id: str = "",
) -> dict[str, Any] | None:
    from adapters.cora.settings import get_cora_archive_settings

    if not get_cora_archive_settings().archive_mirror_enabled:
        return None

    from archive_core.models import ArchiveRequest
    from archive_core.runtime import ArchiveRuntime

    arguments = dict(payload.get("arguments") or {})
    resolved_query = query or _resolve_query(arguments)
    request_payload: dict[str, Any] = {
        "schema_version": "1.0",
        "intent": "deliver",
        "arguments": {},
    }
    if record_id:
        request_payload["arguments"] = {"record_id": record_id}
    elif resolved_query:
        request_payload["arguments"] = {"query": resolved_query}
    else:
        return None

    result = ArchiveRuntime(_file_store()).run(ArchiveRequest.from_dict(request_payload))
    if result.status == "failed" and not result.actions:
        return None

    skill = archive_result_to_skill_payload(result.to_dict())
    skill["action"] = "retrieve"
    return skill


def try_search_file_archive_response(
    *,
    payload: dict[str, Any],
    query: str,
) -> dict[str, Any] | None:
    from adapters.cora.settings import get_cora_archive_settings

    if not get_cora_archive_settings().archive_mirror_enabled or not query.strip():
        return None

    from archive_core.models import ArchiveRequest
    from archive_core.runtime import ArchiveRuntime

    result = ArchiveRuntime(_file_store()).run(
        ArchiveRequest.from_dict(
            {
                "schema_version": "1.0",
                "intent": "search",
                "arguments": {"query": query},
            }
        )
    )
    if result.status == "failed" or "没有找到" in result.message:
        return None

    skill = archive_result_to_skill_payload(result.to_dict())
    skill["action"] = "retrieve"
    return skill
