from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
import logging
from pathlib import Path
from typing import Any

from fastapi import UploadFile
import httpx

from core.channels.base import ChannelTurnInput
from core.channels.wechat.gateway_errors import build_gateway_model_error_reply
from core.channels.wechat.plan_commands import parse_plan_command, plan_command_text
from core.channels.wechat.hitl_commands import (
    build_wechat_hitl_approved_prefix,
    build_wechat_hitl_expired_message,
    build_wechat_hitl_no_pending_message,
    build_wechat_hitl_pending_reminder,
    build_wechat_hitl_rejected_message,
    parse_hitl_command,
)
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

        hitl_result = await self._try_handle_hitl_command(
            event=event,
            session_id=session_id,
            text=resolution.normalized_text,
        )
        if hitl_result is not None:
            self.event_repository.create(
                channel=self.CHANNEL_NAME,
                external_event_id=event.event_id,
                external_user_id=event.user_id,
                status="processed",
                session_id=session_id,
                reply_preview=hitl_result.reply[:300],
            )
            return hitl_result

        try:
            plan_result = await self._try_handle_plan_command(
                event=event,
                session_id=session_id,
                text=resolution.normalized_text,
            )
        except Exception as exc:
            logger.exception(
                "wechat gateway plan command failed session_id=%s event_id=%s",
                session_id,
                event.event_id,
            )
            plan_result = WechatHandleResult(
                deduplicated=False,
                session_id=session_id,
                reply=build_gateway_model_error_reply(exc),
                action="plan_failed",
                disposition="error",
            )
        if plan_result is not None:
            self.event_repository.create(
                channel=self.CHANNEL_NAME,
                external_event_id=event.event_id,
                external_user_id=event.user_id,
                status="processed",
                session_id=session_id,
                reply_preview=plan_result.reply[:300],
            )
            return plan_result

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
            try:
                response = await self._ingest_turn_input(turn_input)
            except httpx.HTTPError as exc:
                logger.exception(
                    "wechat gateway ingest failed session_id=%s event_id=%s",
                    session_id,
                    event.event_id,
                )
                self.event_repository.create(
                    channel=self.CHANNEL_NAME,
                    external_event_id=event.event_id,
                    external_user_id=event.user_id,
                    status="processed",
                    session_id=session_id,
                    reply_preview=build_gateway_model_error_reply(exc)[:300],
                )
                return WechatHandleResult(
                    deduplicated=False,
                    session_id=session_id,
                    reply=build_gateway_model_error_reply(exc),
                    action="ingest_failed",
                    disposition="error",
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
            disposition=response.disposition,
            needs_clarification=response.needs_clarification,
        )

    async def _try_handle_hitl_command(
        self,
        *,
        event: WechatInboundEvent,
        session_id: str,
        text: str | None,
    ) -> WechatHandleResult | None:
        command = parse_hitl_command(text)
        pending = self.clawbot_service.get_latest_pending_hitl(session_id=session_id)

        if command == "approve":
            if pending is None:
                return WechatHandleResult(
                    deduplicated=False,
                    session_id=session_id,
                    reply=build_wechat_hitl_no_pending_message(),
                    action="hitl_no_pending",
                )
            try:
                response = await self.clawbot_service.approve_hitl_and_resume(
                    session_id=session_id,
                    hitl_id=pending.hitl_id,
                    source_metadata={"channel": self.CHANNEL_NAME, "platform": "wechat"},
                )
            except ValueError as exc:
                if "expired" in str(exc).lower():
                    return WechatHandleResult(
                        deduplicated=False,
                        session_id=session_id,
                        reply=build_wechat_hitl_expired_message(tool_name=pending.tool_name),
                        action="hitl_expired",
                    )
                return WechatHandleResult(
                    deduplicated=False,
                    session_id=session_id,
                    reply=f"无法完成确认：{exc}",
                    action="hitl_approve_failed",
                )
            except KeyError as exc:
                return WechatHandleResult(
                    deduplicated=False,
                    session_id=session_id,
                    reply=f"无法完成确认：{exc}",
                    action="hitl_approve_failed",
                )
            prefix = build_wechat_hitl_approved_prefix(tool_name=pending.tool_name)
            return WechatHandleResult(
                deduplicated=False,
                session_id=session_id,
                reply=f"{prefix}{response.reply}".strip(),
                action="hitl_approved",
                disposition=response.disposition,
                needs_clarification=response.needs_clarification,
            )

        if command == "reject":
            if pending is None:
                return WechatHandleResult(
                    deduplicated=False,
                    session_id=session_id,
                    reply=build_wechat_hitl_no_pending_message(),
                    action="hitl_no_pending",
                )
            try:
                rejected = self.clawbot_service.reject_hitl(
                    session_id=session_id,
                    hitl_id=pending.hitl_id,
                )
            except (KeyError, ValueError) as exc:
                return WechatHandleResult(
                    deduplicated=False,
                    session_id=session_id,
                    reply=f"无法完成拒绝：{exc}",
                    action="hitl_reject_failed",
                )
            return WechatHandleResult(
                deduplicated=False,
                session_id=session_id,
                reply=build_wechat_hitl_rejected_message(tool_name=rejected.tool_name),
                action="hitl_rejected",
            )

        if pending is not None and self._has_user_payload(text=text, event=event):
            return WechatHandleResult(
                deduplicated=False,
                session_id=session_id,
                reply=build_wechat_hitl_pending_reminder(tool_name=pending.tool_name),
                action="hitl_pending_blocked",
            )
        return None

    async def _try_handle_plan_command(
        self,
        *,
        event: WechatInboundEvent,
        session_id: str,
        text: str | None,
    ) -> WechatHandleResult | None:
        command = parse_plan_command(text)
        if command is None:
            return None
        metadata = {"channel": self.CHANNEL_NAME, "platform": "wechat"}
        if command == "plan":
            response = await self.clawbot_service.plan_turn(
                session_id=session_id,
                text=plan_command_text(text),
                source_metadata=metadata,
            )
            return WechatHandleResult(
                deduplicated=False,
                session_id=session_id,
                reply=response.reply,
                action="plan_created",
                disposition=response.disposition,
                needs_clarification=response.needs_clarification,
            )
        # Handle both /execute and /replay commands
        response = await self.clawbot_service.execute_plan_turn(
            session_id=session_id,
            text=str(text or "").strip() or "/execute",
            source_metadata=metadata,
        )
        action = "plan_replayed" if command == "replay" else "plan_executed"
        return WechatHandleResult(
            deduplicated=False,
            session_id=session_id,
            reply=response.reply,
            action=action,
            disposition=response.disposition,
            needs_clarification=response.needs_clarification,
        )

    @staticmethod
    def _has_user_payload(*, text: str | None, event: WechatInboundEvent) -> bool:
        if text and str(text).strip():
            return True
        return bool((event.file_path and event.file_name) or event.file_path)

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
                "platform": "wechat",
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
