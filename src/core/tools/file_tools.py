from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


DEFAULT_LIST_LIMIT = 50
DEFAULT_SEARCH_LIMIT = 20
MAX_LIST_LIMIT = 200
MAX_SEARCH_LIMIT = 50
MAX_READ_LINES = 200
MAX_MATCH_LINE_LENGTH = 200


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
            raise ValueError(f"路径不存在：{path}")
        if not target.is_dir():
            raise ValueError(f"路径不是目录：{path}")

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
            return f"目录 `{relative_target}` 下没有可展示的内容。"

        lines = [f"目录 `{relative_target}` 下的内容："]
        lines.extend(f"- [{kind}] {name}" for kind, name in entries)
        if len(entries) >= limit:
            lines.append(f"- 结果已截断到前 {limit} 条。")
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
            raise ValueError(f"路径不存在：{path}")

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

        if not matches:
            return f"没有在 `{target.relative_to(self._resolved_root()).as_posix() or '.'}` 下找到与 `{needle}` 相关的结果。"

        lines = [f"与 `{needle}` 相关的结果："]
        lines.extend(matches)
        if len(matches) >= limit:
            lines.append(f"- 结果已截断到前 {limit} 条。")
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
            raise ValueError(f"文件不存在：{path}")
        if not target.is_file():
            raise ValueError(f"路径不是文件：{path}")

        text = self._read_text_file(target)
        if text is None:
            raise ValueError(f"文件不是可直接阅读的文本格式：{path}")

        if start_line < 1:
            raise ValueError("start_line must be >= 1")
        if end_line is not None and end_line < start_line:
            raise ValueError("end_line must be >= start_line")

        lines = text.splitlines()
        total_lines = len(lines)
        if total_lines == 0:
            return f"文件 `{target.relative_to(self._resolved_root()).as_posix()}` 是空的。"

        slice_end = min(
            end_line if end_line is not None else start_line + MAX_READ_LINES - 1,
            start_line + MAX_READ_LINES - 1,
            total_lines,
        )
        selected = lines[start_line - 1 : slice_end]
        relative_path = target.relative_to(self._resolved_root()).as_posix()
        rendered = "\n".join(
            f"{line_number}: {content}"
            for line_number, content in enumerate(selected, start=start_line)
        )
        lines_out = [f"文件 `{relative_path}` 第 {start_line}-{slice_end} 行（共 {total_lines} 行）：", rendered]
        if slice_end < total_lines and (end_line is None or end_line > slice_end):
            lines_out.append(f"... 已截断，继续读取请从第 {slice_end + 1} 行开始。")
        return "\n".join(lines_out)

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
            raise ValueError("路径超出了文件工具允许访问的工作区范围。") from exc
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
