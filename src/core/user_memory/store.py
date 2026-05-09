from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


DEFAULT_USER_MEMORY_TEMPLATE = "# User Memory\n"


@dataclass(slots=True)
class UserMemoryStore:
    path: Path

    def read_text(self) -> str:
        if not self.path.exists():
            return ""
        return self.path.read_text(encoding="utf-8").strip()

    def ensure_exists(self) -> None:
        if self.path.exists():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(DEFAULT_USER_MEMORY_TEMPLATE, encoding="utf-8")

    def read_entries(self) -> list[str]:
        text = self.read_text()
        if not text:
            return []
        entries: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("- "):
                entries.append(stripped[2:].strip())
        return entries

    def add(self, content: str) -> str:
        cleaned = self._normalize_entry(content)
        if not cleaned:
            raise ValueError("content cannot be empty")
        self.ensure_exists()
        entries = self.read_entries()
        if cleaned in entries:
            return "这条个人记忆已经存在，无需重复添加。"
        entries.append(cleaned)
        self._write_entries(entries)
        return f"已记住：{cleaned}"

    def replace(self, old_text: str, new_content: str) -> str:
        needle = old_text.strip()
        cleaned = self._normalize_entry(new_content)
        if not needle:
            raise ValueError("old_text cannot be empty")
        if not cleaned:
            raise ValueError("new_content cannot be empty")
        entries = self.read_entries()
        idx = self._find_entry_index(entries, needle)
        if idx is None:
            raise ValueError(f"没有找到包含“{needle}”的个人记忆。")
        entries[idx] = cleaned
        self._write_entries(entries)
        return f"已更新个人记忆：{cleaned}"

    def remove(self, old_text: str) -> str:
        needle = old_text.strip()
        if not needle:
            raise ValueError("old_text cannot be empty")
        entries = self.read_entries()
        idx = self._find_entry_index(entries, needle)
        if idx is None:
            raise ValueError(f"没有找到包含“{needle}”的个人记忆。")
        removed = entries.pop(idx)
        self._write_entries(entries)
        return f"已删除个人记忆：{removed}"

    def render(self) -> str:
        text = self.read_text()
        if text:
            return text
        self.ensure_exists()
        return self.read_text()

    def _write_entries(self, entries: list[str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lines = ["# User Memory", ""]
        lines.extend(f"- {entry}" for entry in entries)
        self.path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")

    @staticmethod
    def _normalize_entry(content: str) -> str:
        cleaned = content.strip()
        if cleaned.startswith("- "):
            cleaned = cleaned[2:].strip()
        return cleaned

    @staticmethod
    def _find_entry_index(entries: list[str], needle: str) -> int | None:
        matches = [index for index, entry in enumerate(entries) if needle in entry]
        if not matches:
            return None
        if len(matches) > 1:
            raise ValueError(f"找到多条包含“{needle}”的个人记忆，请给更具体的 old_text。")
        return matches[0]
