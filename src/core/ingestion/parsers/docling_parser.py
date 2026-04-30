from __future__ import annotations

from core.ingestion.parsers.base import FileSource, ParsedContent


class DoclingFileParser:
    """Parse DOCX/MD/PDF (and more in the future) using Docling."""

    def parse(self, source: FileSource) -> ParsedContent:
        # Import lazily so the rest of the app can still start even if docling isn't installed.
        try:
            from docling.chunking import HybridChunker
            from docling.document_converter import DocumentConverter
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("docling is required to parse DOCX/MD/PDF files") from exc

        converter = DocumentConverter()
        result = converter.convert(source=str(source.path))
        doc = result.document

        markdown = doc.export_to_markdown()

        # Structure-aware chunking improves retrieval and avoids naive paragraph splits.
        chunker = HybridChunker()
        chunk_texts: list[str] = []
        for chunk in chunker.chunk(dl_doc=doc):
            enriched = chunker.contextualize(chunk=chunk).strip()
            if enriched:
                chunk_texts.append(enriched)

        title = source.filename.rsplit(".", 1)[0] or source.filename
        metadata: dict = {
            "original_file_name": source.filename,
            "docling_status": getattr(result, "status", None),
            # Used by ingestion service to persist better chunks without bloating item metadata.
            "_chunk_texts": chunk_texts,
        }
        return ParsedContent(
            item_type="document",
            title=title,
            raw_content=markdown,
            normalized_text=markdown,
            metadata=metadata,
        )
