from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SKILL_ROOT = Path(__file__).resolve().parents[1] / "skills" / "archive-core"
if str(_SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(_SKILL_ROOT))

from core.agent.skill_executor import SkillScriptExecutor, SkillScriptRequest
from core.archive.cora_dispatch import run_dispatch
from core.archive.mirror import mirror_upload_file
from core.config import CoreSettings
from core.skills.bootstrap import bootstrap_host_skills
from core.skills.hooks import fire_item_saved_hooks
from core.skills.registry import is_registered


def test_run_dispatch_search_uses_db_or_file_archive(tmp_path: Path, monkeypatch) -> None:
    archive_root = tmp_path / "archive"
    db_path = tmp_path / "clawbot.db"
    files_dir = tmp_path / "files"
    files_dir.mkdir()
    monkeypatch.setenv("CORA_CLAWBOT_DATABASE_PATH", str(db_path))
    monkeypatch.setenv("CORA_FILES_STORAGE_DIR", str(files_dir))
    monkeypatch.setenv("CORA_ARCHIVE_ROOT_DIR", str(archive_root))
    monkeypatch.setenv("CORA_ARCHIVE_MIRROR_ENABLED", "true")

    source = files_dir / "resume.txt"
    source.write_text("my resume content for job", encoding="utf-8")
    mirror_upload_file(
        file_path=source,
        topic="documents",
        asset_type="document",
        summary="resume",
        description="my resume content for job",
    )

    result = run_dispatch(
        {
            "intent": "search",
            "session_id": "session-test",
            "source_message_id": "msg-1",
            "arguments": {"query": "resume"},
            "database_url": f"sqlite:///{db_path.as_posix()}",
        }
    )
    assert result["status"] == "completed"
    assert "resume" in result["message"].lower() or "resume" in str(result)


def test_bootstrap_registers_archive_hooks() -> None:
    bootstrap_host_skills()
    assert is_registered("archive-core")


def test_item_saved_hook_mirrors_file(tmp_path: Path, monkeypatch) -> None:
    archive_root = tmp_path / "archive"
    files_dir = tmp_path / "files"
    files_dir.mkdir()
    monkeypatch.setenv("CORA_ARCHIVE_ROOT_DIR", str(archive_root))
    monkeypatch.setenv("CORA_ARCHIVE_MIRROR_ENABLED", "true")
    from adapters.cora.settings import get_cora_archive_settings

    get_cora_archive_settings.cache_clear()
    bootstrap_host_skills()

    source = files_dir / "note.txt"
    source.write_text("hook mirror test", encoding="utf-8")

    class _Item:
        id = "item-hook-1"
        item_type = "document"
        title = "note"
        summary = "hook mirror test"
        metadata_json = {"stored_file_path": str(source), "topic_slug": "inbox"}

    fire_item_saved_hooks(item=_Item())
    index_path = archive_root / "logs" / "archive_index.jsonl"
    assert index_path.is_file()


def test_mirror_prefers_topic_slug_over_localized_name(tmp_path: Path, monkeypatch) -> None:
    archive_root = tmp_path / "archive"
    files_dir = tmp_path / "files"
    files_dir.mkdir()
    monkeypatch.setenv("CORA_ARCHIVE_ROOT_DIR", str(archive_root))
    monkeypatch.setenv("CORA_ARCHIVE_MIRROR_ENABLED", "true")
    from adapters.cora.mirror import mirror_item_record
    from adapters.cora.settings import get_cora_archive_settings

    get_cora_archive_settings.cache_clear()

    source = files_dir / "meal.jpg"
    source.write_bytes(b"fake-jpeg")

    class _Item:
        item_type = "image"
        title = "wechat_image"
        summary = "泰餐照片"
        metadata_json = {
            "stored_file_path": str(source),
            "topic_slug": "personal-photos",
            "topic_name": "个人照片",
            "user_note": "太美丽餐厅吃泰餐",
        }

    record = mirror_item_record(item=_Item())
    assert record is not None
    assert record["topic"] == "personal-photos"


def test_search_with_nested_arguments_finds_saved_note(tmp_path: Path, monkeypatch) -> None:
    archive_root = tmp_path / "archive"
    db_path = tmp_path / "clawbot.db"
    files_dir = tmp_path / "files"
    files_dir.mkdir()
    monkeypatch.setenv("CORA_CLAWBOT_DATABASE_PATH", str(db_path))
    monkeypatch.setenv("CORA_FILES_STORAGE_DIR", str(files_dir))
    monkeypatch.setenv("CORA_ARCHIVE_ROOT_DIR", str(archive_root))

    from adapters.cora._content_common import build_database
    from core.storage.repositories import ItemRepository

    database = build_database(f"sqlite:///{db_path.as_posix()}")
    ItemRepository(database).create(
        session_id="session-nested",
        source_message_id="msg-save",
        source_event_id=None,
        item_type="text_note",
        title="note",
        raw_content="EVAL_NESTED_DISPATCH_q1 peanut allergy",
        normalized_text="EVAL_NESTED_DISPATCH_q1 peanut allergy",
        summary="EVAL_NESTED_DISPATCH_q1 peanut allergy",
        metadata={"user_note": "EVAL_NESTED_DISPATCH_q1 peanut allergy"},
        locator_hint=None,
        document_key=None,
        version=1,
        is_current=1,
    )

    search = run_dispatch(
        {
            "intent": "search",
            "session_id": "session-nested",
            "source_message_id": "msg-search",
            "arguments": {"arguments": {"query": "EVAL_NESTED_DISPATCH_q1"}},
            "database_url": f"sqlite:///{db_path.as_posix()}",
        }
    )
    assert search["status"] == "completed"
    assert "EVAL_NESTED_DISPATCH_q1" in search["message"]


def test_deliver_falls_back_to_any_session(tmp_path: Path, monkeypatch) -> None:
    archive_root = tmp_path / "archive"
    db_path = tmp_path / "clawbot.db"
    files_dir = tmp_path / "files"
    files_dir.mkdir()
    monkeypatch.setenv("CORA_CLAWBOT_DATABASE_PATH", str(db_path))
    monkeypatch.setenv("CORA_FILES_STORAGE_DIR", str(files_dir))
    monkeypatch.setenv("CORA_ARCHIVE_ROOT_DIR", str(archive_root))

    source = files_dir / "deliver-me.txt"
    source.write_text("deliver fallback marker", encoding="utf-8")

    from adapters.cora._content_common import build_database
    from core.storage.repositories import ItemRepository

    database = build_database(f"sqlite:///{db_path.as_posix()}")
    items = ItemRepository(database)
    items.create(
        session_id="session-origin",
        source_message_id="msg-1",
        source_event_id=None,
        item_type="document",
        title="deliver-me",
        raw_content="deliver fallback marker",
        normalized_text="deliver fallback marker",
        summary="deliver fallback marker",
        metadata={
            "stored_file_path": str(source),
            "original_file_name": "deliver-me.txt",
            "user_note": "DELIVER_FALLBACK_x3",
        },
        locator_hint=None,
        document_key=None,
        version=1,
        is_current=1,
    )

    deliver = run_dispatch(
        {
            "intent": "deliver",
            "session_id": "session-other",
            "source_message_id": "msg-2",
            "arguments": {"query": "DELIVER_FALLBACK_x3"},
            "database_url": f"sqlite:///{db_path.as_posix()}",
        }
    )
    assert deliver["status"] == "completed"
    effects = deliver.get("effects") or []
    assert any(effect.get("kind") == "deliver_file" for effect in effects)


def test_deliver_accepts_item_refs_from_nested_arguments(tmp_path: Path, monkeypatch) -> None:
    archive_root = tmp_path / "archive"
    db_path = tmp_path / "clawbot.db"
    files_dir = tmp_path / "files"
    files_dir.mkdir()
    monkeypatch.setenv("CORA_CLAWBOT_DATABASE_PATH", str(db_path))
    monkeypatch.setenv("CORA_FILES_STORAGE_DIR", str(files_dir))
    monkeypatch.setenv("CORA_ARCHIVE_ROOT_DIR", str(archive_root))

    source = files_dir / "cake.jpg"
    source.write_bytes(b"fake-jpeg")

    from adapters.cora._content_common import build_database
    from core.storage.repositories import ItemRepository

    database = build_database(f"sqlite:///{db_path.as_posix()}")
    items = ItemRepository(database)
    item = items.create(
        session_id="session-1",
        source_message_id="msg-1",
        source_event_id=None,
        item_type="image",
        title="wechat_image",
        raw_content="cake photo",
        normalized_text="女朋友 做蛋糕",
        summary="cake photo",
        metadata={
            "stored_file_path": str(source),
            "original_file_name": "cake.jpg",
            "user_note": "女朋友做蛋糕",
        },
        locator_hint=None,
        document_key=None,
        version=1,
        is_current=1,
    )

    deliver = run_dispatch(
        {
            "intent": "deliver",
            "session_id": "session-1",
            "source_message_id": "msg-2",
            "arguments": {
                "arguments": {
                    "item_refs": [item.id, "00000000-0000-0000-0000-000000000000"],
                }
            },
            "database_url": f"sqlite:///{db_path.as_posix()}",
        }
    )
    assert deliver["status"] == "completed"
    effects = deliver.get("effects") or []
    assert any(effect.get("kind") == "deliver_file" for effect in effects)
    assert effects[0]["payload"]["file_path"] == str(source)


def test_skill_executor_runs_archive_dispatch_in_process(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CORA_ARCHIVE_ROOT_DIR", str(tmp_path / "archive"))
    monkeypatch.setenv("CORA_ARCHIVE_MIRROR_ENABLED", "true")

    executor = SkillScriptExecutor()
    request = SkillScriptRequest(
        skill_name="archive-core",
        script_path="scripts/archive_dispatch.py",
        input_payload={
            "intent": "overview",
            "session_id": "s1",
            "source_message_id": "m1",
            "database_url": f"sqlite:///{(tmp_path / 'x.db').as_posix()}",
        },
    )
    viewed = executor.skill_loader.view_skill(
        "archive-core",
        file_path="scripts/archive_dispatch.py",
    )
    assert viewed is not None
    assert executor._should_run_archive_dispatch_in_process(request=request, viewed=viewed)
