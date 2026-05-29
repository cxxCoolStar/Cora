from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.agent.skill_effects import HostEffectDispatcher, ilink_send_success, wechat_delivery_caption


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
async def test_deliver_file_treats_empty_ilink_payload_as_success() -> None:
    sent: list[dict[str, str]] = []

    async def send_file(**kwargs: object) -> dict[str, object]:
        sent.append({key: str(value) for key, value in kwargs.items()})
        return {}

    dispatcher = HostEffectDispatcher(
        ingestion_service=SimpleNamespace(storage_dir="."),
        can_send_files_to_user=lambda: True,
        resolve_external_user_id=lambda _session_id: "wx-user",
        send_file=send_file,
        persist_temp_upload=lambda upload: "",
        current_source_event_id=lambda _invocation: None,
        item_artifact=lambda **kwargs: {},
        ingest_upload=lambda **kwargs: None,
    )
    invocation = SimpleNamespace(session_id="session-1", source_message_id="message-1")
    execution = SimpleNamespace(reply="", action="", status="completed")
    await dispatcher._deliver_file(
        invocation=invocation,
        execution=execution,
        payload={"title": "wechat_image", "file_path": __file__},
    )
    assert execution.status == "completed"
    assert execution.reply == "已经发送，请查收。"
    assert sent[0]["caption"] == ""


@pytest.mark.asyncio
async def test_deliver_file_skips_duplicate_send_in_same_message() -> None:
    calls = 0

    async def send_file(**kwargs: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"ret": 0}

    dispatcher = HostEffectDispatcher(
        ingestion_service=SimpleNamespace(storage_dir="."),
        can_send_files_to_user=lambda: True,
        resolve_external_user_id=lambda _session_id: "wx-user",
        send_file=send_file,
        persist_temp_upload=lambda upload: "",
        current_source_event_id=lambda _invocation: None,
        item_artifact=lambda **kwargs: {},
        ingest_upload=lambda **kwargs: None,
    )
    invocation = SimpleNamespace(session_id="session-1", source_message_id="message-1")
    payload = {"title": "photo.jpg", "file_path": __file__}
    first = SimpleNamespace(reply="", action="", status="completed")
    second = SimpleNamespace(reply="", action="", status="completed")
    await dispatcher._deliver_file(invocation=invocation, execution=first, payload=payload)
    await dispatcher._deliver_file(invocation=invocation, execution=second, payload=payload)
    assert calls == 1
    assert second.reply == "已经发送，请查收。"
