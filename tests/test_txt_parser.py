from __future__ import annotations

from pathlib import Path

import pytest

from core.ingestion.parsers.base import FileSource
from core.ingestion.parsers.txt_parser import TxtFileParser


def test_txt_parser_can_decode_gbk_source(tmp_path: Path) -> None:
    file_path = tmp_path / "script.py"
    file_path.write_bytes("# 注释\nprint('你好')\n".encode("gbk"))

    parser = TxtFileParser()
    parsed = parser.parse(FileSource(path=file_path, filename="script.py"))

    assert parsed.item_type == "document"
    assert "注释" in parsed.normalized_text
    assert parsed.metadata["text_encoding"] == "gb18030"


def test_txt_parser_rejects_binary_payload(tmp_path: Path) -> None:
    file_path = tmp_path / "archive.bin"
    file_path.write_bytes(b"\x00\x01\x02\x03binary")

    parser = TxtFileParser()

    with pytest.raises(ValueError):
        parser.parse(FileSource(path=file_path, filename="archive.bin"))
