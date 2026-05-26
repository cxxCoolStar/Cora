from __future__ import annotations

from typing import Any

from core.skills.runner import run_archive_dispatch


def run_dispatch(payload: dict[str, Any]) -> dict[str, Any]:
    """Run archive-core Cora dispatch in-process (WeChat / skill_run path)."""
    return run_archive_dispatch(payload)
