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
    frontmatter: dict[str, Any] = field(default_factory=dict)


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
                content = skill_md.read_text(encoding="utf-8")
                frontmatter, body = _parse_frontmatter(content)
                if not _matches_platform(frontmatter, platform=platform):
                    continue
                name = str(frontmatter.get("name") or skill_md.parent.name).strip()
                description = str(frontmatter.get("description") or "").strip()
                loaded.append(
                    SkillDefinition(
                        name=name,
                        description=description,
                        path=skill_md.parent.resolve(),
                        content=body.strip(),
                        frontmatter=frontmatter,
                    )
                )
        return loaded

    def find_skill(self, name: str, *, platform: str | None = None) -> SkillDefinition | None:
        needle = (name or "").strip()
        if not needle:
            return None
        for skill in self.list_skills(platform=platform):
            if skill.name == needle:
                return skill
        return None
