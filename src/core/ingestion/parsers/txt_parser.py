from __future__ import annotations

from core.ingestion.parsers.base import FileSource, ParsedContent


class TxtFileParser:
    def parse(self, source: FileSource) -> ParsedContent:
        text = source.path.read_text(encoding="utf-8")
        title = source.filename.rsplit(".", 1)[0] or source.filename
        return ParsedContent(
            item_type="document",
            title=title,
            raw_content=text,
            normalized_text=text.strip(),
            metadata={"original_file_name": source.filename},
        )
