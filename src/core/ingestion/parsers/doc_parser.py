from __future__ import annotations

import os
import uuid
from pathlib import Path

from core.ingestion.parsers.base import FileSource, ParsedContent
from core.ingestion.parsers.docling_parser import DoclingFileParser


class DocFileParser:
    """Best-effort .doc text extraction using pandoc (via pypandoc)."""

    def parse(self, source: FileSource) -> ParsedContent:
        # 1) Windows: use Microsoft Word (COM) to convert .doc -> .docx, then parse via Docling.
        # This is usually the most faithful path for legacy .doc files.
        if os.name == "nt":
            converted = self._try_convert_doc_to_docx(source.path)
            if converted is not None:
                try:
                    parsed = DoclingFileParser().parse(FileSource(path=converted, filename=source.filename + ".docx"))
                    parsed.metadata["original_file_name"] = source.filename
                    parsed.metadata["derived_docx_path"] = str(converted)
                    return parsed
                except Exception:
                    # Docling not installed or failed; fall back to pandoc extraction below.
                    pass

        # 2) Fallback: attempt pandoc-based extraction to plain text.
        try:
            import pypandoc  # provided by pypandoc_binary dependency
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("pypandoc is required to parse .doc files") from exc

        # Pandoc format for legacy Word is typically "doc".
        text = pypandoc.convert_file(str(source.path), to="plain", format="doc")
        text = (text or "").strip()
        title = source.filename.rsplit(".", 1)[0] or source.filename
        return ParsedContent(
            item_type="document",
            title=title,
            raw_content=text,
            normalized_text=text,
            metadata={"original_file_name": source.filename},
        )

    @staticmethod
    def _try_convert_doc_to_docx(doc_path: Path) -> Path | None:
        try:
            import win32com.client  # type: ignore
        except Exception:
            return None

        out_path = doc_path.with_suffix(f".converted-{uuid.uuid4().hex}.docx")
        word = None
        doc = None
        try:
            word = win32com.client.Dispatch("Word.Application")
            word.Visible = False
            # Open with ReadOnly=True to avoid lock prompts; add_to_recent_files=False for cleanliness.
            doc = word.Documents.Open(str(doc_path), ReadOnly=True, AddToRecentFiles=False)
            # FileFormat=16 => wdFormatXMLDocument (.docx)
            doc.SaveAs(str(out_path), FileFormat=16)
            return out_path if out_path.exists() else None
        except Exception:
            # Conversion failed (Word not installed, broken file, permissions, etc.)
            try:
                if out_path.exists():
                    out_path.unlink()
            except Exception:
                pass
            return None
        finally:
            try:
                if doc is not None:
                    doc.Close(False)
            except Exception:
                pass
            try:
                if word is not None:
                    word.Quit()
            except Exception:
                pass
