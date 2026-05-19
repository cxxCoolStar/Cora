from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
import re
from typing import Any
from zoneinfo import ZoneInfo

try:
    from croniter import croniter
except ImportError:  # pragma: no cover - dependency guard
    croniter = None

DEFAULT_SILENT_REPLY = "[SILENT]"

_DURATION_RE = re.compile(
    r"^\s*(?P<value>\d+)\s*(?P<unit>s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days)\s*$",
    re.IGNORECASE,
)
_CLOCK_RE = re.compile(r"^\s*(?P<hour>\d{1,2}):(?P<minute>\d{2})\s*$")
_CRON_PART_RE = re.compile(r"^[\d\*\-,/]+$")
_WEEKDAY_ALIASES = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tues": 1,
    "tuesday": 1,
    "wed": 2,
    "wednesday": 2,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
}

_INTERVAL_SECONDS_KEYS = (
    "interval_seconds",
    "intervval_seconds",
    "innterval_seconds",
    "every_seconds",
    "delay_seconds",
    "after_seconds",
)
_INTERVAL_MINUTES_KEYS = (
    "interval_minutes",
    "intervval_minutes",
    "innterval_minutes",
    "every_minutes",
    "delay_minutes",
    "after_minutes",
)


def _current_time() -> datetime:
    return datetime.now(UTC)


def _default_timezone(default_timezone: str | None) -> str:
    if default_timezone and default_timezone.strip():
        return default_timezone.strip()
    local_tz = datetime.now().astimezone().tzinfo
    return str(local_tz or "UTC")


def _parse_duration_seconds(value: str) -> int:
    match = _DURATION_RE.match(str(value or ""))
    if match is None:
        raise ValueError("Invalid duration. Use formats like 30m, 2h, 1d, or 45s.")
    amount = int(match.group("value"))
    unit = match.group("unit").lower()
    if unit.startswith("s"):
        return amount
    if unit.startswith("m"):
        return amount * 60
    if unit.startswith("h"):
        return amount * 3600
    return amount * 86400


def _parse_clock(value: str) -> tuple[int, int]:
    match = _CLOCK_RE.match(str(value or ""))
    if match is None:
        raise ValueError("Invalid clock time. Use HH:MM, for example 09:30.")
    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError("Clock time is out of range.")
    return hour, minute


def _normalize_timezone(value: str | None, default_timezone: str | None) -> str:
    timezone_name = _default_timezone(default_timezone) if not value else value.strip()
    try:
        ZoneInfo(timezone_name)
    except Exception as exc:  # pragma: no cover - defensive
        raise ValueError(f"Unknown timezone: {timezone_name}") from exc
    return timezone_name


def _coerce_datetime(value: str, *, timezone_name: str | None) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError("Missing datetime value.")
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("Invalid datetime. Use ISO 8601, for example 2026-05-18T09:30:00+08:00.") from exc
    if parsed.tzinfo is None:
        tz = ZoneInfo(_normalize_timezone(timezone_name, timezone_name))
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(UTC)


def _first_present_value(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload and payload.get(key) is not None:
            return payload.get(key)
    return None


def _extract_delay_seconds(payload: dict[str, Any]) -> int | None:
    interval_seconds = _first_present_value(payload, *_INTERVAL_SECONDS_KEYS)
    if interval_seconds is not None:
        seconds = int(interval_seconds)
        return seconds if seconds > 0 else None
    interval_minutes = _first_present_value(payload, *_INTERVAL_MINUTES_KEYS)
    if interval_minutes is not None:
        minutes = int(interval_minutes)
        if minutes > 0:
            return minutes * 60
    every_value = payload.get("every")
    if every_value is not None:
        seconds = int(every_value)
        return seconds if seconds > 0 else None
    return None


def _normalize_weekdays(values: Any) -> list[int]:
    if values is None:
        raise ValueError("Weekly schedules require days_of_week.")
    if isinstance(values, str):
        raw_values = [part.strip() for part in values.split(",")]
    else:
        raw_values = list(values)
    normalized: list[int] = []
    for raw in raw_values:
        if isinstance(raw, int):
            day = raw
        else:
            text = str(raw or "").strip().lower()
            if text == "":
                continue
            if text.isdigit():
                day = int(text)
            else:
                if text not in _WEEKDAY_ALIASES:
                    raise ValueError(f"Unknown weekday: {raw}")
                day = _WEEKDAY_ALIASES[text]
        if day < 0 or day > 6:
            raise ValueError("days_of_week values must be between 0 (Monday) and 6 (Sunday).")
        if day not in normalized:
            normalized.append(day)
    if not normalized:
        raise ValueError("Weekly schedules require at least one weekday.")
    return sorted(normalized)


def _schedule_from_text(schedule_text: str, *, default_timezone: str | None, now: datetime) -> dict[str, Any]:
    text = str(schedule_text or "").strip()
    lowered = text.lower()
    cron_text = text[5:].strip() if lowered.startswith("cron ") else text
    cron_parts = cron_text.split()
    if len(cron_parts) == 5 and all(_CRON_PART_RE.match(part) for part in cron_parts):
        return {
            "kind": "cron",
            "expr": cron_text,
            "timezone": _normalize_timezone(None, default_timezone),
        }
    if lowered.startswith("every "):
        return {"kind": "interval", "interval_seconds": _parse_duration_seconds(text[6:])}
    if lowered.startswith("in "):
        return {"kind": "once", "at": (now + timedelta(seconds=_parse_duration_seconds(text[3:]))).isoformat()}
    if lowered.startswith("daily "):
        hour, minute = _parse_clock(text[6:].split(" ", 1)[0])
        remainder = text[6:].split(" ", 1)
        timezone_name = remainder[1].strip() if len(remainder) > 1 else None
        return {
            "kind": "daily",
            "hour": hour,
            "minute": minute,
            "timezone": _normalize_timezone(timezone_name, default_timezone),
        }
    if lowered.startswith("weekly "):
        body = text[7:].strip()
        if " " not in body:
            raise ValueError("Weekly schedules must look like 'weekly mon,wed 09:30 Asia/Shanghai'.")
        weekday_part, remainder = body.split(" ", 1)
        if " " in remainder:
            clock_part, timezone_name = remainder.split(" ", 1)
            timezone_name = timezone_name.strip()
        else:
            clock_part, timezone_name = remainder, None
        hour, minute = _parse_clock(clock_part)
        return {
            "kind": "weekly",
            "days_of_week": _normalize_weekdays(weekday_part),
            "hour": hour,
            "minute": minute,
            "timezone": _normalize_timezone(timezone_name, default_timezone),
        }
    if lowered.startswith("at "):
        return {"kind": "once", "at": _coerce_datetime(text[3:], timezone_name=default_timezone).isoformat()}
    return {"kind": "once", "at": _coerce_datetime(text, timezone_name=default_timezone).isoformat()}


def normalize_schedule_input(
    schedule: dict[str, Any] | None = None,
    *,
    schedule_text: str | None = None,
    default_timezone: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current_time = now or _current_time()
    raw = dict(schedule or {})
    if not raw:
        if not schedule_text or not schedule_text.strip():
            raise ValueError("A scheduled task requires either schedule or schedule_text.")
        raw = _schedule_from_text(
            schedule_text,
            default_timezone=default_timezone,
            now=current_time.astimezone(UTC),
        )

    kind = str(raw.get("kind") or "").strip().lower()
    if kind in {"every"}:
        kind = "interval"

    if kind == "once":
        at_value = str(raw.get("at") or raw.get("run_at") or "").strip()
        if at_value:
            at = _coerce_datetime(
                at_value,
                timezone_name=raw.get("timezone") if isinstance(raw.get("timezone"), str) else default_timezone,
            )
        else:
            delay_seconds = _extract_delay_seconds(raw)
            if delay_seconds is None:
                raise ValueError("One-time schedules require either at/run_at or a positive delay.")
            at = current_time.astimezone(UTC) + timedelta(seconds=delay_seconds)
        return {
            "kind": "once",
            "at": at.isoformat(),
        }

    if kind == "interval":
        interval_seconds = _extract_delay_seconds(raw)
        if interval_seconds is None:
            raise ValueError("Interval schedules require interval_seconds or interval_minutes.")
        seconds = int(interval_seconds)
        if seconds <= 0:
            raise ValueError("Interval schedules require a positive interval.")
        normalized: dict[str, Any] = {
            "kind": "interval",
            "interval_seconds": seconds,
        }
        anchor_at = raw.get("anchor_at")
        if anchor_at:
            normalized["anchor_at"] = _coerce_datetime(str(anchor_at), timezone_name=default_timezone).isoformat()
        return normalized

    if kind == "daily":
        timezone_name = _normalize_timezone(
            raw.get("timezone") if isinstance(raw.get("timezone"), str) else None,
            default_timezone,
        )
        hour = int(raw.get("hour"))
        minute = int(raw.get("minute") or 0)
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            raise ValueError("Daily schedule time is out of range.")
        return {
            "kind": "daily",
            "hour": hour,
            "minute": minute,
            "timezone": timezone_name,
        }

    if kind == "weekly":
        timezone_name = _normalize_timezone(
            raw.get("timezone") if isinstance(raw.get("timezone"), str) else None,
            default_timezone,
        )
        hour = int(raw.get("hour"))
        minute = int(raw.get("minute") or 0)
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            raise ValueError("Weekly schedule time is out of range.")
        return {
            "kind": "weekly",
            "days_of_week": _normalize_weekdays(raw.get("days_of_week")),
            "hour": hour,
            "minute": minute,
            "timezone": timezone_name,
        }

    if kind == "cron":
        expr = str(raw.get("expr") or raw.get("expression") or "").strip()
        if not expr:
            raise ValueError("Cron schedules require expr.")
        if croniter is None:
            raise ValueError("Cron schedules require the croniter package.")
        try:
            croniter(expr)
        except Exception as exc:
            raise ValueError(f"Invalid cron expression: {expr}") from exc
        return {
            "kind": "cron",
            "expr": expr,
            "timezone": _normalize_timezone(
                raw.get("timezone") if isinstance(raw.get("timezone"), str) else None,
                default_timezone,
            ),
        }

    raise ValueError("Unsupported schedule kind. Use once, interval, daily, weekly, or cron.")


def compute_next_run_at(
    schedule: dict[str, Any],
    *,
    now: datetime | None = None,
    last_run_at: datetime | None = None,
) -> datetime | None:
    current_time = (now or _current_time()).astimezone(UTC)
    kind = str(schedule.get("kind") or "").strip().lower()

    if kind == "once":
        if last_run_at is not None:
            return None
        return _coerce_datetime(str(schedule.get("at") or ""), timezone_name="UTC")

    if kind == "interval":
        seconds = int(schedule.get("interval_seconds") or 0)
        if seconds <= 0:
            raise ValueError("Interval schedules require a positive interval.")
        if last_run_at is not None:
            return last_run_at.astimezone(UTC) + timedelta(seconds=seconds)
        anchor_at = schedule.get("anchor_at")
        if anchor_at:
            anchor = _coerce_datetime(str(anchor_at), timezone_name="UTC")
            if anchor > current_time:
                return anchor
            elapsed = (current_time - anchor).total_seconds()
            steps = int(elapsed // seconds) + 1
            return anchor + timedelta(seconds=steps * seconds)
        return current_time + timedelta(seconds=seconds)

    if kind in {"daily", "weekly"}:
        timezone_name = _normalize_timezone(
            schedule.get("timezone") if isinstance(schedule.get("timezone"), str) else None,
            None,
        )
        tz = ZoneInfo(timezone_name)
        reference = (last_run_at or current_time).astimezone(tz)
        if last_run_at is not None:
            reference = reference + timedelta(seconds=1)
        hour = int(schedule.get("hour") or 0)
        minute = int(schedule.get("minute") or 0)
        days_of_week = (
            list(schedule.get("days_of_week") or [])
            if kind == "weekly"
            else list(range(7))
        )
        for offset in range(0, 8):
            candidate_date = (reference + timedelta(days=offset)).date()
            candidate = datetime.combine(candidate_date, time(hour=hour, minute=minute), tzinfo=tz)
            if candidate.weekday() not in days_of_week:
                continue
            if candidate >= reference:
                return candidate.astimezone(UTC)
        raise ValueError("Unable to compute the next run time for this schedule.")

    if kind == "cron":
        if croniter is None:
            raise ValueError("Cron schedules require the croniter package.")
        timezone_name = _normalize_timezone(
            schedule.get("timezone") if isinstance(schedule.get("timezone"), str) else None,
            None,
        )
        tz = ZoneInfo(timezone_name)
        base = (last_run_at or current_time).astimezone(tz)
        expr = str(schedule.get("expr") or "").strip()
        if not expr:
            raise ValueError("Cron schedules require expr.")
        next_run = croniter(expr, base).get_next(datetime)
        if next_run.tzinfo is None:
            next_run = next_run.replace(tzinfo=tz)
        return next_run.astimezone(UTC)

    raise ValueError("Unsupported schedule kind.")


def format_schedule(schedule: dict[str, Any]) -> str:
    kind = str(schedule.get("kind") or "").strip().lower()
    if kind == "once":
        return f"once at {str(schedule.get('at') or '').strip()}"
    if kind == "interval":
        seconds = int(schedule.get("interval_seconds") or 0)
        if seconds % 3600 == 0:
            return f"every {seconds // 3600}h"
        if seconds % 60 == 0:
            return f"every {seconds // 60}m"
        return f"every {seconds}s"
    if kind == "daily":
        return f"daily {int(schedule.get('hour') or 0):02d}:{int(schedule.get('minute') or 0):02d} {schedule.get('timezone') or 'UTC'}"
    if kind == "weekly":
        days = ",".join(str(day) for day in list(schedule.get("days_of_week") or []))
        return (
            f"weekly {days} "
            f"{int(schedule.get('hour') or 0):02d}:{int(schedule.get('minute') or 0):02d} "
            f"{schedule.get('timezone') or 'UTC'}"
        )
    if kind == "cron":
        expr = str(schedule.get("expr") or "").strip()
        timezone_name = str(schedule.get("timezone") or "UTC").strip() or "UTC"
        return f"cron {expr} {timezone_name}".strip()
    return "unknown schedule"
