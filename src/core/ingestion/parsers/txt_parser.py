from __future__ import annotations

from pathlib import Path

from core.ingestion.parsers.base import FileSource, ParsedContent


class TxtFileParser:
    TEXT_FILE_EXTENSIONS = frozenset(
        {
            ".txt",
            ".mdx",
            ".rst",
            ".log",
            ".csv",
            ".tsv",
            ".py",
            ".pyi",
            ".js",
            ".jsx",
            ".ts",
            ".tsx",
            ".java",
            ".go",
            ".rs",
            ".c",
            ".cc",
            ".cpp",
            ".cxx",
            ".h",
            ".hh",
            ".hpp",
            ".hxx",
            ".cs",
            ".php",
            ".rb",
            ".swift",
            ".kt",
            ".kts",
            ".scala",
            ".lua",
            ".sql",
            ".sh",
            ".bash",
            ".zsh",
            ".ps1",
            ".bat",
            ".cmd",
            ".json",
            ".yaml",
            ".yml",
            ".xml",
            ".toml",
            ".ini",
            ".cfg",
            ".conf",
            ".properties",
            ".env",
            ".gitignore",
            ".dockerignore",
            ".editorconfig",
        }
    )
    _TEXT_ENCODINGS = ("utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "gb18030", "gbk")

    def supports_suffix(self, suffix: str) -> bool:
        return suffix.lower() in self.TEXT_FILE_EXTENSIONS

    def can_parse(self, source: FileSource) -> bool:
        return not self._looks_binary(source.path.read_bytes())

    def parse(self, source: FileSource) -> ParsedContent:
        payload = source.path.read_bytes()
        if self._looks_binary(payload):
            raise ValueError(f"{source.filename} does not look like a text file")

        text, encoding = self._decode_bytes(payload)
        title = source.filename.rsplit(".", 1)[0] or source.filename
        return ParsedContent(
            item_type="document",
            title=title,
            raw_content=text,
            normalized_text=text.strip(),
            metadata={
                "original_file_name": source.filename,
                "file_suffix": Path(source.filename).suffix.lower(),
                "text_encoding": encoding,
            },
        )

    @classmethod
    def _decode_bytes(cls, payload: bytes) -> tuple[str, str]:
        for encoding in cls._TEXT_ENCODINGS:
            try:
                return payload.decode(encoding), encoding
            except UnicodeDecodeError:
                continue
        raise UnicodeDecodeError("text", payload, 0, len(payload), "Unable to decode text file with known encodings")

    @staticmethod
    def _looks_binary(payload: bytes) -> bool:
        if not payload:
            return False
        sample = payload[:4096]
        if b"\x00" in sample:
            return True
        control_bytes = sum(1 for byte in sample if byte < 32 and byte not in {9, 10, 12, 13})
        return (control_bytes / max(len(sample), 1)) > 0.3
