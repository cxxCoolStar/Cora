from __future__ import annotations

import pytest

from core.channels.delivery import ChannelDelivery, ilink_send_success, wechat_delivery_caption


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (None, True),
        ({}, True),
        ({"ret": None, "errcode": None}, True),
        ({"ret": 0, "errcode": 0}, True),
        ({"ret": 1}, False),
        ({"errcode": 40001}, False),
    ],
)
def test_ilink_send_success(result: object, expected: bool) -> None:
    assert ilink_send_success(result) is expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("wechat_image", ""),
        ("wechat_image.jpg", ""),
        ("photo.jpg", "photo.jpg"),
    ],
)
def test_wechat_delivery_caption(title: str, expected: str) -> None:
    assert wechat_delivery_caption(title) == expected


@pytest.mark.asyncio
async def test_channel_delivery_treats_empty_ilink_payload_as_success(tmp_path) -> None:
    sent: list[dict[str, str]] = []
    source = tmp_path / "photo.jpg"
    source.write_bytes(b"jpeg")

    async def send_file(**kwargs: object) -> dict[str, object]:
        sent.append({key: str(value) for key, value in kwargs.items()})
        return {}

    delivery = ChannelDelivery(
        storage_dir=tmp_path,
        can_send_files_to_user=lambda: True,
        resolve_external_user_id=lambda _session_id: "wx-user",
        send_file=send_file,
    )
    outcome = await delivery.deliver_file(
        session_id="session-1",
        source_message_id="message-1",
        file_path=str(source),
        title="wechat_image",
    )
    assert outcome["status"] == "completed"
    assert outcome["reply"] == "已经发送，请查收。"
    assert sent[0]["caption"] == ""


@pytest.mark.asyncio
async def test_channel_delivery_skips_duplicate_send_in_same_message(tmp_path) -> None:
    calls = 0
    source = tmp_path / "photo.jpg"
    source.write_bytes(b"jpeg")

    async def send_file(**kwargs: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"ret": 0}

    delivery = ChannelDelivery(
        storage_dir=tmp_path,
        can_send_files_to_user=lambda: True,
        resolve_external_user_id=lambda _session_id: "wx-user",
        send_file=send_file,
    )
    first = await delivery.deliver_file(
        session_id="session-1",
        source_message_id="message-1",
        file_path=str(source),
        title="photo.jpg",
    )
    second = await delivery.deliver_file(
        session_id="session-1",
        source_message_id="message-1",
        file_path=str(source),
        title="photo.jpg",
    )
    assert calls == 1
    assert first["status"] == "completed"
    assert second["reply"] == "已经发送，请查收。"
