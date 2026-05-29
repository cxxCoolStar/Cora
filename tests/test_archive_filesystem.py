from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SKILL_ROOT = Path(__file__).resolve().parents[1] / "skills" / "archive-core"
if str(_SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(_SKILL_ROOT))

from archive_core.paths import archive_filename_from_note, slugify_user_note  # noqa: E402
from archive_core.store.file_store import FileArchiveStore  # noqa: E402

from core.skills.runner import run_archive_dispatch  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_archive_settings_cache() -> None:
    from adapters.cora.settings import get_cora_archive_settings

    get_cora_archive_settings.cache_clear()


def test_slugify_user_note_strips_prefix_and_invalid_chars() -> None:
    assert slugify_user_note("=这是我女朋友做蛋糕的照片") == "这是我女朋友做蛋糕的照片"
    assert "/" not in slugify_user_note("a/b")


def test_archive_filename_from_note() -> None:
    name = archive_filename_from_note(user_note="女朋友做蛋糕", suffix=".jpg")
    assert name.endswith(".jpg")
    assert "女朋友做蛋糕" in name


def test_filesystem_dispatch_save_and_deliver(tmp_path: Path, monkeypatch) -> None:
    archive_root = tmp_path / "archive"
    source = tmp_path / "cake.jpg"
    source.write_bytes(b"jpeg")
    monkeypatch.setenv("CORA_ARCHIVE_ROOT_DIR", str(archive_root))
    monkeypatch.setenv("CORA_ARCHIVE_STORAGE_MODE", "filesystem")

    saved = run_archive_dispatch(
        {
            "intent": "save",
            "session_id": "s1",
            "source_message_id": "m1",
            "upload_path": str(source),
            "upload_name": "wechat_image.jpg",
            "archive_root": str(archive_root),
            "archive_storage_mode": "filesystem",
            "arguments": {"user_note": "女朋友做蛋糕背影"},
        }
    )
    assert saved["action"] == "capture"
    assert saved["effects"] == []
    assert "照片已保存" in saved["message"]

    store = FileArchiveStore(archive_root)
    records = store.search(query="女朋友 做蛋糕", limit=5)
    assert records
    assert "女朋友做蛋糕背影" in records[0].record.filename

    deliver = run_archive_dispatch(
        {
            "intent": "deliver",
            "session_id": "s1",
            "source_message_id": "m2",
            "archive_root": str(archive_root),
            "archive_storage_mode": "filesystem",
            "arguments": {"query": "女朋友 做蛋糕"},
        }
    )
    assert deliver["action"] == "retrieve"
    assert any(effect.get("kind") == "deliver_file" for effect in deliver.get("effects") or [])


def test_filesystem_dispatch_deliver_clarifies_on_multiple_matches(tmp_path: Path, monkeypatch) -> None:
    archive_root = tmp_path / "archive"
    monkeypatch.setenv("CORA_ARCHIVE_ROOT_DIR", str(archive_root))
    monkeypatch.setenv("CORA_ARCHIVE_STORAGE_MODE", "filesystem")
    store = FileArchiveStore(archive_root)
    for note in ("女朋友做蛋糕A", "女朋友做蛋糕B"):
        path = tmp_path / f"{note}.jpg"
        path.write_bytes(b"jpeg")
        store.save_asset(
            source_path=path,
            topic="personal-photos",
            asset_type="image",
            user_note=note,
        )

    deliver = run_archive_dispatch(
        {
            "intent": "deliver",
            "session_id": "s1",
            "source_message_id": "m1",
            "archive_root": str(archive_root),
            "archive_storage_mode": "filesystem",
            "arguments": {"query": "女朋友 做蛋糕"},
        }
    )
    assert deliver["action"] == "clarify"
    assert deliver.get("pending_state_delta")

    selected = run_archive_dispatch(
        {
            "intent": "resolve_pending",
            "session_id": "s1",
            "source_message_id": "m2",
            "archive_root": str(archive_root),
            "archive_storage_mode": "filesystem",
            "arguments": {
                "resolution": "select",
                "target": {"type": "working_set_rank", "value": 2},
            },
            "runtime_state": {
                "pending_state": deliver["pending_state_delta"]["request"]["payload"],
            },
        }
    )
    assert selected["action"] == "retrieve"
    assert selected.get("pending_state_delta", {}).get("status") == "resolved"
