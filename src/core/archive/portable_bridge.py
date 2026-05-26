from __future__ import annotations

from pathlib import Path
from typing import Any

from core.skills.runner import ensure_archive_adapter_paths


def run_portable_archive(
    payload: dict[str, Any],
    *,
    archive_root: Path,
    transport: str = "in_process",
) -> dict[str, Any]:
    ensure_archive_adapter_paths()
    from adapters.cora.bridge import run_portable_archive as _run

    return _run(payload, archive_root=archive_root, transport=transport)


def archive_result_to_skill_payload(result: dict[str, Any]) -> dict[str, Any]:
    ensure_archive_adapter_paths()
    from adapters.cora.bridge import archive_result_to_skill_payload as _convert

    return _convert(result)
