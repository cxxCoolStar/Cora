from __future__ import annotations

from docx import Document

from core.ingestion.parsers.base import FileSource, ParsedContent


class DocxFileParser:
    def parse(self, source: FileSource) -> ParsedContent:
        document = Document(source.path)
        paragraphs = [paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()]
        text = "\n".join(paragraphs)
        title = source.filename.rsplit(".", 1)[0] or source.filename
        return ParsedContent(
            item_type="document",
            title=title,
            raw_content=text,
            normalized_text=text,
            metadata={"original_file_name": source.filename},
        )
