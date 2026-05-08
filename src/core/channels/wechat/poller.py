from __future__ import annotations

import asyncio
import logging

from core.channels.wechat.ilink_client import WechatIlinkClient
from core.channels.wechat.service import WechatGatewayService

logger = logging.getLogger(__name__)


class WechatPoller:
    def __init__(self, *, client: WechatIlinkClient, gateway_service: WechatGatewayService) -> None:
        self.client = client
        self.gateway_service = gateway_service
        self._stopped = False

    async def run_forever(self) -> None:
        logger.info("wechat poller started")
        while not self._stopped:
            try:
                events = await self.client.get_updates()
                if events:
                    logger.info("wechat poll received %d text event(s)", len(events))
                for event in events:
                    text_preview = event.text[:80] if event.text else "(none)"
                    logger.info("wechat inbound: user=%s event=%s text=%s", event.user_id, event.event_id, text_preview)
                    result = await self.gateway_service.handle_inbound_event(event=event)
                    if result.deduplicated:
                        logger.info("wechat event deduplicated: %s", event.event_id)
                        continue
                    if not result.reply.strip():
                        logger.info("wechat reply suppressed: user=%s event=%s action=%s", event.user_id, event.event_id, result.action)
                        continue
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
            except Exception:
                logger.exception("wechat poll loop failed; retry in 2 seconds")
                await asyncio.sleep(2)

    def stop(self) -> None:
        self._stopped = True
