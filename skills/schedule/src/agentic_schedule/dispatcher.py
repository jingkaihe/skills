from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .config import (
    CONTEXT_ENV_KEYS,
    DEFAULT_EXECUTION_DEADLINE_SECONDS,
    DEFAULT_POLL_SECONDS,
    DEFAULT_RETENTION_SECONDS,
)
from .io import logger
from .models import active_schedule_count, is_active_schedule
from .service import dispatcher_status, read_pid_payload
from .store import (
    dispatcher_log_file,
    load_state_unlocked,
    logs_dir,
    pid_file,
    run_records_dir,
    save_state_unlocked,
    schedule_dir,
    state_lock,
)
from .timeparse import compute_next_run, format_dt, parse_utc, utc_now


def dispatcher_disabled() -> bool:
    raw_value = os.environ.get("AGENTIC_SCHEDULE_DISABLE_DISPATCHER") or os.environ.get(
        "KODELET_SCHEDULE_DISABLE_DISPATCHER", ""
    )
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def clean_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in CONTEXT_ENV_KEYS:
        env.pop(key, None)
    return env


def ensure_dispatcher_running() -> dict[str, Any]:
    if dispatcher_disabled():
        status = dispatcher_status()
        status["disabled"] = True
        status["reason"] = "AGENTIC_SCHEDULE_DISABLE_DISPATCHER is set"
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
        payload = {
            "pid": process.pid,
            "started_at": format_dt(utc_now()),
            "log_path": str(dispatcher_log_file()),
        }
        pid_file().write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return {"running": True, **payload, "pid_file": str(pid_file())}


def poll_seconds() -> int:
    raw_value = os.environ.get("AGENTIC_SCHEDULE_POLL_SECONDS") or os.environ.get(
        "KODELET_SCHEDULE_POLL_SECONDS", str(DEFAULT_POLL_SECONDS)
    )
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


def cleanup_empty_parent(path: Path, stop_at: Path) -> None:
    current = path.parent
    stop_at = stop_at.resolve()
    while current.resolve() != stop_at and current.exists():
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def cleanup_finished_runs(state: dict[str, Any], now: datetime) -> int:
    root = run_records_dir()
    if not root.exists():
        return 0

    removed = 0
    schedules = state.get("schedules", {})
    if not isinstance(schedules, dict):
        schedules = {}

    for record_path in root.glob("*/*.json"):
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        name = str(record.get("name") or record_path.parent.name)
        schedule = schedules.get(name)
        if not isinstance(schedule, dict):
            schedule = (
                record.get("schedule")
                if isinstance(record.get("schedule"), dict)
                else {}
            )
        retention_seconds = int(
            schedule.get("retention_seconds") or DEFAULT_RETENTION_SECONDS
        )
        finished_at = parse_utc(record.get("finished_at"))
        if finished_at is None or finished_at > now - timedelta(
            seconds=retention_seconds
        ):
            continue

        log_path = Path(str(record.get("log_path") or ""))
        try:
            record_path.unlink()
            removed += 1
            cleanup_empty_parent(record_path, root)
        except FileNotFoundError:
            pass
        if log_path.exists():
            try:
                log_path.unlink()
                cleanup_empty_parent(log_path, logs_dir())
            except OSError:
                pass

    return removed


def prepare_due_runs(
    state: dict[str, Any], now: datetime
) -> tuple[list[dict[str, Any]], bool]:
    due_runs: list[dict[str, Any]] = []
    changed = False
    for name, schedule in sorted(state.get("schedules", {}).items()):
        if not isinstance(schedule, dict) or not is_active_schedule(schedule):
            continue
        next_run_at = parse_utc(schedule.get("next_run_at"))
        if next_run_at is None or next_run_at > now:
            continue

        deadline_seconds = int(
            schedule.get("execution_deadline_seconds")
            or DEFAULT_EXECUTION_DEADLINE_SECONDS
        )
        if (now - next_run_at).total_seconds() > deadline_seconds:
            schedule["last_started_at"] = None
            schedule["last_finished_at"] = format_dt(now)
            schedule["last_run_status"] = "skipped"
            schedule["last_exit_code"] = None
            schedule["last_pid"] = None
            schedule["last_log_path"] = None
            schedule["last_error"] = (
                f"missed execution deadline by more than {deadline_seconds} seconds"
            )
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
                schedule["next_run_at"] = (
                    format_dt(next_after_skip) if next_after_skip else None
                )
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
            schedule["next_run_at"] = (
                format_dt(next_after_dispatch) if next_after_dispatch else None
            )
        changed = True

    return due_runs, changed


def write_run_record(record: dict[str, Any]) -> None:
    path = Path(str(record["record_path"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(record, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


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
    command = [
        sys.executable,
        "-m",
        "agentic_schedule.cli",
        "run-record",
        str(record["record_path"]),
    ]
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
        removed_count = cleanup_finished_runs(state, now)
        if removed_count:
            logger().info("finished_run_retention_cleanup", removed_count=removed_count)
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
            json.dumps(
                {
                    "pid": own_pid,
                    "started_at": format_dt(utc_now()),
                    "log_path": str(dispatcher_log_file()),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    logger().info("dispatcher_started", pid=own_pid)
    try:
        while True:
            try:
                due_count, active_count = dispatch_due_schedules()
                if due_count:
                    logger().info(
                        "schedules_dispatched",
                        due_count=due_count,
                        active_count=active_count,
                    )
            except (
                Exception
            ) as exc:  # Keep the scheduler alive after per-iteration failures.
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
