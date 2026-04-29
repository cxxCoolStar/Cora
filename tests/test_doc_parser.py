from __future__ import annotations

from pathlib import Path

import pytest

from core.ingestion.parsers.base import FileSource
from core.ingestion.parsers.doc_parser import DocFileParser
from core.ingestion.parsers.base import ParsedContent


def test_doc_parser_uses_pandoc(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Create a dummy .doc file; we mock pandoc conversion so the file contents don't matter.
    doc_path = tmp_path / "resume.doc"
    doc_path.write_bytes(b"%DOC%")

    # Force the parser to skip Word conversion even on Windows.
    monkeypatch.setattr(DocFileParser, "_try_convert_doc_to_docx", staticmethod(lambda _: None))

    class FakePypandoc:
        @staticmethod
        def convert_file(path: str, to: str, format: str):
            assert Path(path) == doc_path
            assert to == "plain"
            assert format == "doc"
            return "Name: Alice\nSkills: Python"

    monkeypatch.setitem(__import__("sys").modules, "pypandoc", FakePypandoc)

    parser = DocFileParser()
    parsed = parser.parse(FileSource(path=doc_path, filename="resume.doc"))

    assert parsed.item_type == "document"
    assert parsed.title == "resume"
    assert "Skills" in parsed.normalized_text


def test_doc_parser_prefers_word_conversion_when_available(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    doc_path = tmp_path / "resume.doc"
    doc_path.write_bytes(b"%DOC%")

    # Simulate Windows.
    monkeypatch.setattr("os.name", "nt", raising=False)

    # Mock conversion to create a docx placeholder file.
    converted_path = tmp_path / "resume.docx"
    converted_path.write_text("fake docx", encoding="utf-8")
    monkeypatch.setattr(DocFileParser, "_try_convert_doc_to_docx", staticmethod(lambda _: converted_path))

    # Mock Docling parse so we don't depend on docling internals in this unit test.
    def fake_docling_parse(_self, source: FileSource):
        return ParsedContent(
            item_type="document",
            title="resume",
            raw_content="converted",
            normalized_text="converted",
            metadata={"original_file_name": source.filename},
        )

    monkeypatch.setattr("core.ingestion.parsers.doc_parser.DoclingFileParser.parse", fake_docling_parse)

    parser = DocFileParser()
    parsed = parser.parse(FileSource(path=doc_path, filename="resume.doc"))
    assert parsed.item_type == "document"
    assert parsed.normalized_text == "converted"
    assert parsed.metadata["original_file_name"] == "resume.doc"
    assert parsed.metadata["derived_docx_path"] == str(converted_path)
