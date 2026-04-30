from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Protocol

from core.ingestion.parsers.base import FileSource, ParsedContent


class VisionImageDescriber(Protocol):
    def describe_image(self, *, image_path: Path, mime_type: str) -> str:
        ...


class ImageFileParser:
    """Parse image uploads by converting visual content into searchable text."""

    def __init__(self, *, describer: VisionImageDescriber | None) -> None:
        self.describer = describer

    def parse(self, source: FileSource) -> ParsedContent:
        if self.describer is None:
            raise RuntimeError(
                "Auxiliary vision client is not configured. "
                "Set CORA_AUXILIARY_VISION_MODEL and CORA_AUXILIARY_VISION_API_KEY."
            )

        mime_type = self._guess_mime_type(source.filename)
        description = self.describer.describe_image(image_path=source.path, mime_type=mime_type).strip()
        title = source.filename.rsplit(".", 1)[0] or source.filename
        normalized_text = (
            f"Image file: {source.filename}\n"
            f"Visual description:\n{description}"
        ).strip()
        return ParsedContent(
            item_type="image",
            title=title,
            raw_content=description,
            normalized_text=normalized_text,
            metadata={
                "original_file_name": source.filename,
                "mime_type": mime_type,
                "file_suffix": Path(source.filename).suffix.lower(),
                "vision_status": "ok",
            },
        )

    @staticmethod
    def _guess_mime_type(filename: str) -> str:
        guessed, _ = mimetypes.guess_type(filename)
        if guessed and guessed.startswith("image/"):
            return guessed
        return "image/png"
