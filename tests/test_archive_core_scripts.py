from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_SCRIPTS_DIR = REPO_ROOT / "skills" / "archive-core" / "scripts"
SAVE_SCRIPT = SKILL_SCRIPTS_DIR / "save_asset.py"
FIND_SCRIPT = SKILL_SCRIPTS_DIR / "find_asset.py"
UPDATE_SCRIPT = SKILL_SCRIPTS_DIR / "update_index.py"


def _run_script(script: Path, *args: str) -> dict:
    completed = subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(completed.stdout)


def test_save_asset_creates_topic_file_and_index_record(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    source_file = tmp_path / "wechat_image.jpg"
    source_file.write_bytes(b"fake-image-bytes")

    result = _run_script(
        SAVE_SCRIPT,
        "--archive-root",
        str(archive_root),
        "--source-file",
        str(source_file),
        "--topic",
        "personal-photos",
        "--type",
        "image",
        "--summary",
        "Portrait at Zhujiang Park",
        "--description",
        "A portrait photo in a garden scene",
        "--source",
        "wechat",
        "--user-note",
        "My girlfriend at Zhujiang Park",
    )

    assert result["success"] is True
    stored_path = Path(result["stored_path"])
    assert stored_path.is_file()
    assert stored_path.parent.name == "personal-photos"

    index_path = archive_root / "logs" / "archive_index.jsonl"
    lines = index_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["topic"] == "personal-photos"
    assert record["type"] == "image"
    assert record["filename"] == stored_path.name


def test_find_asset_returns_matching_record(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    source_file = tmp_path / "wechat_image.jpg"
    source_file.write_bytes(b"fake-image-bytes")

    save_result = _run_script(
        SAVE_SCRIPT,
        "--archive-root",
        str(archive_root),
        "--source-file",
        str(source_file),
        "--topic",
        "personal-photos",
        "--type",
        "image",
        "--summary",
        "Portrait at Zhujiang Park",
        "--description",
        "A portrait photo in a garden scene",
        "--source",
        "wechat",
    )

    find_result = _run_script(
        FIND_SCRIPT,
        "--archive-root",
        str(archive_root),
        "--query",
        "zhujiang park",
    )

    assert find_result["success"] is True
    assert find_result["count"] == 1
    first = find_result["results"][0]
    assert first["id"] == save_result["record"]["id"]
    assert first["topic"] == "personal-photos"
    assert first["file_exists"] is True


def test_update_index_appends_record_from_json_file(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    record_path = tmp_path / "record.json"
    record_path.write_text(
        json.dumps(
            {
                "id": "img_manual_001",
                "type": "image",
                "topic": "personal-photos",
                "path": "topics/personal-photos/manual.jpg",
                "filename": "manual.jpg",
                "summary": "Manual record",
                "description": "Inserted through update_index.py",
                "source": "test",
                "user_note": "",
                "created_at": "2026-05-06T10:00:00+08:00",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = _run_script(
        UPDATE_SCRIPT,
        "--archive-root",
        str(archive_root),
        "--record-file",
        str(record_path),
    )

    assert result["success"] is True
    assert result["record_id"] == "img_manual_001"

    index_path = archive_root / "logs" / "archive_index.jsonl"
    lines = index_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["id"] == "img_manual_001"
