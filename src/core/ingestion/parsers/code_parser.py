from __future__ import annotations

from core.ingestion.parsers.txt_parser import TxtFileParser


class CodeFileParser(TxtFileParser):
    """Compatibility wrapper for code and config files treated as plain text."""
