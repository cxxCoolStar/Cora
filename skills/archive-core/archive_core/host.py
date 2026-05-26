from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(slots=True)
class DeliverOutcome:
    delivered: bool
    message: str = ""


class ArchiveHost(Protocol):
    """Optional host capabilities (delivery, enriched search, uploads)."""

    def deliver_file(self, *, path: Path, title: str, session: dict) -> DeliverOutcome: ...
