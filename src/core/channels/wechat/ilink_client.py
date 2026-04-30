from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import logging
import uuid

import httpx

from core.channels.wechat.types import WechatInboundEvent

logger = logging.getLogger(__name__)

@dataclass(slots=True)
class WechatIlinkConfig:
    token: str
    base_url: str = "https://ilinkai.weixin.qq.com"
    app_id: str = "bot"
    poll_timeout_seconds: int = 35


class WechatIlinkClient:
    """Minimal iLink API client inspired by Hermes Weixin adapter."""

    def __init__(self, config: WechatIlinkConfig) -> None:
        self.config = config
        self._http = httpx.AsyncClient(timeout=40.0)
        self._sync_buf = ""

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get_updates(self) -> list[WechatInboundEvent]:
        payload = {
            "base_info": {"channel_version": "2.2.0"},
            "get_updates_buf": self._sync_buf,
            "timeout": self.config.poll_timeout_seconds * 1000,
        }
        data = await self._post_json("ilink/bot/getupdates", payload)
        new_sync_buf = str(data.get("get_updates_buf") or "")
        if new_sync_buf:
            self._sync_buf = new_sync_buf
        updates = data.get("msgs") or data.get("updates") or []
        events: list[WechatInboundEvent] = []
        for update in updates:
            event = self._parse_update(update)
            if event is not None:
                events.append(event)
            else:
                logger.info("wechat skipped non-text update keys=%s", list(update.keys())[:10] if isinstance(update, dict) else type(update))
        if not updates:
            logger.debug("wechat getupdates returned no messages")
        return events

    async def send_text(self, *, peer_user_id: str, text: str, context_token: str | None = None) -> dict[str, Any]:
        message: dict[str, Any] = {
            "from_user_id": "",
            "to_user_id": peer_user_id,
            "client_id": str(uuid.uuid4()),
            "message_type": 2,
            "message_state": 2,
            "item_list": [{"type": 1, "text_item": {"text": text}}],
        }
        if context_token:
            message["context_token"] = context_token
        payload = {
            "base_info": {"channel_version": "2.2.0"},
            "msg": message,
        }
        return await self._post_json("ilink/bot/sendmessage", payload)

    async def _post_json(self, endpoint: str, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.config.base_url.rstrip('/')}/{endpoint}"
        headers = {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "Authorization": f"Bearer {self.config.token}",
            "iLink-App-Id": self.config.app_id,
        }
        resp = await self._http.post(url, json=body, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict):
            if data.get("errcode") not in (None, 0):
                raise RuntimeError(f"iLink error {data.get('errcode')}: {data.get('errmsg')}")
            if data.get("ret") not in (None, 0):
                raise RuntimeError(f"iLink ret {data.get('ret')}: {data.get('errmsg')}")
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _parse_update(update: dict[str, Any]) -> WechatInboundEvent | None:
        event_id = str(update.get("message_id") or update.get("update_id") or update.get("msg_id") or "")
        if not event_id:
            return None
        context_token = update.get("context_token")
        from_user = str(update.get("from_user_id") or update.get("user_id") or "")
        if not from_user:
            return None

        text = ""
        item_list = update.get("item_list") or []
        if isinstance(item_list, list):
            for item in item_list:
                if not isinstance(item, dict):
                    continue
                if int(item.get("type") or 0) == 1:
                    text_item = item.get("text_item") or {}
                    text = str(text_item.get("text") or "").strip()
                    if text:
                        break
        msg = update.get("msg")
        if not text and isinstance(msg, dict):
            text = str(msg.get("text") or "").strip()
        if not text:
            text = str(update.get("text") or "").strip()
        if not text.strip():
            return None

        return WechatInboundEvent(
            event_id=event_id,
            user_id=from_user,
            text=text.strip(),
            context_token=str(context_token) if context_token else None,
            raw_payload=update,
        )
