from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.agent.skill_loader import SkillLoader
from core.clawbot.planner import ToolPlan
from core.clawbot.tool_domains import SkillToolHandler
from core.tools.registry import ToolInvocation


@dataclass
class _InvocationFactory:
    skill_root: Path

    def build(self, *, tool: str, arguments: dict[str, object]) -> ToolInvocation:
        return ToolInvocation(
            session_id="session-skills",
            source_message_id="message-skills",
            plan=ToolPlan(
                tool=tool,
                arguments=arguments,
                reason="test",
                source="test",
            ),
            text=None,
            upload=None,
            context={},
        )


def _make_skill(
    root: Path,
    *,
    name: str,
    description: str,
    category: str | None = None,
    body: str = "Follow these steps carefully.",
) -> Path:
    skill_dir = root / name if category is None else root / category / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                f"name: {name}",
                f"description: {description}",
                "---",
                "",
                f"# {name}",
                "",
                body,
            ]
        ),
        encoding="utf-8",
    )
    return skill_dir


def test_skill_loader_collects_linked_files(tmp_path: Path) -> None:
    skill_dir = _make_skill(
        tmp_path,
        name="archive-core",
        description="Archive workflow guidance.",
        category="archive",
    )
    (skill_dir / "references").mkdir()
    (skill_dir / "references" / "notes.md").write_text("Reference", encoding="utf-8")
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "save_content.py").write_text("print('ok')", encoding="utf-8")

    loader = SkillLoader(skill_roots=[tmp_path])
    skill = loader.find_skill("archive-core")

    assert skill is not None
    assert skill.category == "archive"
    assert skill.linked_files["references"] == ["references/notes.md"]
    assert skill.linked_files["scripts"] == ["scripts/save_content.py"]


def test_skill_tool_handler_lists_and_views_skills(tmp_path: Path) -> None:
    skill_dir = _make_skill(
        tmp_path,
        name="archive-core",
        description="Archive workflow guidance.",
        category="archive",
        body="Use the archive workflow.",
    )
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "search_content.py").write_text("print('search')", encoding="utf-8")

    handler = SkillToolHandler.from_roots([tmp_path])
    invocation_factory = _InvocationFactory(skill_root=tmp_path)

    listed = handler.list_skills(
        invocation_factory.build(tool="skills_list", arguments={})
    )
    assert "Available skills:" in listed.reply
    assert "archive-core [archive]" in listed.reply

    viewed = handler.view_skill(
        invocation_factory.build(tool="skill_view", arguments={"name": "archive-core"})
    )
    assert "Skill: archive-core" in viewed.reply
    assert "Supporting files:" in viewed.reply
    assert "scripts/search_content.py" in viewed.reply

    script_view = handler.view_skill(
        invocation_factory.build(
            tool="skill_view",
            arguments={"name": "archive-core", "file_path": "scripts/search_content.py"},
        )
    )
    assert "Skill file: archive-core / scripts/search_content.py" in script_view.reply
    assert "print('search')" in script_view.reply


def test_skill_tool_handler_blocks_path_traversal(tmp_path: Path) -> None:
    _make_skill(
        tmp_path,
        name="archive-core",
        description="Archive workflow guidance.",
    )
    handler = SkillToolHandler.from_roots([tmp_path])
    invocation_factory = _InvocationFactory(skill_root=tmp_path)

    viewed = handler.view_skill(
        invocation_factory.build(
            tool="skill_view",
            arguments={"name": "archive-core", "file_path": "../secret.txt"},
        )
    )

    assert "must stay within the skill directory" in viewed.reply


def test_real_archive_core_skill_exposes_runtime_contract_reference() -> None:
    loader = SkillLoader()
    skill = loader.find_skill("archive-core")

    assert skill is not None
    assert "references/runtime-contract.md" in skill.linked_files["references"]
    assert skill.runtime_metadata["entrypoint"] == "scripts/archive_dispatch.py"
    assert skill.runtime_metadata["required_input_fields"] == ["intent"]

    viewed = loader.view_skill(name="archive-core", file_path="references/runtime-contract.md")

    assert viewed is not None
    assert "Archive Core Runtime Contract" in viewed.content
    assert "sqlite:///C:/full/path/to/.cora/clawbot.db" in viewed.content
