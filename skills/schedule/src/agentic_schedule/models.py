from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

from .config import (
    DEFAULT_EXECUTION_DEADLINE_SECONDS,
    DEFAULT_HARNESS,
    DEFAULT_KODELET_FLAGS,
    DEFAULT_RETENTION,
    NAME_RE,
    SUPPORTED_HARNESSES,
)
from .io import optional_bool, optional_string, required_string
from .timeparse import (
    format_dt,
    parse_retention_seconds,
    parse_when,
    resolve_timezone,
    utc_now,
)


def validate_name(name: str) -> None:
    if not NAME_RE.fullmatch(name):
        raise ValueError(
            "name must start with a letter or number and contain only letters, numbers, '.', '_' or '-'"
        )


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


def validate_harness(value: str | None) -> str:
    harness = (value or DEFAULT_HARNESS).strip().lower()
    if not harness:
        harness = DEFAULT_HARNESS
    if harness not in SUPPORTED_HARNESSES:
        raise ValueError(f"harness must be one of: {', '.join(SUPPORTED_HARNESSES)}")
    return harness


def schedule_harness_from_payload(payload: dict[str, Any]) -> str:
    explicit_harness = optional_string(payload, "harness", None)
    env_harness = os.environ.get("SCHEDULE_SKILL_HARNESS")
    return validate_harness(explicit_harness or env_harness or DEFAULT_HARNESS)


def build_schedule(payload: dict[str, Any]) -> dict[str, Any]:
    name = required_string(payload, "name")
    validate_name(name)
    instruction = required_string(payload, "instruction")
    harness = schedule_harness_from_payload(payload)
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
    retention = (
        optional_string(payload, "retention", DEFAULT_RETENTION) or DEFAULT_RETENTION
    )
    retention_seconds = parse_retention_seconds(retention)

    now = format_dt(utc_now())
    status = "active" if enabled else "disabled"
    if spec["kind"] == "once" and enabled:
        status = "pending"

    return {
        "name": name,
        "instruction": instruction,
        "harness": harness,
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
        "retention": retention,
        "retention_seconds": retention_seconds,
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


def redact_schedule(
    schedule: dict[str, Any], include_environment: bool = False
) -> dict[str, Any]:
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
    return sum(
        1
        for schedule in state.get("schedules", {}).values()
        if isinstance(schedule, dict) and is_active_schedule(schedule)
    )
