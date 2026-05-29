from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True, slots=True)
class CoraArchiveSettings:
    archive_root_dir: Path
    archive_mirror_enabled: bool
    archive_storage_mode: str = "filesystem"


@lru_cache(maxsize=1)
def get_cora_archive_settings() -> CoraArchiveSettings:
    try:
        from core.config import CoreSettings

        settings = CoreSettings()
        return CoraArchiveSettings(
            archive_root_dir=settings.archive_root_dir,
            archive_mirror_enabled=settings.archive_mirror_enabled,
            archive_storage_mode=str(getattr(settings, "archive_storage_mode", "filesystem")),
        )
    except Exception:
        import os

        root = Path(os.environ.get("CORA_ARCHIVE_ROOT_DIR") or os.environ.get("ARCHIVE_ROOT") or ".cora/archive")
        enabled = str(os.environ.get("CORA_ARCHIVE_MIRROR_ENABLED", "true")).strip().lower() not in {
            "0",
            "false",
            "no",
        }
        mode = str(os.environ.get("CORA_ARCHIVE_STORAGE_MODE", "filesystem")).strip().lower() or "filesystem"
        return CoraArchiveSettings(archive_root_dir=root, archive_mirror_enabled=enabled, archive_storage_mode=mode)
