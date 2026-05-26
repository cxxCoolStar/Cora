from __future__ import annotations

import logging

from core.skills.registry import is_registered, mark_registered
from core.skills.runner import ensure_archive_adapter_paths

logger = logging.getLogger(__name__)


def bootstrap_host_skills() -> None:
    """Register portable skill hooks and host adapters once per process."""
    if is_registered("archive-core"):
        return
    ensure_archive_adapter_paths()
    try:
        from adapters.cora.hooks import register_cora_hooks

        register_cora_hooks()
        mark_registered("archive-core")
        logger.debug("registered archive-core cora hooks")
    except Exception:
        logger.exception("failed to bootstrap archive-core adapter")
