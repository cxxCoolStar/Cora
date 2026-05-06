from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.archivefs.service import ArchiveImageWorkflow, ArchiveSkillScriptRunner
from core.ingestion.parsers.image_parser import ImageFileParser


@dataclass
class StubDescriber:
    description: str

    def describe_image(self, *, image_path: Path, mime_type: str) -> str:
        return self.description


def test_archive_skill_script_runner_saves_and_finds_asset(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    source_file = tmp_path / "note.txt"
    source_file.write_text("hello archive", encoding="utf-8")

    runner = ArchiveSkillScriptRunner(archive_root=archive_root)
    saved = runner.save_asset(
        source_file=source_file,
        topic="documents",
        asset_type="document",
        summary="hello archive",
        description="plain text note",
        source="test",
    )

    assert saved.topic == "documents"
    assert saved.stored_path.is_file()
    assert saved.record.asset_type == "document"

    found = runner.find_assets(query="plain text")
    assert found.count == 1
    assert found.results[0]["id"] == saved.record.id
    assert found.results[0]["file_exists"] is True


def test_archive_image_workflow_parses_then_saves_image(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    image_path = tmp_path / "wechat_image.jpg"
    image_path.write_bytes(b"fake-image-bytes")

    parser = ImageFileParser(
        describer=StubDescriber(
            description="A portrait photo in a garden with a young woman."
        )
    )
    runner = ArchiveSkillScriptRunner(archive_root=archive_root)
    workflow = ArchiveImageWorkflow(image_parser=parser, archive_runner=runner)

    saved = workflow.save_image(
        image_path=image_path,
        topic="personal-photos",
        source="wechat",
        user_note="Taken at Zhujiang Park",
    )

    assert saved.topic == "personal-photos"
    assert saved.record.asset_type == "image"
    assert saved.record.summary == "wechat_image"
    assert "portrait photo" in saved.record.description.lower()
