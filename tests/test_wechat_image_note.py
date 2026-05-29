from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import UploadFile

from core.channels.wechat.poller import WechatPoller
from core.channels.wechat.types import WechatHandleResult, WechatInboundEvent
from core.clawbot.wechat_image_note import (
    WECHAT_IMAGE_NOTE_QUESTION,
    classify_image_note_follow_up,
    is_image_only_turn,
)
from tests.test_clawbot_api import build_test_container


def test_classify_image_note_follow_up() -> None:
    assert classify_image_note_follow_up("直接保存") == ("direct_save", None)
    assert classify_image_note_follow_up("取消") == ("cancel", None)
    resolution, note = classify_image_note_follow_up("和女朋友在太美丽餐厅吃泰餐")
    assert resolution == "note"
    assert note == "和女朋友在太美丽餐厅吃泰餐"


def test_classify_image_note_follow_up_strips_leading_equals() -> None:
    resolution, note = classify_image_note_follow_up("=这张图片是我跟女朋友去太美丽餐厅吃泰餐时拍下的照片")
    assert resolution == "note"
    assert note == "这张图片是我跟女朋友去太美丽餐厅吃泰餐时拍下的照片"


def test_wechat_image_only_stages_pending_without_saving(tmp_path: Path) -> None:
    container = build_test_container(tmp_path)
    session = container.session_repository.create()
    upload = UploadFile(filename="wechat_image.jpg", file=BytesIO(b"fake image bytes"))
    inbox_path = str(tmp_path / "files" / "wechat_inbox" / "photo.jpg")
    Path(inbox_path).parent.mkdir(parents=True, exist_ok=True)
    Path(inbox_path).write_bytes(b"fake image bytes")

    response = asyncio.run(
        container.clawbot_service.ingest(
            session_id=session.id,
            text=None,
            upload=upload,
            source_metadata={
                "channel": "wechat",
                "platform": "wechat",
                "inbox_file_path": inbox_path,
            },
        )
    )

    assert response.action == "clarify"
    assert WECHAT_IMAGE_NOTE_QUESTION in response.reply
    archive_root = tmp_path / "archive"
    assert not list((archive_root / "topics").glob("**/*.jpg")) if (archive_root / "topics").exists() else True
    pending = container.pending_state_repository.get_latest_pending(session_id=session.id)
    assert pending is not None
    assert pending.pending_payload_json.get("type") == "upload_save"


def test_wechat_image_note_follow_up_saves_with_user_note(tmp_path: Path) -> None:
    container = build_test_container(tmp_path)
    session = container.session_repository.create()
    upload = UploadFile(filename="wechat_image.jpg", file=BytesIO(b"fake image bytes"))
    inbox_path = str(tmp_path / "files" / "wechat_inbox" / "photo.jpg")
    Path(inbox_path).parent.mkdir(parents=True, exist_ok=True)
    Path(inbox_path).write_bytes(b"fake image bytes")

    asyncio.run(
        container.clawbot_service.ingest(
            session_id=session.id,
            text=None,
            upload=upload,
            source_metadata={
                "channel": "wechat",
                "platform": "wechat",
                "inbox_file_path": inbox_path,
            },
        )
    )

    follow_up = asyncio.run(
        container.clawbot_service.ingest(
            session_id=session.id,
            text="=这张图片是我跟女朋友去太美丽餐厅吃泰餐时拍下的照片",
            upload=None,
            source_metadata={"channel": "wechat", "platform": "wechat"},
        )
    )

    assert follow_up.action == "capture"
    assert "照片已保存" in follow_up.reply

    archive_root = tmp_path / "archive"
    topic_dir = archive_root / "topics" / "personal-photos"
    assert topic_dir.is_dir()
    saved_files = list(topic_dir.glob("*.jpg"))
    assert len(saved_files) == 1
    assert "女朋友" in saved_files[0].name or "太美丽" in saved_files[0].name


def test_wechat_image_with_caption_saves_immediately(tmp_path: Path) -> None:
    container = build_test_container(tmp_path)
    session = container.session_repository.create()
    upload = UploadFile(filename="wechat_image.jpg", file=BytesIO(b"fake image bytes"))

    response = asyncio.run(
        container.clawbot_service.ingest(
            session_id=session.id,
            text="周末去公园拍的照片",
            upload=upload,
            source_metadata={"channel": "wechat", "platform": "wechat"},
        )
    )

    assert response.action == "capture"
    archive_root = tmp_path / "archive"
    topic_dir = archive_root / "topics" / "personal-photos"
    assert topic_dir.is_dir()
    assert list(topic_dir.glob("*.jpg"))


def test_poller_merges_image_then_text_before_processing() -> None:
    processed: list[WechatInboundEvent] = []

    class _Gateway:
        async def handle_inbound_event(self, *, event: WechatInboundEvent):
            processed.append(event)
            return WechatHandleResult(
                deduplicated=False,
                session_id="session-1",
                reply="ok",
                action="chat",
            )

    class _Client:
        async def send_text(self, **kwargs):
            return {"ret": 0}

    poller = WechatPoller(
        client=_Client(),
        gateway_service=_Gateway(),
        image_note_wait_seconds=5.0,
    )
    image_event = WechatInboundEvent(
        event_id="img-1",
        user_id="wx-user",
        file_name="wechat_image.jpg",
        file_path="/tmp/wechat_image.jpg",
        create_time_ms=1_000,
    )
    text_event = WechatInboundEvent(
        event_id="txt-1",
        user_id="wx-user",
        text="和女朋友吃泰餐",
        create_time_ms=1_500,
    )

    async def _run() -> None:
        await poller._handle_event(image_event)
        assert processed == []
        await poller._handle_event(text_event)
        assert len(processed) == 1
        assert processed[0].text == "和女朋友吃泰餐"
        assert processed[0].file_path == "/tmp/wechat_image.jpg"

    asyncio.run(_run())
