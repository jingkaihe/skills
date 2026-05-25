from __future__ import annotations

import contextlib
import copy
import json
import logging
import os
import re
import shlex
import subprocess
import sys
import time
import uuid
from datetime import datetime, time as day_time, timedelta, timezone
from pathlib import Path
from typing import Any

import dateparser
import structlog

try:
    import fcntl
except ImportError:  # pragma: no cover - fcntl is unavailable on Windows.
    fcntl = None  # type: ignore[assignment]

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python 3.11 includes zoneinfo.
    ZoneInfo = None  # type: ignore[assignment]


STATE_VERSION = 1
DEFAULT_KODELET_FLAGS = ["--headless"]
DEFAULT_POLL_SECONDS = 30
DEFAULT_EXECUTION_DEADLINE_SECONDS = 10 * 60
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
INTERVAL_RE = re.compile(r"^every\s+(.+?)(?:\s+starting\s+(.+))?$", re.IGNORECASE)
CONTEXT_ENV_KEYS = {
    "KODELET_CONVERSATION_ID",
    "KODELET_WORKING_DIR",
    "KODELET_PROVIDER",
    "KODELET_MODEL",
    "KODELET_PROFILE",
}
WEEKDAYS = {
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

_LOGGING_CONFIGURED = False


def emit_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def emit_error(message: str) -> int:
    emit_json({"error": message})
    return 1


def configure_json_logging() -> None:
    global _LOGGING_CONFIGURED
    if _LOGGING_CONFIGURED:
        return
    logging.basicConfig(format="%(message)s", level=logging.INFO)
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso", utc=True, key="timestamp"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _LOGGING_CONFIGURED = True


def logger() -> Any:
    configure_json_logging()
    return structlog.get_logger("agentic_schedule")


def load_payload(raw_input: str) -> dict[str, Any]:
    if not raw_input.strip():
        return {}
    payload = json.loads(raw_input)
    if not isinstance(payload, dict):
        raise ValueError("input must be a JSON object")
    return payload


def optional_bool(payload: dict[str, Any], key: str, default: bool) -> bool:
    value = payload.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value


def required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required and must be a non-empty string")
    return value.strip()


def optional_string(payload: dict[str, Any], key: str, default: str | None = None) -> str | None:
    value = payload.get(key, default)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    value = value.strip()
    return value if value else default


def reject_unknown_keys(payload: dict[str, Any], allowed_keys: set[str]) -> None:
    unknown_keys = sorted(set(payload) - allowed_keys)
    if unknown_keys:
        raise ValueError(f"unsupported parameter(s): {', '.join(unknown_keys)}")


def schedule_dir() -> Path:
    raw_path = os.environ.get("KODELET_SCHEDULE_DIR") or os.environ.get("CUSTOM_TOOL_SCHEDULE_DIR")
    if not raw_path:
        return Path.home() / ".kodelet" / "schedules"
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


def schedule_file() -> Path:
    return schedule_dir() / "schedules.json"


def lock_file() -> Path:
    return schedule_dir() / "schedules.lock"


def pid_file() -> Path:
    return schedule_dir() / "dispatcher.pid.json"


def dispatcher_log_file() -> Path:
    return schedule_dir() / "dispatcher.log"


def logs_dir() -> Path:
    return schedule_dir() / "logs"


def run_records_dir() -> Path:
    return schedule_dir() / "runs"


@contextlib.contextmanager
def state_lock() -> Any:
    schedule_dir().mkdir(parents=True, exist_ok=True)
    handle = lock_file().open("a+", encoding="utf-8")
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def default_state() -> dict[str, Any]:
    return {"version": STATE_VERSION, "schedules": {}}


def load_state_unlocked() -> dict[str, Any]:
    path = schedule_file()
    if not path.exists():
        return default_state()

    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"failed to parse schedule file {path}: {exc}") from exc

    if not isinstance(state, dict):
        raise ValueError(f"schedule file has invalid format: {path}")

    schedules = state.get("schedules", {})
    if isinstance(schedules, list):
        schedules = {str(item.get("name")): item for item in schedules if isinstance(item, dict) and item.get("name")}
    if not isinstance(schedules, dict):
        raise ValueError(f"schedule file has invalid schedules object: {path}")

    state["version"] = int(state.get("version", STATE_VERSION))
    state["schedules"] = schedules
    return state


def save_state_unlocked(state: dict[str, Any]) -> None:
    path = schedule_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp_path, path)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_dt(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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


def parse_datetime(raw_value: str, timezone_name: str | None, relative_base: datetime | None = None) -> datetime:
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
        raise ValueError(f"invalid time of day: {raw_value}; expected HH:MM or HH:MM:SS")
    try:
        hour = int(parts[0])
        minute = int(parts[1])
        second = int(parts[2]) if len(parts) == 3 else 0
        return day_time(hour=hour, minute=minute, second=second)
    except ValueError as exc:
        raise ValueError(f"invalid time of day: {raw_value}; expected HH:MM or HH:MM:SS") from exc


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
        raise ValueError(f"schedule {schedule.get('name', '<unknown>')} has invalid schedule metadata")

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


def parse_when(raw_when: str, timezone_name: str | None) -> tuple[dict[str, Any], datetime]:
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
            spec = {"kind": "interval", "seconds": seconds, "start_at": format_dt(start_at)}
            if start_at >= now - timedelta(seconds=1):
                return spec, max_datetime(start_at, now)
            return spec, next_interval_run(spec, now)
        start_at = now + timedelta(seconds=seconds)
        spec = {"kind": "interval", "seconds": seconds, "start_at": format_dt(start_at)}
        return spec, start_at

    daily_match = re.match(r"^daily\s+(\d{1,2}:\d{2}(?::\d{2})?)$", value, flags=re.IGNORECASE)
    if daily_match:
        tod = parse_time_of_day(daily_match.group(1))
        spec = {"kind": "daily", "time": format_time_of_day(tod), "timezone": stored_timezone}
        return spec, next_daily_run(spec, now)

    weekly_match = re.match(r"^weekly\s+([A-Za-z]+)\s+(\d{1,2}:\d{2}(?::\d{2})?)$", value, flags=re.IGNORECASE)
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


def validate_name(name: str) -> None:
    if not NAME_RE.fullmatch(name):
        raise ValueError("name must start with a letter or number and contain only letters, numbers, '.', '_' or '-'")


def validate_environment(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("environment must be an object whose values are strings")
    environment: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key:
            raise ValueError("environment keys must be non-empty strings")
        if not isinstance(item, str):
            raise ValueError(f"environment value for {key} must be a string")
        environment[key] = item
    return environment


def build_schedule(payload: dict[str, Any]) -> dict[str, Any]:
    name = required_string(payload, "name")
    validate_name(name)
    instruction = required_string(payload, "instruction")
    when = required_string(payload, "when")
    timezone_name = optional_string(payload, "timezone", None)
    if timezone_name:
        resolve_timezone(timezone_name)

    spec, next_run_at = parse_when(when, timezone_name)
    enabled = optional_bool(payload, "enabled", True)
    working_directory = optional_string(payload, "working_directory", None)
    if working_directory is None:
        working_directory = os.environ.get("KODELET_WORKING_DIR") or os.getcwd()
    working_directory = str(Path(working_directory).expanduser())

    now = format_dt(utc_now())
    status = "active" if enabled else "disabled"
    if spec["kind"] == "once" and enabled:
        status = "pending"

    return {
        "name": name,
        "instruction": instruction,
        "when": when,
        "schedule": spec,
        "timezone": timezone_name or "local",
        "enabled": enabled,
        "status": status,
        "working_directory": working_directory,
        "kodelet_command": "kodelet",
        "kodelet_flags": list(DEFAULT_KODELET_FLAGS),
        "environment": validate_environment(payload.get("environment")),
        "next_run_at": format_dt(next_run_at),
        "execution_deadline_seconds": DEFAULT_EXECUTION_DEADLINE_SECONDS,
        "created_at": now,
        "updated_at": now,
        "run_count": 0,
        "last_run_id": None,
        "last_started_at": None,
        "last_finished_at": None,
        "last_run_status": None,
        "last_exit_code": None,
        "last_pid": None,
        "last_log_path": None,
        "last_error": None,
    }


def redact_schedule(schedule: dict[str, Any], include_environment: bool = False) -> dict[str, Any]:
    redacted = copy.deepcopy(schedule)
    redacted.pop("kodelet_command", None)
    redacted.pop("kodelet_flags", None)
    environment = redacted.get("environment")
    if isinstance(environment, dict) and not include_environment:
        redacted["environment"] = {key: "<redacted>" for key in sorted(environment)}
        redacted["environment_keys"] = sorted(environment)
    return redacted


def sorted_schedules(state: dict[str, Any]) -> list[dict[str, Any]]:
    schedules = state.get("schedules", {})
    return [schedules[name] for name in sorted(schedules)]


def is_active_schedule(schedule: dict[str, Any]) -> bool:
    return bool(schedule.get("enabled")) and bool(schedule.get("next_run_at"))


def active_schedule_count(state: dict[str, Any]) -> int:
    return sum(1 for schedule in state.get("schedules", {}).values() if isinstance(schedule, dict) and is_active_schedule(schedule))


def create_schedule_tool(raw_input: str) -> int:
    try:
        payload = load_payload(raw_input)
        reject_unknown_keys(
            payload,
            {"name", "instruction", "when", "timezone", "working_directory", "environment", "overwrite", "enabled"},
        )
        schedule = build_schedule(payload)
        overwrite = optional_bool(payload, "overwrite", False)
    except json.JSONDecodeError as exc:
        return emit_error(f"invalid JSON input: {exc}")
    except ValueError as exc:
        return emit_error(str(exc))

    with state_lock():
        state = load_state_unlocked()
        schedules = state["schedules"]
        if schedule["name"] in schedules and not overwrite:
            return emit_error(f"schedule already exists: {schedule['name']} (set overwrite=true to replace it)")
        schedules[schedule["name"]] = schedule
        save_state_unlocked(state)

    dispatcher = dispatcher_status()
    if schedule["enabled"]:
        dispatcher = ensure_dispatcher_running()

    emit_json(
        {
            "status": "success",
            "message": f"Schedule {schedule['name']} has been created",
            "schedule_file": str(schedule_file()),
            "dispatcher": dispatcher,
            "schedule": redact_schedule(schedule),
        }
    )
    return 0


def list_schedule_tool(raw_input: str) -> int:
    try:
        payload = load_payload(raw_input)
        reject_unknown_keys(payload, {"include_inactive", "include_environment"})
        include_inactive = optional_bool(payload, "include_inactive", True)
        include_environment = optional_bool(payload, "include_environment", False)
    except json.JSONDecodeError as exc:
        return emit_error(f"invalid JSON input: {exc}")
    except ValueError as exc:
        return emit_error(str(exc))

    try:
        with state_lock():
            state = load_state_unlocked()
            schedules = [schedule for schedule in sorted_schedules(state) if include_inactive or is_active_schedule(schedule)]
            active_count = active_schedule_count(state)
    except ValueError as exc:
        return emit_error(str(exc))

    dispatcher = dispatcher_status()
    if active_count > 0:
        dispatcher = ensure_dispatcher_running()

    emit_json(
        {
            "status": "success",
            "schedule_file": str(schedule_file()),
            "dispatcher": dispatcher,
            "count": len(schedules),
            "active_count": active_count,
            "schedules": [redact_schedule(schedule, include_environment) for schedule in schedules],
        }
    )
    return 0


def get_schedule_tool(raw_input: str) -> int:
    try:
        payload = load_payload(raw_input)
        reject_unknown_keys(payload, {"name", "include_environment"})
        name = required_string(payload, "name")
        include_environment = optional_bool(payload, "include_environment", False)
    except json.JSONDecodeError as exc:
        return emit_error(f"invalid JSON input: {exc}")
    except ValueError as exc:
        return emit_error(str(exc))

    try:
        with state_lock():
            state = load_state_unlocked()
            schedule = state["schedules"].get(name)
    except ValueError as exc:
        return emit_error(str(exc))

    if schedule is None:
        return emit_error(f"schedule not found: {name}")

    emit_json(
        {
            "status": "success",
            "schedule_file": str(schedule_file()),
            "dispatcher": dispatcher_status(),
            "schedule": redact_schedule(schedule, include_environment),
        }
    )
    return 0


def delete_schedule_tool(raw_input: str) -> int:
    try:
        payload = load_payload(raw_input)
        reject_unknown_keys(payload, {"name", "missing_ok"})
        name = required_string(payload, "name")
        missing_ok = optional_bool(payload, "missing_ok", False)
    except json.JSONDecodeError as exc:
        return emit_error(f"invalid JSON input: {exc}")
    except ValueError as exc:
        return emit_error(str(exc))

    try:
        with state_lock():
            state = load_state_unlocked()
            schedule = state["schedules"].pop(name, None)
            if schedule is None and not missing_ok:
                return emit_error(f"schedule not found: {name}")
            save_state_unlocked(state)
    except ValueError as exc:
        return emit_error(str(exc))

    emit_json(
        {
            "status": "success",
            "message": f"Schedule {name} has been deleted" if schedule else f"Schedule {name} was already absent",
            "schedule_file": str(schedule_file()),
            "dispatcher": dispatcher_status(),
            "deleted_schedule": redact_schedule(schedule) if schedule else None,
        }
    )
    return 0


def process_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read_pid_payload() -> dict[str, Any] | None:
    path = pid_file()
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def dispatcher_status() -> dict[str, Any]:
    payload = read_pid_payload()
    if payload is None:
        return {"running": False, "pid_file": str(pid_file()), "log_path": str(dispatcher_log_file())}
    pid = int(payload.get("pid") or 0)
    running = process_running(pid)
    status = {
        "running": running,
        "pid": pid if pid else None,
        "pid_file": str(pid_file()),
        "log_path": payload.get("log_path") or str(dispatcher_log_file()),
        "started_at": payload.get("started_at"),
    }
    if not running and pid:
        status["stale_pid"] = pid
    return status


def dispatcher_disabled() -> bool:
    return os.environ.get("KODELET_SCHEDULE_DISABLE_DISPATCHER", "").strip().lower() in {"1", "true", "yes", "on"}


def clean_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in CONTEXT_ENV_KEYS:
        env.pop(key, None)
    return env


def ensure_dispatcher_running() -> dict[str, Any]:
    if dispatcher_disabled():
        status = dispatcher_status()
        status["disabled"] = True
        status["reason"] = "KODELET_SCHEDULE_DISABLE_DISPATCHER is set"
        return status

    with state_lock():
        status = dispatcher_status()
        if status.get("running"):
            return status

        schedule_dir().mkdir(parents=True, exist_ok=True)
        dispatcher_log_file().parent.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, "-m", "agentic_schedule.cli", "dispatch-loop"]
        with dispatcher_log_file().open("ab") as log_handle:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                env=clean_env(),
                start_new_session=True,
                close_fds=True,
            )
        payload = {"pid": process.pid, "started_at": format_dt(utc_now()), "log_path": str(dispatcher_log_file())}
        pid_file().write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return {"running": True, **payload, "pid_file": str(pid_file())}


def poll_seconds() -> int:
    raw_value = os.environ.get("KODELET_SCHEDULE_POLL_SECONDS", str(DEFAULT_POLL_SECONDS))
    try:
        return max(1, int(raw_value))
    except ValueError:
        return DEFAULT_POLL_SECONDS


def run_id_for(now: datetime) -> str:
    timestamp = now.strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{uuid.uuid4().hex[:8]}"


def run_record_path(name: str, run_id: str) -> Path:
    return run_records_dir() / name / f"{run_id}.json"


def log_path_for(name: str, run_id: str) -> Path:
    return logs_dir() / name / f"{run_id}.log"


def prepare_due_runs(state: dict[str, Any], now: datetime) -> tuple[list[dict[str, Any]], bool]:
    due_runs: list[dict[str, Any]] = []
    changed = False
    for name, schedule in sorted(state.get("schedules", {}).items()):
        if not isinstance(schedule, dict) or not is_active_schedule(schedule):
            continue
        next_run_at = parse_utc(schedule.get("next_run_at"))
        if next_run_at is None or next_run_at > now:
            continue

        deadline_seconds = int(schedule.get("execution_deadline_seconds") or DEFAULT_EXECUTION_DEADLINE_SECONDS)
        if (now - next_run_at).total_seconds() > deadline_seconds:
            schedule["last_started_at"] = None
            schedule["last_finished_at"] = format_dt(now)
            schedule["last_run_status"] = "skipped"
            schedule["last_exit_code"] = None
            schedule["last_pid"] = None
            schedule["last_log_path"] = None
            schedule["last_error"] = f"missed execution deadline by more than {deadline_seconds} seconds"
            schedule["updated_at"] = format_dt(now)
            logger().warning(
                "schedule_run_skipped_missed_deadline",
                schedule_name=name,
                scheduled_for=format_dt(next_run_at),
                deadline_seconds=deadline_seconds,
                lateness_seconds=round((now - next_run_at).total_seconds(), 3),
            )
            if schedule.get("schedule", {}).get("kind") == "once":
                schedule["enabled"] = False
                schedule["status"] = "skipped"
                schedule["next_run_at"] = None
            else:
                schedule["status"] = "active"
                next_after_skip = compute_next_run(schedule, now)
                schedule["next_run_at"] = format_dt(next_after_skip) if next_after_skip else None
            changed = True
            continue

        current_run_id = run_id_for(now)
        current_log_path = log_path_for(name, current_run_id)
        schedule_snapshot = copy.deepcopy(schedule)
        due_runs.append(
            {
                "name": name,
                "run_id": current_run_id,
                "schedule": schedule_snapshot,
                "log_path": str(current_log_path),
                "record_path": str(run_record_path(name, current_run_id)),
            }
        )

        schedule["last_run_id"] = current_run_id
        schedule["last_started_at"] = format_dt(now)
        schedule["last_finished_at"] = None
        schedule["last_run_status"] = "running"
        schedule["last_exit_code"] = None
        schedule["last_pid"] = None
        schedule["last_log_path"] = str(current_log_path)
        schedule["last_error"] = None
        schedule["run_count"] = int(schedule.get("run_count") or 0) + 1
        schedule["updated_at"] = format_dt(now)

        if schedule.get("schedule", {}).get("kind") == "once":
            schedule["enabled"] = False
            schedule["status"] = "running"
            schedule["next_run_at"] = None
        else:
            schedule["status"] = "active"
            next_after_dispatch = compute_next_run(schedule, now)
            schedule["next_run_at"] = format_dt(next_after_dispatch) if next_after_dispatch else None
        changed = True

    return due_runs, changed


def write_run_record(record: dict[str, Any]) -> None:
    path = Path(str(record["record_path"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def update_run_pid(name: str, run_id: str, pid: int) -> None:
    with state_lock():
        state = load_state_unlocked()
        schedule = state.get("schedules", {}).get(name)
        if isinstance(schedule, dict) and schedule.get("last_run_id") == run_id:
            schedule["last_pid"] = pid
            schedule["updated_at"] = format_dt(utc_now())
            save_state_unlocked(state)


def mark_run_failed_to_start(name: str, run_id: str, error: str) -> None:
    with state_lock():
        state = load_state_unlocked()
        schedule = state.get("schedules", {}).get(name)
        if isinstance(schedule, dict) and schedule.get("last_run_id") == run_id:
            schedule["last_run_status"] = "failed"
            schedule["last_exit_code"] = 127
            schedule["last_error"] = error
            if schedule.get("schedule", {}).get("kind") == "once":
                schedule["status"] = "failed"
            schedule["updated_at"] = format_dt(utc_now())
            save_state_unlocked(state)


def start_runner(record: dict[str, Any]) -> None:
    write_run_record(record)
    command = [sys.executable, "-m", "agentic_schedule.cli", "run-record", str(record["record_path"])]
    try:
        with dispatcher_log_file().open("ab") as log_handle:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                env=clean_env(),
                start_new_session=True,
                close_fds=True,
            )
    except OSError as exc:
        mark_run_failed_to_start(str(record["name"]), str(record["run_id"]), str(exc))
        logger().error(
            "schedule_run_start_failed",
            schedule_name=str(record["name"]),
            run_id=str(record["run_id"]),
            error=str(exc),
        )
        return
    update_run_pid(str(record["name"]), str(record["run_id"]), process.pid)
    logger().info(
        "schedule_run_started",
        schedule_name=str(record["name"]),
        run_id=str(record["run_id"]),
        pid=process.pid,
    )


def dispatch_due_schedules() -> tuple[int, int]:
    now = utc_now()
    with state_lock():
        state = load_state_unlocked()
        due_runs, changed = prepare_due_runs(state, now)
        if changed:
            save_state_unlocked(state)
        active_count = active_schedule_count(state)

    for record in due_runs:
        start_runner(record)

    return len(due_runs), active_count


def dispatcher_loop() -> int:
    own_pid = os.getpid()
    schedule_dir().mkdir(parents=True, exist_ok=True)
    with state_lock():
        pid_file().write_text(
            json.dumps({"pid": own_pid, "started_at": format_dt(utc_now()), "log_path": str(dispatcher_log_file())}, indent=2)
            + "\n",
            encoding="utf-8",
        )

    logger().info("dispatcher_started", pid=own_pid)
    try:
        while True:
            try:
                due_count, active_count = dispatch_due_schedules()
                if due_count:
                    logger().info("schedules_dispatched", due_count=due_count, active_count=active_count)
                if active_count == 0:
                    logger().info("dispatcher_exiting", reason="no_active_schedules")
                    return 0
            except Exception as exc:  # Keep the scheduler alive after per-iteration failures.
                logger().error("dispatcher_iteration_failed", error=str(exc))
            time.sleep(poll_seconds())
    finally:
        with state_lock():
            payload = read_pid_payload()
            if payload and int(payload.get("pid") or 0) == own_pid:
                try:
                    pid_file().unlink()
                except FileNotFoundError:
                    pass


def build_kodelet_command(schedule: dict[str, Any]) -> list[str]:
    command_name = str(schedule.get("kodelet_command") or "kodelet")
    flags = schedule.get("kodelet_flags") or []
    if not isinstance(flags, list) or any(not isinstance(item, str) or item == "" for item in flags):
        raise ValueError("schedule has invalid kodelet_flags")
    instruction = schedule.get("instruction")
    if not isinstance(instruction, str) or not instruction.strip():
        raise ValueError("schedule has no instruction")
    return [command_name, "run", *flags, instruction]


def mark_run_finished(name: str, run_id: str, exit_code: int, error: str | None = None) -> None:
    with state_lock():
        state = load_state_unlocked()
        schedule = state.get("schedules", {}).get(name)
        if isinstance(schedule, dict) and schedule.get("last_run_id") == run_id:
            schedule["last_finished_at"] = format_dt(utc_now())
            schedule["last_run_status"] = "succeeded" if exit_code == 0 else "failed"
            schedule["last_exit_code"] = exit_code
            schedule["last_error"] = error
            if schedule.get("schedule", {}).get("kind") == "once":
                schedule["status"] = "completed" if exit_code == 0 else "failed"
            schedule["updated_at"] = format_dt(utc_now())
            save_state_unlocked(state)


def run_record(record_path: str) -> int:
    path = Path(record_path)
    record = json.loads(path.read_text(encoding="utf-8"))
    schedule = record.get("schedule")
    if not isinstance(schedule, dict):
        raise ValueError(f"invalid run record: {path}")

    name = str(record["name"])
    run_id = str(record["run_id"])
    log_path = Path(str(record["log_path"]))
    log_path.parent.mkdir(parents=True, exist_ok=True)

    exit_code = 1
    error: str | None = None
    command: list[str] = []
    cwd: Path | None = None
    started_at = utc_now()
    try:
        command = build_kodelet_command(schedule)
        working_directory = str(schedule.get("working_directory") or "")
        cwd = Path(working_directory).expanduser() if working_directory else None
        environment = clean_env()
        if isinstance(schedule.get("environment"), dict):
            environment.update({str(key): str(value) for key, value in schedule["environment"].items()})

        with log_path.open("a", encoding="utf-8") as log_handle, contextlib.redirect_stdout(log_handle), contextlib.redirect_stderr(
            log_handle
        ):
            run_logger = logger().bind(schedule_name=name, run_id=run_id, log_path=str(log_path))
            run_logger.info(
                "scheduled_run_started",
                working_directory=str(cwd) if cwd else None,
                command=shlex.join(command),
            )
            log_handle.flush()

            if cwd is not None and not cwd.is_dir():
                error = f"working directory does not exist: {cwd}"
                run_logger.error("scheduled_run_invalid_working_directory", error=error)
                exit_code = 127
            else:
                process = subprocess.Popen(
                    command,
                    cwd=str(cwd) if cwd else None,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                if process.stdout is not None:
                    for line in process.stdout:
                        run_logger.info("scheduled_run_output", output=line.rstrip("\n"))
                exit_code = int(process.wait())
            run_logger.info(
                "scheduled_run_finished",
                exit_code=exit_code,
                duration_seconds=round((utc_now() - started_at).total_seconds(), 3),
            )
    except FileNotFoundError as exc:
        exit_code = 127
        error = str(exc)
        with log_path.open("a", encoding="utf-8") as log_handle, contextlib.redirect_stdout(log_handle), contextlib.redirect_stderr(
            log_handle
        ):
            logger().bind(schedule_name=name, run_id=run_id, log_path=str(log_path)).error(
                "scheduled_run_execute_failed",
                error=str(exc),
                command=shlex.join(command) if command else None,
            )
    except Exception as exc:
        exit_code = 1
        error = str(exc)
        with log_path.open("a", encoding="utf-8") as log_handle, contextlib.redirect_stdout(log_handle), contextlib.redirect_stderr(
            log_handle
        ):
            logger().bind(schedule_name=name, run_id=run_id, log_path=str(log_path)).error(
                "scheduled_run_failed",
                error=str(exc),
                command=shlex.join(command) if command else None,
            )
    finally:
        mark_run_finished(name, run_id, exit_code, error)
    return exit_code
