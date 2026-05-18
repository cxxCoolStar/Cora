from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
import re
from zoneinfo import ZoneInfo

from core.channels.wechat.types import WechatInboundEvent
from core.clawbot.service import ClawBotService
from core.storage.models import ChannelSessionMapRecord
from core.storage.repositories import ChannelSessionMapRepository


@dataclass(slots=True)
class WechatSessionResolution:
    session_id: str
    is_new_session: bool
    reason: str
    normalized_text: str | None
    reply_text: str | None = None


@dataclass(slots=True)
class _ManualResetCommand:
    remaining_text: str | None


class WechatSessionRouter:
    MANUAL_RESET_PATTERN = re.compile(r"^\s*/(?:new|reset)\b(?P<rest>[\s\S]*)$", re.IGNORECASE)

    def __init__(
        self,
        *,
        clawbot_service: ClawBotService,
        session_map_repository: ChannelSessionMapRepository,
        idle_minutes: int = 120,
        daily_reset_hour: int | None = 4,
        timezone_name: str | None = None,
        enable_manual_reset: bool = True,
    ) -> None:
        self.clawbot_service = clawbot_service
        self.session_map_repository = session_map_repository
        self.idle_minutes = max(0, int(idle_minutes))
        self.daily_reset_hour = None if daily_reset_hour is None else int(daily_reset_hour)
        self.enable_manual_reset = bool(enable_manual_reset)
        if self.daily_reset_hour is not None and not 0 <= self.daily_reset_hour <= 23:
            raise ValueError("daily_reset_hour must be between 0 and 23.")
        self.timezone = ZoneInfo(timezone_name) if timezone_name else datetime.now().astimezone().tzinfo

    def resolve(self, *, channel: str, event: WechatInboundEvent) -> WechatSessionResolution:
        now = self._event_time(event)
        binding = self.session_map_repository.get_binding(channel=channel, external_user_id=event.user_id)
        manual_reset = self._parse_manual_reset(event.text) if self.enable_manual_reset else None
        normalized_text = manual_reset.remaining_text if manual_reset is not None else event.text

        if binding is None:
            reset_reason = "manual_reset" if manual_reset is not None else "initial_bind"
            session_id = self._create_and_bind(
                channel=channel,
                external_user_id=event.user_id,
                now=now,
                reason=reset_reason,
            )
            if manual_reset is not None and not self._has_message_payload(text=normalized_text, event=event):
                return WechatSessionResolution(
                    session_id=session_id,
                    is_new_session=True,
                    reason="manual_reset",
                    normalized_text=None,
                    reply_text="Started a new conversation.",
                )
            return WechatSessionResolution(
                session_id=session_id,
                is_new_session=True,
                reason=reset_reason,
                normalized_text=normalized_text,
            )

        if manual_reset is not None:
            session_id = self._create_and_bind(
                channel=channel,
                external_user_id=event.user_id,
                now=now,
                reason="manual_reset",
            )
            if not self._has_message_payload(text=normalized_text, event=event):
                return WechatSessionResolution(
                    session_id=session_id,
                    is_new_session=True,
                    reason="manual_reset",
                    normalized_text=None,
                    reply_text="Started a new conversation.",
                )
            return WechatSessionResolution(
                session_id=session_id,
                is_new_session=True,
                reason="manual_reset",
                normalized_text=normalized_text,
            )

        active_session_id = self._active_session_id(binding=binding)
        if active_session_id is None:
            session_id = self._create_and_bind(
                channel=channel,
                external_user_id=event.user_id,
                now=now,
                reason="stale_binding",
            )
            return WechatSessionResolution(
                session_id=session_id,
                is_new_session=True,
                reason="stale_binding",
                normalized_text=normalized_text,
            )

        if self.clawbot_service.pending_state_repository.get_latest_pending(session_id=active_session_id) is not None:
            self._touch_binding(binding=binding, now=now)
            return WechatSessionResolution(
                session_id=active_session_id,
                is_new_session=False,
                reason="pending_clarification",
                normalized_text=normalized_text,
            )

        if self._should_roll_for_idle(binding=binding, now=now):
            session_id = self._create_and_bind(
                channel=channel,
                external_user_id=event.user_id,
                now=now,
                reason="idle_timeout",
            )
            return WechatSessionResolution(
                session_id=session_id,
                is_new_session=True,
                reason="idle_timeout",
                normalized_text=normalized_text,
            )

        if self._should_roll_for_daily_boundary(binding=binding, now=now):
            session_id = self._create_and_bind(
                channel=channel,
                external_user_id=event.user_id,
                now=now,
                reason="daily_reset",
            )
            return WechatSessionResolution(
                session_id=session_id,
                is_new_session=True,
                reason="daily_reset",
                normalized_text=normalized_text,
            )

        self._touch_binding(binding=binding, now=now)
        return WechatSessionResolution(
            session_id=active_session_id,
            is_new_session=False,
            reason="reuse_existing",
            normalized_text=normalized_text,
        )

    def _create_and_bind(self, *, channel: str, external_user_id: str, now: datetime, reason: str) -> str:
        session = self.clawbot_service.create_session()
        self.session_map_repository.upsert(
            channel=channel,
            external_user_id=external_user_id,
            session_id=session.id,
            session_started_at=now,
            last_interaction_at=now,
            last_reset_reason=reason,
        )
        return session.id

    def _touch_binding(self, *, binding: ChannelSessionMapRecord, now: datetime) -> None:
        self.session_map_repository.upsert(
            channel=binding.channel,
            external_user_id=binding.external_user_id,
            session_id=binding.session_id,
            session_started_at=binding.session_started_at or now,
            last_interaction_at=now,
            last_reset_reason=binding.last_reset_reason,
        )

    def _active_session_id(self, *, binding: ChannelSessionMapRecord) -> str | None:
        session_id = str(binding.session_id or "").strip()
        if not session_id:
            return None
        try:
            self.clawbot_service.session_repository.get(session_id)
        except KeyError:
            return None
        return session_id

    def _should_roll_for_idle(self, *, binding: ChannelSessionMapRecord, now: datetime) -> bool:
        if self.idle_minutes <= 0:
            return False
        last_seen = binding.last_interaction_at or binding.session_started_at or binding.updated_at or binding.created_at
        if last_seen is None:
            return False
        return now - self._as_utc(last_seen) >= timedelta(minutes=self.idle_minutes)

    def _should_roll_for_daily_boundary(self, *, binding: ChannelSessionMapRecord, now: datetime) -> bool:
        if self.daily_reset_hour is None:
            return False
        started_at = binding.session_started_at or binding.created_at or binding.updated_at
        if started_at is None:
            return False
        boundary = self._latest_reset_boundary(now=now)
        if boundary is None:
            return False
        return self._as_utc(started_at) < boundary

    def _latest_reset_boundary(self, *, now: datetime) -> datetime | None:
        if self.daily_reset_hour is None:
            return None
        local_now = now.astimezone(self.timezone)
        boundary = datetime.combine(local_now.date(), time(self.daily_reset_hour, 0), tzinfo=self.timezone)
        if local_now < boundary:
            boundary -= timedelta(days=1)
        return boundary.astimezone(UTC)

    @staticmethod
    def _has_message_payload(*, text: str | None, event: WechatInboundEvent) -> bool:
        if text and text.strip():
            return True
        return bool((event.file_path and event.file_name) or event.file_path)

    @classmethod
    def _parse_manual_reset(cls, text: str | None) -> _ManualResetCommand | None:
        if not text:
            return None
        match = cls.MANUAL_RESET_PATTERN.match(text)
        if match is None:
            return None
        rest = str(match.group("rest") or "").strip()
        return _ManualResetCommand(remaining_text=rest or None)

    @staticmethod
    def _event_time(event: WechatInboundEvent) -> datetime:
        if event.create_time_ms is not None:
            return datetime.fromtimestamp(max(0, event.create_time_ms) / 1000, tz=UTC)
        return datetime.now(UTC)

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
