from __future__ import annotations

import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.channels.wechat.context_token_store import WechatContextTokenStore  # noqa: E402
from core.channels.wechat.ilink_client import WechatIlinkClient, WechatIlinkConfig  # noqa: E402


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

