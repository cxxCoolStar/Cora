from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class ParsedContent:
    item_type: str
    title: str
    raw_content: str
    normalized_text: str
    metadata: dict


@dataclass(slots=True)
class FileSource:
    path: Path
    filename: str
