from __future__ import annotations

from typing import Any

from core.skills.runner import run_archive_dispatch


def dispatch_cora_archive(payload: dict[str, Any]) -> dict[str, Any]:
    """Dispatch a Cora-shaped archive request through the archive-core adapter."""
    return run_archive_dispatch(payload)
