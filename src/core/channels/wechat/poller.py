from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from typing import Any

from core.channels.wechat.ilink_client import WechatIlinkClient
from core.channels.wechat.progress import WechatProgressSettings, wechat_progress_scope
from core.channels.wechat.service import WechatGatewayService
from core.channels.wechat.types import WechatInboundEvent

logger = logging.getLogger(__name__)


class WechatPoller:
    def __init__(
        self,
        *,
        client: WechatIlinkClient,
        gateway_service: WechatGatewayService,
        aggregation_window_seconds: float = 3.0,
        late_media_window_ms: int = 30000,
        progress_settings: WechatProgressSettings | None = None,
    ) -> None:
        self.client = client
        self.gateway_service = gateway_service
        self.progress_settings = progress_settings or WechatProgressSettings()
        self._stopped = False
        self.aggregation_window_seconds = aggregation_window_seconds
        self.late_media_window_ms = late_media_window_ms
        self._pending_text_events: dict[str, tuple[WechatInboundEvent, float]] = {}
        self._recent_text_events: dict[str, WechatInboundEvent] = {}

    async def run_forever(self) -> None:
        logger.info("wechat poller started")
        while not self._stopped:
            try:
                events = await self.client.get_updates()
                if events:
                    logger.info("wechat poll received %d inbound event(s)", len(events))
                for event in events:
                    await self._handle_event(event)
                await self._flush_due_pending()
            except Exception:
                logger.exception("wechat poll loop failed; retry in 2 seconds")
                await asyncio.sleep(2)
        await self._flush_due_pending(force=True)

    def stop(self) -> None:
        self._stopped = True

    async def _handle_event(self, event: WechatInboundEvent) -> None:
        text_preview = event.text[:80] if event.text else "(none)"
        logger.info("wechat inbound: user=%s event=%s text=%s", event.user_id, event.event_id, text_preview)
        key = self._event_key(event)
        if self._is_text_only(event):
            deadline = asyncio.get_running_loop().time() + self.aggregation_window_seconds
            existing = self._pending_text_events.get(key)
            if existing is not None:
                await self._process_event(existing[0])
            self._pending_text_events[key] = (event, deadline)
            return
        if self._is_media_companion(event):
            pending = self._pending_text_events.pop(key, None)
            if pending is not None and self._is_companion_match(pending[0], event):
                await self._process_event(self._merge_events(pending[0], event))
                return
            recent = self._recent_text_events.get(key)
            if recent is not None and self._is_companion_match(recent, event):
                if event.media_download_failed and not event.file_path:
                    await self._send_follow_up(
                        event=recent,
                        reply="补充说明：刚才那条图文消息里的图片下载失败了，所以目前只记录了文字，还没有保存图片本体。",
                    )
                    return
        await self._process_event(event)

    async def _flush_due_pending(self, *, force: bool = False) -> None:
        now = asyncio.get_running_loop().time()
        ready_keys = [
            key for key, (_, deadline) in self._pending_text_events.items()
            if force or deadline <= now
        ]
        for key in ready_keys:
            event, _ = self._pending_text_events.pop(key)
            await self._process_event(event)

    async def _process_event(self, event: WechatInboundEvent) -> None:
        async with wechat_progress_scope(
            event=event,
            client=self.client,
            settings=self.progress_settings,
        ):
            result = await self.gateway_service.handle_inbound_event(event=event)
        if self._is_text_only(event):
            self._recent_text_events[self._event_key(event)] = event
        if result.deduplicated:
            logger.info("wechat event deduplicated: %s", event.event_id)
            return
        if not result.reply.strip():
            logger.info("wechat reply suppressed: user=%s event=%s action=%s", event.user_id, event.event_id, result.action)
            return
        send_result = await self.client.send_text(
            peer_user_id=event.user_id,
            text=result.reply,
            context_token=event.context_token,
        )
        logger.info(
            "wechat reply sent: user=%s event=%s action=%s ret=%s errcode=%s",
            event.user_id,
            event.event_id,
            result.action,
            send_result.get("ret") if isinstance(send_result, dict) else None,
            send_result.get("errcode") if isinstance(send_result, dict) else None,
        )

    async def _send_follow_up(self, *, event: WechatInboundEvent, reply: str) -> None:
        send_result = await self.client.send_text(
            peer_user_id=event.user_id,
            text=reply,
            context_token=event.context_token,
        )
        logger.info(
            "wechat follow-up sent: user=%s event=%s ret=%s errcode=%s",
            event.user_id,
            event.event_id,
            send_result.get("ret") if isinstance(send_result, dict) else None,
            send_result.get("errcode") if isinstance(send_result, dict) else None,
        )

    @staticmethod
    def _event_key(event: WechatInboundEvent) -> str:
        return f"{event.user_id}::{event.conversation_id or 'direct'}"

    @staticmethod
    def _is_text_only(event: WechatInboundEvent) -> bool:
        return bool(event.text and not event.file_path and not event.media_download_failed)

    @staticmethod
    def _is_media_companion(event: WechatInboundEvent) -> bool:
        return bool((event.file_path or event.media_download_failed) and not event.text)

    def _is_companion_match(self, text_event: WechatInboundEvent, media_event: WechatInboundEvent) -> bool:
        if text_event.user_id != media_event.user_id:
            return False
        if (text_event.conversation_id or "") != (media_event.conversation_id or ""):
            return False
        if text_event.create_time_ms is None or media_event.create_time_ms is None:
            return True
        return abs(media_event.create_time_ms - text_event.create_time_ms) <= self.late_media_window_ms

    @staticmethod
    def _merge_events(text_event: WechatInboundEvent, media_event: WechatInboundEvent) -> WechatInboundEvent:
        merged_payload: dict[str, Any] | None = None
        if text_event.raw_payload or media_event.raw_payload:
            merged_payload = {
                "text_event": text_event.raw_payload,
                "media_event": media_event.raw_payload,
            }
        return replace(
            text_event,
            file_name=media_event.file_name or text_event.file_name,
            file_path=media_event.file_path or text_event.file_path,
            file_mime=media_event.file_mime or text_event.file_mime,
            media_download_failed=media_event.media_download_failed,
            media_download_error=media_event.media_download_error,
            raw_payload=merged_payload,
        )
