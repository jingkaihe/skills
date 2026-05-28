from __future__ import annotations

import json
from .io import (
    emit_error,
    emit_json,
    load_payload,
    optional_bool,
    reject_unknown_keys,
    required_string,
)
from .models import (
    active_schedule_count,
    build_schedule,
    is_active_schedule,
    redact_schedule,
    sorted_schedules,
)
from .service import dispatcher_status
from .store import load_state_unlocked, save_state_unlocked, schedule_file, state_lock


def create_schedule_tool(raw_input: str) -> int:
    try:
        payload = load_payload(raw_input)
        reject_unknown_keys(
            payload,
            {
                "name",
                "instruction",
                "harness",
                "when",
                "timezone",
                "working_directory",
                "environment",
                "overwrite",
                "enabled",
                "retention",
            },
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
            return emit_error(
                f"schedule already exists: {schedule['name']} (set overwrite=true to replace it)"
            )
        schedules[schedule["name"]] = schedule
        save_state_unlocked(state)

    emit_json(
        {
            "status": "success",
            "message": f"Schedule {schedule['name']} has been created",
            "schedule_file": str(schedule_file()),
            "dispatcher": dispatcher_status(),
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
            schedules = [
                schedule
                for schedule in sorted_schedules(state)
                if include_inactive or is_active_schedule(schedule)
            ]
            active_count = active_schedule_count(state)
    except ValueError as exc:
        return emit_error(str(exc))

    emit_json(
        {
            "status": "success",
            "schedule_file": str(schedule_file()),
            "dispatcher": dispatcher_status(),
            "count": len(schedules),
            "active_count": active_count,
            "schedules": [
                redact_schedule(schedule, include_environment) for schedule in schedules
            ],
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
            "message": f"Schedule {name} has been deleted"
            if schedule
            else f"Schedule {name} was already absent",
            "schedule_file": str(schedule_file()),
            "dispatcher": dispatcher_status(),
            "deleted_schedule": redact_schedule(schedule) if schedule else None,
        }
    )
    return 0
