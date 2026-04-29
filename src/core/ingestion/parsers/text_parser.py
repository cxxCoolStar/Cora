from __future__ import annotations

from core.ingestion.parsers.base import ParsedContent


class TextParser:
    def parse(self, text: str) -> ParsedContent:
        stripped = text.strip()
        lines = [line.strip() for line in stripped.splitlines() if line.strip()]
        title = lines[0][:60] if lines else "Text note"
        return ParsedContent(
            item_type="text_note",
            title=title or "Text note",
            raw_content=text,
            normalized_text=stripped,
            metadata={},
        )
