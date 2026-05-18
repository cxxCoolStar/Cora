from __future__ import annotations

import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.channels.wechat.context_token_store import WechatContextTokenStore  # noqa: E402
from core.channels.wechat.ilink_client import WechatIlinkClient, WechatIlinkConfig, _aes128_ecb_encrypt  # noqa: E402


class _SpyClient(WechatIlinkClient):
    def __init__(self, config: WechatIlinkConfig) -> None:
        super().__init__(config)
        self.last_body = None

    async def _post_json(self, endpoint: str, body: dict):  # type: ignore[override]
        self.last_body = body
        return {"ret": 0, "errcode": 0}


def test_context_token_store_persists_and_restores(tmp_path: Path):
    token_file = tmp_path / "wechat" / "tokens.json"
    store = WechatContextTokenStore(token_file)
    store.set("u1", "ctx-1")
    store.set("u2", "ctx-2")

    restored = WechatContextTokenStore(token_file)
    assert restored.get("u1") == "ctx-1"
    assert restored.get("u2") == "ctx-2"


def test_ilink_client_send_uses_persisted_context_token(tmp_path: Path):
    token_file = tmp_path / "wechat" / "tokens.json"
    store = WechatContextTokenStore(token_file)
    store.set("peer-a", "ctx-peer-a")

    client = _SpyClient(
        WechatIlinkConfig(
            token="dummy",
            context_tokens_path=token_file,
        )
    )
    asyncio.run(client.send_text(peer_user_id="peer-a", text="hello", context_token=None))

    assert client.last_body is not None
    msg = client.last_body["msg"]
    assert msg.get("context_token") == "ctx-peer-a"
    asyncio.run(client.aclose())


def test_ilink_client_download_file_decrypts_media_payload(tmp_path: Path):
    class _FakeResponse:
        def __init__(self, content: bytes) -> None:
            self.content = content

        def raise_for_status(self) -> None:
            return None

    class _FakeHTTP:
        def __init__(self, content: bytes) -> None:
            self.content = content
            self.urls: list[str] = []

        async def get(self, url: str):
            self.urls.append(url)
            return _FakeResponse(self.content)

        async def aclose(self) -> None:
            return None

    client = WechatIlinkClient(
        WechatIlinkConfig(
            token="dummy",
            download_dir=tmp_path / "wechat_downloads",
        )
    )
    plaintext = b"legacy-doc-binary"
    aes_key = bytes.fromhex("00112233445566778899aabbccddeeff")
    ciphertext = _aes128_ecb_encrypt(plaintext, aes_key)
    fake_http = _FakeHTTP(ciphertext)
    client._http = fake_http  # type: ignore[assignment]

    item = {
        "file_item": {
            "file_name": "resume.doc",
            "media": {
                "encrypt_query_param": "enc-token",
                "aes_key": "MDAxMTIyMzM0NDU1NjY3Nzg4OTlhYWJiY2NkZGVlZmY=",
            },
        }
    }

    downloaded, error = asyncio.run(client._download_file(item))

    assert downloaded is not None
    assert error is None
    assert Path(downloaded).read_bytes() == plaintext
    assert fake_http.urls == ["https://novac2c.cdn.weixin.qq.com/c2c/download?encrypted_query_param=enc-token"]
    asyncio.run(client.aclose())


def test_ilink_client_parse_update_returns_media_failure_companion(tmp_path: Path):
    client = WechatIlinkClient(
        WechatIlinkConfig(
            token="dummy",
            download_dir=tmp_path / "wechat_downloads",
        )
    )

    async def _fail_download(item: dict):
        return None, "ConnectError('boom')"

    client._download_image = _fail_download  # type: ignore[method-assign]
    update = {
        "message_id": "msg-1",
        "from_user_id": "wx-user-1",
        "session_id": "conv-1",
        "create_time_ms": 123456789,
        "item_list": [
            {
                "type": 2,
                "image_item": {
                    "media": {
                        "full_url": "https://example.com/full.jpg",
                        "encrypt_query_param": "enc-token",
                        "aes_key": "dummy",
                    }
                },
            }
        ],
    }

    event = asyncio.run(client._parse_update(update))

    assert event is not None
    assert event.text is None
    assert event.file_path is None
    assert event.media_download_failed is True
    assert event.media_download_error == "ConnectError('boom')"
    assert event.conversation_id == "conv-1"
    asyncio.run(client.aclose())

