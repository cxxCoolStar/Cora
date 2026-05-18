from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from core.agent.skill_loader import SkillLoader
from core.tools.file_tools import FileToolStore
from core.user_memory import UserMemoryStore

if TYPE_CHECKING:
    from core.tools import ToolInvocation


@dataclass(slots=True)
class DomainToolReply:
    reply: str
    action: str


@dataclass(slots=True)
class UserMemoryToolHandler:
    store: UserMemoryStore

    @classmethod
    def from_path(cls, path: Path) -> "UserMemoryToolHandler":
        return cls(store=UserMemoryStore(path))

    def execute(self, invocation: "ToolInvocation") -> DomainToolReply:
        action = str(invocation.plan.arguments.get("action") or "").strip()
        try:
            if action == "read":
                return DomainToolReply(reply=self.store.render(), action="memory")
            if action == "add":
                content = str(invocation.plan.arguments.get("content") or "").strip()
                return DomainToolReply(reply=self.store.add(content), action="memory")
            if action == "replace":
                old_text = str(invocation.plan.arguments.get("old_text") or "").strip()
                new_content = str(invocation.plan.arguments.get("new_content") or "").strip()
                return DomainToolReply(
                    reply=self.store.replace(old_text, new_content),
                    action="memory",
                )
            if action == "remove":
                old_text = str(invocation.plan.arguments.get("old_text") or "").strip()
                return DomainToolReply(reply=self.store.remove(old_text), action="memory")
        except ValueError as exc:
            return DomainToolReply(reply=str(exc), action="memory")
        return DomainToolReply(reply="我暂时还不能处理这个 user_memory 动作。", action="memory")


@dataclass(slots=True)
class FileToolHandler:
    store: FileToolStore

    @classmethod
    def from_root(cls, root: Path) -> "FileToolHandler":
        return cls(store=FileToolStore(root))

    def list_files(self, invocation: "ToolInvocation") -> DomainToolReply:
        try:
            reply = self.store.list_files(
                path=str(invocation.plan.arguments.get("path") or "."),
                recursive=bool(invocation.plan.arguments.get("recursive") or False),
                max_results=int(invocation.plan.arguments.get("max_results") or 50),
                include_hidden=bool(invocation.plan.arguments.get("include_hidden") or False),
            )
        except ValueError as exc:
            return DomainToolReply(reply=str(exc), action="inspect")
        return DomainToolReply(reply=reply, action="inspect")

    def search_files(self, invocation: "ToolInvocation") -> DomainToolReply:
        try:
            reply = self.store.search_files(
                query=str(invocation.plan.arguments.get("query") or ""),
                path=str(invocation.plan.arguments.get("path") or "."),
                file_pattern=str(invocation.plan.arguments.get("file_pattern") or "").strip() or None,
                case_sensitive=bool(invocation.plan.arguments.get("case_sensitive") or False),
                max_results=int(invocation.plan.arguments.get("max_results") or 20),
                include_hidden=bool(invocation.plan.arguments.get("include_hidden") or False),
            )
        except ValueError as exc:
            return DomainToolReply(reply=str(exc), action="inspect")
        return DomainToolReply(reply=reply, action="inspect")

    def read_file(self, invocation: "ToolInvocation") -> DomainToolReply:
        try:
            reply = self.store.read_file(
                path=str(invocation.plan.arguments.get("path") or ""),
                start_line=int(invocation.plan.arguments.get("start_line") or 1),
                end_line=(
                    int(invocation.plan.arguments["end_line"])
                    if invocation.plan.arguments.get("end_line") is not None
                    else None
                ),
            )
        except ValueError as exc:
            return DomainToolReply(reply=str(exc), action="inspect")
        return DomainToolReply(reply=reply, action="inspect")

    def write_file(self, invocation: "ToolInvocation") -> DomainToolReply:
        try:
            reply = self.store.write_file(
                path=str(invocation.plan.arguments.get("path") or ""),
                content=str(invocation.plan.arguments.get("content") or ""),
                append=bool(invocation.plan.arguments.get("append") or False),
            )
        except ValueError as exc:
            return DomainToolReply(reply=str(exc), action="edit")
        return DomainToolReply(reply=reply, action="edit")


@dataclass(slots=True)
class SkillToolHandler:
    loader: SkillLoader

    @classmethod
    def from_roots(cls, roots: list[Path] | None = None) -> "SkillToolHandler":
        return cls(loader=SkillLoader(skill_roots=roots))

    def list_skills(self, invocation: "ToolInvocation") -> DomainToolReply:
        category_filter = str(invocation.plan.arguments.get("category") or "").strip()
        skills = self.loader.list_skills()
        if category_filter:
            skills = [skill for skill in skills if (skill.category or "") == category_filter]
        if not skills:
            if category_filter:
                return DomainToolReply(
                    reply=f"没有找到分类为 {category_filter} 的可用 skills。",
                    action="skill",
                )
            return DomainToolReply(reply="当前没有可用 skills。", action="skill")

        lines = ["Available skills:"]
        for skill in skills:
            description = skill.description or "No description provided."
            if skill.category:
                lines.append(f"- {skill.name} [{skill.category}]: {description}")
            else:
                lines.append(f"- {skill.name}: {description}")
        lines.append("Use skill_view with the exact skill name to load the full instructions.")
        return DomainToolReply(reply="\n".join(lines), action="skill")

    def view_skill(self, invocation: "ToolInvocation") -> DomainToolReply:
        name = str(invocation.plan.arguments.get("name") or "").strip()
        file_path = str(invocation.plan.arguments.get("file_path") or "").strip() or None
        if not name:
            return DomainToolReply(reply="skill_view 需要提供 skill 名称。", action="skill")

        try:
            viewed = self.loader.view_skill(name=name, file_path=file_path)
        except ValueError as exc:
            return DomainToolReply(reply=str(exc), action="skill")
        if viewed is None:
            return DomainToolReply(reply=f"没有找到名为 {name} 的 skill。", action="skill")

        if viewed.file_path:
            reply = (
                f"Skill file: {viewed.skill.name} / {viewed.file_path}\n"
                f"Path: {viewed.absolute_path}\n\n"
                f"{viewed.content}"
            )
            return DomainToolReply(reply=reply, action="skill")

        lines = [
            f"Skill: {viewed.skill.name}",
            f"Path: {viewed.absolute_path}",
        ]
        if viewed.skill.description:
            lines.append(f"Description: {viewed.skill.description}")
        if viewed.skill.category:
            lines.append(f"Category: {viewed.skill.category}")
        lines.extend(["", viewed.content])
        if viewed.skill.linked_files:
            lines.extend(["", "Supporting files:"])
            for section in ("references", "templates", "assets", "scripts"):
                for linked_file in viewed.skill.linked_files.get(section, []):
                    lines.append(f"- {linked_file}")
            lines.append("Use skill_view again with file_path to load one of these files.")
        return DomainToolReply(reply="\n".join(lines), action="skill")
