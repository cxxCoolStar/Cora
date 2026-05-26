from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


def archive_skill_root() -> Path:
    return Path(__file__).resolve().parents[3] / "skills" / "archive-core"


def repo_src_root() -> Path:
    return Path(__file__).resolve().parents[2]


def ensure_archive_adapter_paths() -> None:
    skill_root = str(archive_skill_root())
    repo_src = str(repo_src_root())
    for entry in (repo_src, skill_root):
        if entry not in sys.path:
            sys.path.insert(0, entry)


def run_archive_dispatch(payload: dict[str, Any]) -> dict[str, Any]:
    """Run archive-core Cora adapter dispatch in-process."""
    ensure_archive_adapter_paths()
    from adapters.cora.dispatch import run_dispatch

    return run_dispatch(payload)
