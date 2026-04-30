from __future__ import annotations

from core.channels.wechat.types import WechatHandleResult, WechatInboundEvent
from core.clawbot.service import ClawBotService
from core.storage.repositories import ChannelEventRepository, ChannelSessionMapRepository


class WechatGatewayService:
    CHANNEL_NAME = "wechat"

    def __init__(
        self,
        *,
        clawbot_service: ClawBotService,
        event_repository: ChannelEventRepository,
        session_map_repository: ChannelSessionMapRepository,
    ) -> None:
        self.clawbot_service = clawbot_service
        self.event_repository = event_repository
        self.session_map_repository = session_map_repository

    async def handle_inbound_event(self, *, event: WechatInboundEvent) -> WechatHandleResult:
        existing = self.event_repository.get(channel=self.CHANNEL_NAME, external_event_id=event.event_id)
        if existing is not None:
            return WechatHandleResult(
                deduplicated=True,
                session_id=existing.session_id or "",
                reply=existing.reply_preview or "duplicate event ignored",
                action="deduplicated",
            )

        session_id = self.session_map_repository.get_session_id(
            channel=self.CHANNEL_NAME,
            external_user_id=event.user_id,
        )
        if session_id is None:
            session = self.clawbot_service.create_session()
            session_id = session.id
            self.session_map_repository.upsert(
                channel=self.CHANNEL_NAME,
                external_user_id=event.user_id,
                session_id=session_id,
            )

        response = await self.clawbot_service.ingest(session_id=session_id, text=event.text, upload=None)
        self.event_repository.create(
            channel=self.CHANNEL_NAME,
            external_event_id=event.event_id,
            external_user_id=event.user_id,
            status="processed",
            session_id=session_id,
            reply_preview=response.reply[:300],
        )
        return WechatHandleResult(
            deduplicated=False,
            session_id=session_id,
            reply=response.reply,
            action=response.action,
        )
