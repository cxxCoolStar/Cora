from __future__ import annotations

from io import BytesIO
import logging
from pathlib import Path
from typing import Any

from fastapi import UploadFile

from core.channels.wechat.ilink_client import WechatIlinkClient
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
        ilink_client: WechatIlinkClient | None = None,
    ) -> None:
        self.clawbot_service = clawbot_service
        self.event_repository = event_repository
        self.session_map_repository = session_map_repository
        self._ilink_client = ilink_client

    async def handle_inbound_event(self, *, event: WechatInboundEvent) -> WechatHandleResult:
        logger.info(
            "wechat gateway inbound event_id=%s user_id=%s has_text=%s has_file=%s media_failed=%s",
            event.event_id,
            event.user_id,
            bool(event.text),
            bool(event.file_path),
            event.media_download_failed,
        )
        if event.media_download_failed and not event.file_path:
            logger.warning(
                "wechat gateway media_missing event_id=%s user_id=%s error=%s",
                event.event_id,
                event.user_id,
                event.media_download_error,
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
            response = await self.clawbot_service.ingest(
                session_id=session_id,
                text=event.text,
                upload=upload,
                source_metadata={
                    "channel": self.CHANNEL_NAME,
                    "external_event_id": event.event_id,
                    "external_user_id": event.user_id,
                    "file_name": event.file_name,
                    "file_mime": event.file_mime,
                    "media_download_failed": event.media_download_failed,
                    "media_download_error": event.media_download_error,
                },
            )
            if event.media_download_failed and not event.file_path:
                if response.reply.strip() == "I do not have a final answer yet.":
                    response.reply = "我收到了你发的文字，但这条消息里的图片下载失败了，所以这次还没有成功保存图片。你可以把图片再发一次，或者等我把这条微信图文接收链路修好后再试。"
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

    async def send_file_to_user(
        self,
        *,
        user_id: str,
        file_path: str,
        caption: str = "",
        context_token: str | None = None,
    ) -> dict[str, Any]:
        """Send a file to a WeChat user.

        Args:
            user_id: Target WeChat user ID
            file_path: Local file path to send
            caption: Optional caption text
            context_token: Optional context token for the session

        Returns:
            API response from iLink
        """
        if self._ilink_client is None:
            raise RuntimeError("ilink_client not configured")

        logger.info(
            "wechat gateway sending file user_id=%s file=%s caption=%s",
            user_id,
            file_path,
            bool(caption),
        )
        result = await self._ilink_client.send_file(
            peer_user_id=user_id,
            file_path=file_path,
            caption=caption,
            context_token=context_token,
        )
        logger.info("wechat gateway file sent user_id=%s result=%s", user_id, result.get("ret"))
        return result
