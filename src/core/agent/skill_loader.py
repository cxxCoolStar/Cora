from __future__ import annotations

import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


_PLATFORM_MAP = {
    "macos": "darwin",
    "linux": "linux",
    "windows": "win32",
}


@dataclass(slots=True)
class SkillDefinition:
    name: str
    description: str
    path: Path
    content: str
    raw_content: str
    category: str | None = None
    linked_files: dict[str, list[str]] = field(default_factory=dict)
    frontmatter: dict[str, Any] = field(default_factory=dict)
    runtime_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SkillFileView:
    skill: SkillDefinition
    file_path: str | None
    content: str
    absolute_path: Path


def _parse_scalar(value: str) -> Any:
    text = value.strip()
    if not text:
        return ""
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if inner and "'" not in inner and '"' not in inner:
            return [part.strip() for part in inner.split(",") if part.strip()]
        try:
            parsed = ast.literal_eval(text)
        except (SyntaxError, ValueError):
            return text
        if isinstance(parsed, list):
            return parsed
        return text
    if text.lower() in {"true", "false"}:
        return text.lower() == "true"
    return text


def _parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    if not content.startswith("---\n"):
        return {}, content
    lines = content.splitlines()
    frontmatter: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(0, frontmatter)]
    idx = 1
    while idx < len(lines):
        line = lines[idx]
        if line.strip() == "---":
            body = "\n".join(lines[idx + 1 :]).lstrip("\n")
            return frontmatter, body
        if not line.strip():
            idx += 1
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if ":" not in stripped:
            idx += 1
            continue
        key, raw_value = stripped.split(":", 1)
        value = raw_value.strip()
        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        current = stack[-1][1]
        if value == "":
            child: dict[str, Any] = {}
            current[key] = child
            stack.append((indent, child))
        else:
            current[key] = _parse_scalar(value)
        idx += 1
    return {}, content


def _matches_platform(frontmatter: dict[str, Any], platform: str | None = None) -> bool:
    platforms = frontmatter.get("platforms")
    if not platforms:
        return True
    if isinstance(platforms, str):
        platforms = [platforms]
    current = platform or sys.platform
    for candidate in platforms:
        normalized = _PLATFORM_MAP.get(str(candidate).strip().lower(), str(candidate).strip().lower())
        if current.startswith(normalized):
            return True
    return False


class SkillLoader:
    def __init__(self, *, skill_roots: list[Path] | None = None) -> None:
        if skill_roots is None:
            repo_root = Path(__file__).resolve().parents[3]
            skill_roots = [repo_root / "skills"]
        self.skill_roots = skill_roots

    def list_skills(self, *, platform: str | None = None) -> list[SkillDefinition]:
        loaded: list[SkillDefinition] = []
        for root in self.skill_roots:
            if not root.exists():
                continue
            for skill_md in sorted(root.rglob("SKILL.md")):
                if any(part in {".git", ".github", ".hub"} for part in skill_md.parts):
                    continue
                content = skill_md.read_text(encoding="utf-8")
                frontmatter, body = _parse_frontmatter(content)
                if not _matches_platform(frontmatter, platform=platform):
                    continue
                name = str(frontmatter.get("name") or skill_md.parent.name).strip()
                description = str(frontmatter.get("description") or "").strip()
                category = self._category_for_skill(root=root, skill_dir=skill_md.parent)
                loaded.append(
                    SkillDefinition(
                        name=name,
                        description=description,
                        path=skill_md.parent.resolve(),
                        content=body.strip(),
                        raw_content=content,
                        category=category,
                        linked_files=self._collect_linked_files(skill_md.parent),
                        frontmatter=frontmatter,
                        runtime_metadata=self._runtime_metadata(frontmatter),
                    )
                )
        return loaded

    def find_skill(self, name: str, *, platform: str | None = None) -> SkillDefinition | None:
        needle = (name or "").strip()
        if not needle:
            return None
        for skill in self.list_skills(platform=platform):
            if skill.name == needle or self._matches_relative_skill_path(skill=skill, needle=needle):
                return skill
        return None

    def view_skill(
        self,
        name: str,
        *,
        file_path: str | None = None,
        platform: str | None = None,
    ) -> SkillFileView | None:
        skill = self.find_skill(name, platform=platform)
        if skill is None:
            return None
        if not file_path:
            return SkillFileView(
                skill=skill,
                file_path=None,
                content=skill.content,
                absolute_path=(skill.path / "SKILL.md").resolve(),
            )
        normalized = Path(file_path)
        if normalized.is_absolute() or ".." in normalized.parts:
            raise ValueError("Skill file_path must stay within the skill directory.")
        target = (skill.path / normalized).resolve()
        try:
            target.relative_to(skill.path.resolve())
        except ValueError as exc:
            raise ValueError("Skill file_path must stay within the skill directory.") from exc
        if not target.exists() or not target.is_file():
            raise ValueError(f"Skill file not found: {file_path}")
        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"Skill file is not readable text: {file_path}") from exc
        return SkillFileView(
            skill=skill,
            file_path=str(normalized).replace("\\", "/"),
            content=content,
            absolute_path=target,
        )

    @staticmethod
    def _matches_relative_skill_path(*, skill: SkillDefinition, needle: str) -> bool:
        parts = [part for part in skill.path.parts if part]
        if not parts:
            return False
        tail = "/".join(parts[-2:]) if len(parts) >= 2 else parts[-1]
        return tail == needle.strip().replace("\\", "/")

    @staticmethod
    def _category_for_skill(*, root: Path, skill_dir: Path) -> str | None:
        try:
            relative_parent = skill_dir.relative_to(root).parent
        except ValueError:
            return None
        if not relative_parent.parts:
            return None
        return "/".join(relative_parent.parts)

    @staticmethod
    def _collect_linked_files(skill_dir: Path) -> dict[str, list[str]]:
        linked: dict[str, list[str]] = {}
        for section in ("references", "templates", "assets", "scripts"):
            section_dir = skill_dir / section
            if not section_dir.exists():
                continue
            files = [
                str(path.relative_to(skill_dir)).replace("\\", "/")
                for path in sorted(section_dir.rglob("*"))
                if path.is_file()
            ]
            if files:
                linked[section] = files
        return linked

    @staticmethod
    def _runtime_metadata(frontmatter: dict[str, Any]) -> dict[str, Any]:
        metadata = frontmatter.get("metadata")
        if not isinstance(metadata, dict):
            return {}
        cora = metadata.get("cora")
        if not isinstance(cora, dict):
            return {}
        runtime = cora.get("runtime")
        return runtime if isinstance(runtime, dict) else {}
