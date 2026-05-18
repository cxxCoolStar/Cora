from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


DEFAULT_LIST_LIMIT = 50
DEFAULT_SEARCH_LIMIT = 20
MAX_LIST_LIMIT = 200
MAX_SEARCH_LIMIT = 50
MAX_READ_LINES = 200
MAX_MATCH_LINE_LENGTH = 200
MAX_WRITE_CHARS = 200_000


@dataclass(slots=True)
class FileToolStore:
    root: Path

    def list_files(
        self,
        *,
        path: str = ".",
        recursive: bool = False,
        max_results: int = DEFAULT_LIST_LIMIT,
        include_hidden: bool = False,
    ) -> str:
        limit = max(1, min(max_results, MAX_LIST_LIMIT))
        target = self._resolve_path(path)
        if not target.exists():
            raise ValueError(f"path does not exist: {path}")
        if not target.is_dir():
            raise ValueError(f"path is not a directory: {path}")

        entries: list[tuple[str, str]] = []
        iterator = target.rglob("*") if recursive else target.iterdir()
        for candidate in sorted(iterator, key=lambda item: (not item.is_dir(), str(item).lower())):
            relative_candidate = candidate.relative_to(self._resolved_root())
            if not include_hidden and self._is_hidden(relative_candidate):
                continue
            kind = "dir" if candidate.is_dir() else "file"
            entries.append((kind, relative_candidate.as_posix()))
            if len(entries) >= limit:
                break

        relative_target = target.relative_to(self._resolved_root()).as_posix() or "."
        if not entries:
            return f"Directory `{relative_target}` is empty."

        lines = [f"Contents of `{relative_target}`:"]
        lines.extend(f"- [{kind}] {name}" for kind, name in entries)
        if len(entries) >= limit:
            lines.append(f"- Results truncated to the first {limit} entries.")
        return "\n".join(lines)

    def search_files(
        self,
        *,
        query: str,
        path: str = ".",
        file_pattern: str | None = None,
        case_sensitive: bool = False,
        max_results: int = DEFAULT_SEARCH_LIMIT,
        include_hidden: bool = False,
    ) -> str:
        needle = query.strip()
        if not needle:
            raise ValueError("query cannot be empty")
        limit = max(1, min(max_results, MAX_SEARCH_LIMIT))
        target = self._resolve_path(path)
        if not target.exists():
            raise ValueError(f"path does not exist: {path}")

        matches: list[str] = []
        normalized_needle = needle if case_sensitive else needle.lower()
        for file_path in self._iter_files(target=target, include_hidden=include_hidden):
            relative_path = file_path.relative_to(self._resolved_root()).as_posix()
            if file_pattern and not file_path.match(file_pattern):
                continue

            filename_haystack = relative_path if case_sensitive else relative_path.lower()
            if normalized_needle in filename_haystack:
                matches.append(f"- {relative_path} (filename match)")
                if len(matches) >= limit:
                    break

            text = self._read_text_file(file_path)
            if text is None:
                continue
            for line_number, line in enumerate(text.splitlines(), start=1):
                haystack = line if case_sensitive else line.lower()
                if normalized_needle not in haystack:
                    continue
                snippet = line.strip()
                if len(snippet) > MAX_MATCH_LINE_LENGTH:
                    snippet = snippet[: MAX_MATCH_LINE_LENGTH - 3] + "..."
                matches.append(f"- {relative_path}:{line_number}: {snippet}")
                if len(matches) >= limit:
                    break
            if len(matches) >= limit:
                break

        relative_target = target.relative_to(self._resolved_root()).as_posix() or "."
        if not matches:
            return f"No matches for `{needle}` under `{relative_target}`."

        lines = [f"Matches for `{needle}`:"]
        lines.extend(matches)
        if len(matches) >= limit:
            lines.append(f"- Results truncated to the first {limit} matches.")
        return "\n".join(lines)

    def read_file(
        self,
        *,
        path: str,
        start_line: int = 1,
        end_line: int | None = None,
    ) -> str:
        target = self._resolve_path(path)
        if not target.exists():
            raise ValueError(f"file does not exist: {path}")
        if not target.is_file():
            raise ValueError(f"path is not a file: {path}")

        text = self._read_text_file(target)
        if text is None:
            raise ValueError(f"file is not readable text: {path}")

        if start_line < 1:
            raise ValueError("start_line must be >= 1")
        if end_line is not None and end_line < start_line:
            raise ValueError("end_line must be >= start_line")

        lines = text.splitlines()
        total_lines = len(lines)
        relative_path = target.relative_to(self._resolved_root()).as_posix()
        if total_lines == 0:
            return f"File `{relative_path}` is empty."

        slice_end = min(
            end_line if end_line is not None else start_line + MAX_READ_LINES - 1,
            start_line + MAX_READ_LINES - 1,
            total_lines,
        )
        selected = lines[start_line - 1 : slice_end]
        rendered = "\n".join(
            f"{line_number}: {content}"
            for line_number, content in enumerate(selected, start=start_line)
        )
        lines_out = [f"File `{relative_path}` lines {start_line}-{slice_end} of {total_lines}:", rendered]
        if slice_end < total_lines and (end_line is None or end_line > slice_end):
            lines_out.append(f"... truncated; continue from line {slice_end + 1}.")
        return "\n".join(lines_out)

    def write_file(
        self,
        *,
        path: str,
        content: str,
        append: bool = False,
    ) -> str:
        cleaned_path = path.strip()
        if not cleaned_path:
            raise ValueError("path cannot be empty")
        if len(content) > MAX_WRITE_CHARS:
            raise ValueError(f"content is too large; limit is {MAX_WRITE_CHARS} characters")

        target = self._resolve_path(cleaned_path)
        if target.exists() and target.is_dir():
            raise ValueError(f"path is a directory, not a file: {path}")

        target.parent.mkdir(parents=True, exist_ok=True)
        if append:
            with target.open("a", encoding="utf-8", newline="") as handle:
                handle.write(content)
        else:
            target.write_text(content, encoding="utf-8", newline="")

        relative_path = target.relative_to(self._resolved_root()).as_posix()
        line_count = len(content.splitlines()) or (1 if content else 0)
        verb = "Appended to" if append else "Wrote"
        return f"{verb} `{relative_path}` ({len(content)} chars, {line_count} lines)."

    def _resolve_path(self, path: str) -> Path:
        cleaned = path.strip() or "."
        root = self._resolved_root()
        candidate = Path(cleaned)
        if not candidate.is_absolute():
            candidate = root / candidate
        candidate = candidate.resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError("path escapes the allowed workspace root") from exc
        return candidate

    def _iter_files(self, *, target: Path, include_hidden: bool) -> list[Path]:
        if target.is_file():
            return [target]
        files: list[Path] = []
        for candidate in sorted(target.rglob("*"), key=lambda item: str(item).lower()):
            if not candidate.is_file():
                continue
            relative_candidate = candidate.relative_to(self._resolved_root())
            if not include_hidden and self._is_hidden(relative_candidate):
                continue
            files.append(candidate)
        return files

    def _read_text_file(self, path: Path) -> str | None:
        raw = path.read_bytes()
        if b"\x00" in raw[:1024]:
            return None
        return raw.decode("utf-8", errors="replace")

    def _resolved_root(self) -> Path:
        return self.root.resolve()

    @staticmethod
    def _is_hidden(path: Path) -> bool:
        return any(part.startswith(".") for part in path.parts if part not in {".", ".."})
