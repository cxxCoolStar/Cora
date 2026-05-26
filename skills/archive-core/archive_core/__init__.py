"""Portable archive-core library (host-agnostic)."""

from archive_core.models import (
    SCHEMA_VERSION,
    ArchiveAction,
    ArchiveArtifact,
    ArchiveRecord,
    ArchiveRequest,
    ArchiveResult,
    ScoredRecord,
)
from archive_core.runtime import ArchiveRuntime
from archive_core.store.file_store import FileArchiveStore

__all__ = [
    "SCHEMA_VERSION",
    "ArchiveAction",
    "ArchiveArtifact",
    "ArchiveRecord",
    "ArchiveRequest",
    "ArchiveResult",
    "ArchiveRuntime",
    "FileArchiveStore",
    "ScoredRecord",
]

__version__ = "0.2.0"
