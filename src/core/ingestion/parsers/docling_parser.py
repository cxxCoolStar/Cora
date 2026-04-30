from __future__ import annotations

from core.ingestion.parsers.base import FileSource, ParsedContent


class DoclingFileParser:
    """Parse DOCX/MD/PDF (and more in the future) using Docling."""

    def parse(self, source: FileSource) -> ParsedContent:
        # Import lazily so the rest of the app can still start even if docling isn't installed.
        try:
            from docling.document_converter import DocumentConverter
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("docling is required to parse DOCX/MD/PDF files") from exc

        converter = DocumentConverter()
        result = converter.convert(source=str(source.path))
        doc = result.document

        markdown = doc.export_to_markdown()

        title = source.filename.rsplit(".", 1)[0] or source.filename
        metadata: dict = {
            "original_file_name": source.filename,
            "docling_status": getattr(result, "status", None),
        }
        return ParsedContent(
            item_type="document",
            title=title,
            raw_content=markdown,
            normalized_text=markdown,
            metadata=metadata,
        )
