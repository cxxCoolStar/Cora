from __future__ import annotations

from io import BytesIO
import logging
from pathlib import Path

from fastapi import UploadFile

from core.channels.wechat.types import WechatHandleResult, WechatInboundEvent
from core.clawbot.service import ClawBotService
from core.storage.repositories import ChannelEventRepository, ChannelSessionMapRepository

logger = logging.getLogger(__name__)


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
        logger.info(
            "wechat gateway inbound event_id=%s user_id=%s has_text=%s has_file=%s",
            event.event_id,
            event.user_id,
            bool(event.text),
            bool(event.file_path),
        )
        existing = self.event_repository.get(channel=self.CHANNEL_NAME, external_event_id=event.event_id)
        if existing is not None:
            logger.info("wechat gateway deduplicated event_id=%s session_id=%s", event.event_id, existing.session_id)
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
            logger.info("wechat gateway new_session user_id=%s session_id=%s", event.user_id, session_id)
            self.session_map_repository.upsert(
                channel=self.CHANNEL_NAME,
                external_user_id=event.user_id,
                session_id=session_id,
            )
        else:
            logger.info("wechat gateway reuse_session user_id=%s session_id=%s", event.user_id, session_id)

        upload = None
        try:
            if event.file_path and event.file_name:
                logger.info("wechat gateway loading_file session_id=%s file=%s path=%s", session_id, event.file_name, event.file_path)
                file_bytes = Path(event.file_path).read_bytes()
                upload = UploadFile(
                    filename=event.file_name,
                    file=BytesIO(file_bytes),
                    headers=None,
                )
            response = await self.clawbot_service.ingest(session_id=session_id, text=event.text, upload=upload)
            logger.info("wechat gateway clawbot_done session_id=%s action=%s", session_id, response.action)
        finally:
            if upload is not None:
                await upload.close()
        self.event_repository.create(
            channel=self.CHANNEL_NAME,
            external_event_id=event.event_id,
            external_user_id=event.user_id,
            status="processed",
            session_id=session_id,
            reply_preview=response.reply[:300],
        )
        logger.info("wechat gateway persisted event_id=%s session_id=%s action=%s", event.event_id, session_id, response.action)
        return WechatHandleResult(
            deduplicated=False,
            session_id=session_id,
            reply=response.reply,
            action=response.action,
        )
