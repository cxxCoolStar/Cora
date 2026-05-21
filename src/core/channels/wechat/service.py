from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
import logging
from pathlib import Path
from typing import Any

from fastapi import UploadFile

from core.channels.base import ChannelTurnInput
from core.channels.wechat.ilink_client import WechatIlinkClient
from core.channels.wechat.session_router import WechatSessionRouter
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
        session_idle_minutes: int = 120,
        session_daily_reset_hour: int | None = 4,
        session_timezone: str | None = None,
        enable_manual_reset: bool = True,
        session_router: WechatSessionRouter | None = None,
    ) -> None:
        self.clawbot_service = clawbot_service
        self.event_repository = event_repository
        self.session_map_repository = session_map_repository
        self._ilink_client = ilink_client
        self._session_router = session_router or WechatSessionRouter(
            clawbot_service=clawbot_service,
            session_map_repository=session_map_repository,
            idle_minutes=session_idle_minutes,
            daily_reset_hour=session_daily_reset_hour,
            timezone_name=session_timezone,
            enable_manual_reset=enable_manual_reset,
        )

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

        resolution = self._session_router.resolve(channel=self.CHANNEL_NAME, event=event)
        session_id = resolution.session_id
        logger.info(
            "wechat gateway session_resolution user_id=%s session_id=%s is_new=%s reason=%s",
            event.user_id,
            session_id,
            resolution.is_new_session,
            resolution.reason,
        )
        if resolution.reply_text is not None:
            self.event_repository.create(
                channel=self.CHANNEL_NAME,
                external_event_id=event.event_id,
                external_user_id=event.user_id,
                status="processed",
                session_id=session_id,
                reply_preview=resolution.reply_text[:300],
            )
            return WechatHandleResult(
                deduplicated=False,
                session_id=session_id,
                reply=resolution.reply_text,
                action="session_reset",
            )

        upload = None
        try:
            source_created_at = self._source_created_at(event)
            if event.file_path and event.file_name:
                logger.info("wechat gateway loading_file session_id=%s file=%s path=%s", session_id, event.file_name, event.file_path)
                file_bytes = Path(event.file_path).read_bytes()
                upload = UploadFile(
                    filename=event.file_name,
                    file=BytesIO(file_bytes),
                    headers=None,
                )
            turn_input = self._build_turn_input(
                event=event,
                session_id=session_id,
                normalized_text=resolution.normalized_text,
                session_reset_reason=resolution.reason,
                session_is_new=resolution.is_new_session,
                source_created_at=source_created_at,
                upload=upload,
            )
            response = await self._ingest_turn_input(turn_input)
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

    def _build_turn_input(
        self,
        *,
        event: WechatInboundEvent,
        session_id: str,
        normalized_text: str | None,
        session_reset_reason: str,
        session_is_new: bool,
        source_created_at: datetime | None,
        upload: UploadFile | None,
    ) -> ChannelTurnInput:
        return ChannelTurnInput(
            channel=self.CHANNEL_NAME,
            session_id=session_id,
            source_message_id=event.event_id,
            external_user_id=event.user_id,
            user_text=normalized_text,
            raw_text=event.text,
            upload=upload,
            delivery_available=self._ilink_client is not None,
            platform_preset="cora-wechat",
            source_metadata={
                "channel": self.CHANNEL_NAME,
                "external_event_id": event.event_id,
                "external_user_id": event.user_id,
                "file_name": event.file_name,
                "file_mime": event.file_mime,
                "media_download_failed": event.media_download_failed,
                "media_download_error": event.media_download_error,
                "session_reset_reason": session_reset_reason,
                "session_is_new": session_is_new,
                "source_create_time_ms": event.create_time_ms,
                "source_created_at": source_created_at.isoformat() if source_created_at is not None else None,
                "delivery_available": self._ilink_client is not None,
                "platform_preset": "cora-wechat",
            },
        )

    async def _ingest_turn_input(self, turn_input: ChannelTurnInput):
        return await self.clawbot_service.ingest(
            session_id=turn_input.session_id,
            text=turn_input.user_text,
            upload=turn_input.upload,
            source_metadata=turn_input.source_metadata,
        )

    @staticmethod
    def _source_created_at(event: WechatInboundEvent) -> datetime | None:
        if event.create_time_ms is None:
            return None
        return datetime.fromtimestamp(max(0, event.create_time_ms) / 1000, tz=UTC)

    async def send_text_to_user(
        self,
        user_id: str,
        text: str,
        *,
        context_token: str | None = None,
    ) -> dict[str, Any]:
        """Send plain text to a WeChat user."""
        if self._ilink_client is None:
            raise RuntimeError("ilink_client not configured")

        logger.info(
            "wechat gateway sending text user_id=%s has_text=%s",
            user_id,
            bool(str(text or "").strip()),
        )
        result = await self._ilink_client.send_text(
            peer_user_id=user_id,
            text=text,
            context_token=context_token,
        )
        logger.info("wechat gateway text sent user_id=%s result=%s", user_id, result.get("ret"))
        return result

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
