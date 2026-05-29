from __future__ import annotations

import pytest

from core.channels.wechat.service import WechatGatewayService


@pytest.mark.asyncio
async def test_send_file_to_user_accepts_legacy_file_name_kwarg() -> None:
    class _Client:
        def __init__(self) -> None:
            self.kwargs: dict[str, object] = {}

        async def send_file(self, **kwargs: object) -> dict[str, int]:
            self.kwargs = kwargs
            return {"ret": 0}

    client = _Client()
    gateway = WechatGatewayService(
        clawbot_service=object(),  # type: ignore[arg-type]
        event_repository=object(),  # type: ignore[arg-type]
        session_map_repository=object(),  # type: ignore[arg-type]
        ilink_client=client,  # type: ignore[arg-type]
    )
    result = await gateway.send_file_to_user(
        user_id="wx-user",
        file_path="/tmp/photo.jpg",
        file_name="photo.jpg",
    )
    assert result == {"ret": 0}
    assert client.kwargs["caption"] == "photo.jpg"
