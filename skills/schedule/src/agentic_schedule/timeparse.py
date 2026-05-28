from __future__ import annotations

import re
from datetime import datetime, time as day_time, timedelta, timezone
from typing import Any

import dateparser

from .config import INTERVAL_RE, WEEKDAYS, ZoneInfo


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_dt(value: datetime) -> str:
    return (
        value.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def resolve_timezone(timezone_name: str | None) -> Any:
    if not timezone_name or timezone_name == "local":
        return datetime.now().astimezone().tzinfo
    if timezone_name.upper() == "UTC":
        return timezone.utc
    if ZoneInfo is None:
        raise ValueError("IANA timezone support is unavailable in this Python runtime")
    try:
        return ZoneInfo(timezone_name)
    except Exception as exc:  # zoneinfo raises ZoneInfoNotFoundError, but keep stdlib-only typing simple.
        raise ValueError(f"invalid timezone: {timezone_name}") from exc


def parse_duration_seconds(raw_value: str) -> int:
    base = utc_now()
    parsed = parse_datetime(f"in {raw_value}", None, base)
    seconds = int((parsed - base).total_seconds())
    if seconds <= 0:
        raise ValueError(f"invalid duration: {raw_value}")
    return seconds


def parse_retention_seconds(raw_value: str) -> int:
    return parse_duration_seconds(raw_value)


def parse_datetime(
    raw_value: str, timezone_name: str | None, relative_base: datetime | None = None
) -> datetime:
    value = raw_value.strip()
    if not value:
        raise ValueError("datetime value must be non-empty")

    base = relative_base or utc_now()
    tz = resolve_timezone(timezone_name)
    settings = {
        "RELATIVE_BASE": base.astimezone(tz),
        "RETURN_AS_TIMEZONE_AWARE": True,
        "TIMEZONE": timezone_name or "local",
        "TO_TIMEZONE": "UTC",
    }
    parsed = dateparser.parse(value, settings=settings)
    if parsed is None:
        raise ValueError(f"invalid datetime: {raw_value}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(timezone.utc)


def parse_time_of_day(raw_value: str) -> day_time:
    parts = raw_value.strip().split(":")
    if len(parts) not in {2, 3}:
        raise ValueError(
            f"invalid time of day: {raw_value}; expected HH:MM or HH:MM:SS"
        )
    try:
        hour = int(parts[0])
        minute = int(parts[1])
        second = int(parts[2]) if len(parts) == 3 else 0
        return day_time(hour=hour, minute=minute, second=second)
    except ValueError as exc:
        raise ValueError(
            f"invalid time of day: {raw_value}; expected HH:MM or HH:MM:SS"
        ) from exc


def format_time_of_day(value: day_time) -> str:
    return value.strftime("%H:%M:%S")


def next_daily_run(spec: dict[str, Any], after: datetime) -> datetime:
    tz = resolve_timezone(spec.get("timezone"))
    after_local = after.astimezone(tz)
    tod = parse_time_of_day(str(spec["time"]))
    candidate = datetime.combine(after_local.date(), tod, tzinfo=tz)
    if candidate <= after_local:
        candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def next_weekly_run(spec: dict[str, Any], after: datetime) -> datetime:
    tz = resolve_timezone(spec.get("timezone"))
    after_local = after.astimezone(tz)
    tod = parse_time_of_day(str(spec["time"]))
    weekday = int(spec["weekday"])
    days_ahead = (weekday - after_local.weekday()) % 7
    candidate_date = after_local.date() + timedelta(days=days_ahead)
    candidate = datetime.combine(candidate_date, tod, tzinfo=tz)
    if candidate <= after_local:
        candidate += timedelta(days=7)
    return candidate.astimezone(timezone.utc)


def next_interval_run(spec: dict[str, Any], after: datetime) -> datetime:
    seconds = int(spec["seconds"])
    start_at = parse_utc(str(spec["start_at"]))
    if start_at is None:
        start_at = after + timedelta(seconds=seconds)
    if start_at > after:
        return start_at
    elapsed = (after - start_at).total_seconds()
    steps = int(elapsed // seconds) + 1
    return start_at + timedelta(seconds=steps * seconds)


def compute_next_run(schedule: dict[str, Any], after: datetime) -> datetime | None:
    spec = schedule.get("schedule")
    if not isinstance(spec, dict):
        raise ValueError(
            f"schedule {schedule.get('name', '<unknown>')} has invalid schedule metadata"
        )

    kind = spec.get("kind")
    if kind == "once":
        target = parse_utc(str(spec.get("at", "")))
        if target is None:
            raise ValueError("one-time schedule is missing at timestamp")
        return target if target > after else None
    if kind == "interval":
        return next_interval_run(spec, after)
    if kind == "daily":
        return next_daily_run(spec, after)
    if kind == "weekly":
        return next_weekly_run(spec, after)
    raise ValueError(f"unsupported schedule kind: {kind}")


def parse_when(
    raw_when: str, timezone_name: str | None
) -> tuple[dict[str, Any], datetime]:
    value = raw_when.strip()
    lower = value.lower()
    now = utc_now()
    stored_timezone = timezone_name or "local"

    if lower in {"now", "once now", "at now"}:
        spec = {"kind": "once", "at": format_dt(now)}
        return spec, now

    if lower.startswith("in "):
        target = parse_datetime(value, stored_timezone, now)
        spec = {"kind": "once", "at": format_dt(target)}
        return spec, target

    if lower.startswith("at "):
        target = parse_datetime(value[3:], stored_timezone, now)
        validate_one_time_target(target, now)
        spec = {"kind": "once", "at": format_dt(max_datetime(target, now))}
        return spec, max_datetime(target, now)

    every_match = INTERVAL_RE.match(value)
    if every_match:
        seconds = parse_duration_seconds(every_match.group(1))
        start_raw = every_match.group(2)
        if start_raw:
            start_at = parse_datetime(start_raw, stored_timezone, now)
            spec = {
                "kind": "interval",
                "seconds": seconds,
                "start_at": format_dt(start_at),
            }
            if start_at >= now - timedelta(seconds=1):
                return spec, max_datetime(start_at, now)
            return spec, next_interval_run(spec, now)
        start_at = now + timedelta(seconds=seconds)
        spec = {"kind": "interval", "seconds": seconds, "start_at": format_dt(start_at)}
        return spec, start_at

    daily_match = re.match(
        r"^daily\s+(\d{1,2}:\d{2}(?::\d{2})?)$", value, flags=re.IGNORECASE
    )
    if daily_match:
        tod = parse_time_of_day(daily_match.group(1))
        spec = {
            "kind": "daily",
            "time": format_time_of_day(tod),
            "timezone": stored_timezone,
        }
        return spec, next_daily_run(spec, now)

    weekly_match = re.match(
        r"^weekly\s+([A-Za-z]+)\s+(\d{1,2}:\d{2}(?::\d{2})?)$",
        value,
        flags=re.IGNORECASE,
    )
    if weekly_match:
        weekday_name = weekly_match.group(1).lower()
        if weekday_name not in WEEKDAYS:
            raise ValueError(f"invalid weekday: {weekly_match.group(1)}")
        tod = parse_time_of_day(weekly_match.group(2))
        spec = {
            "kind": "weekly",
            "weekday": WEEKDAYS[weekday_name],
            "weekday_name": weekday_name,
            "time": format_time_of_day(tod),
            "timezone": stored_timezone,
        }
        return spec, next_weekly_run(spec, now)

    try:
        target = parse_datetime(value, stored_timezone, now)
    except ValueError as exc:
        raise ValueError(
            "when must use one of: 'now', 'in <duration>', 'at <ISO datetime>', "
            "'every <duration> [starting <ISO datetime|now>]', 'daily HH:MM', or 'weekly <weekday> HH:MM'"
        ) from exc
    validate_one_time_target(target, now)
    spec = {"kind": "once", "at": format_dt(max_datetime(target, now))}
    return spec, max_datetime(target, now)


def validate_one_time_target(target: datetime, now: datetime) -> None:
    if target < now - timedelta(minutes=1):
        raise ValueError("one-time schedule is in the past")


def max_datetime(left: datetime, right: datetime) -> datetime:
    return left if left >= right else right
