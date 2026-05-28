from __future__ import annotations

import contextlib
import json
import shlex
import subprocess
from pathlib import Path
from typing import Any

from .config import DEFAULT_CLAUDE_FLAGS, DEFAULT_CODEX_FLAGS, DEFAULT_HARNESS
from .dispatcher import clean_env, run_record_path
from .io import logger
from .models import validate_harness
from .store import load_state_unlocked, save_state_unlocked, state_lock
from .timeparse import format_dt, utc_now


def build_kodelet_command(schedule: dict[str, Any]) -> list[str]:
    command_name = str(schedule.get("kodelet_command") or "kodelet")
    flags = schedule.get("kodelet_flags") or []
    if not isinstance(flags, list) or any(
        not isinstance(item, str) or item == "" for item in flags
    ):
        raise ValueError("schedule has invalid kodelet_flags")
    instruction = schedule_instruction(schedule)
    return [command_name, "run", *flags, instruction]


def schedule_instruction(schedule: dict[str, Any]) -> str:
    instruction = schedule.get("instruction")
    if not isinstance(instruction, str) or not instruction.strip():
        raise ValueError("schedule has no instruction")
    return instruction


def build_claude_command(schedule: dict[str, Any]) -> list[str]:
    return ["claude", *DEFAULT_CLAUDE_FLAGS, schedule_instruction(schedule)]


def build_codex_command(schedule: dict[str, Any]) -> list[str]:
    return ["codex", *DEFAULT_CODEX_FLAGS, schedule_instruction(schedule)]


def build_harness_command(schedule: dict[str, Any]) -> list[str]:
    harness = validate_harness(str(schedule.get("harness") or DEFAULT_HARNESS))
    if harness == "kodelet":
        return build_kodelet_command(schedule)
    if harness == "claude":
        return build_claude_command(schedule)
    if harness == "codex":
        return build_codex_command(schedule)
    raise ValueError(f"unsupported harness: {harness}")


def mark_run_finished(
    name: str, run_id: str, exit_code: int, error: str | None = None
) -> None:
    finished_at = format_dt(utc_now())
    with state_lock():
        state = load_state_unlocked()
        schedule = state.get("schedules", {}).get(name)
        if isinstance(schedule, dict) and schedule.get("last_run_id") == run_id:
            schedule["last_finished_at"] = finished_at
            schedule["last_run_status"] = "succeeded" if exit_code == 0 else "failed"
            schedule["last_exit_code"] = exit_code
            schedule["last_error"] = error
            if schedule.get("schedule", {}).get("kind") == "once":
                schedule["status"] = "completed" if exit_code == 0 else "failed"
            schedule["updated_at"] = finished_at
            save_state_unlocked(state)
    try:
        path = run_record_path(name, run_id)
        record = json.loads(path.read_text(encoding="utf-8"))
        record.update(
            {
                "finished_at": finished_at,
                "exit_code": exit_code,
                "error": error,
                "status": "succeeded" if exit_code == 0 else "failed",
            }
        )
        path.write_text(
            json.dumps(record, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (OSError, json.JSONDecodeError):
        logger().warning(
            "run_record_finish_update_failed", schedule_name=name, run_id=run_id
        )


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
        command = build_harness_command(schedule)
        working_directory = str(schedule.get("working_directory") or "")
        cwd = Path(working_directory).expanduser() if working_directory else None
        environment = clean_env()
        if isinstance(schedule.get("environment"), dict):
            environment.update(
                {str(key): str(value) for key, value in schedule["environment"].items()}
            )

        with (
            log_path.open("a", encoding="utf-8") as log_handle,
            contextlib.redirect_stdout(log_handle),
            contextlib.redirect_stderr(log_handle),
        ):
            run_logger = logger().bind(
                schedule_name=name, run_id=run_id, log_path=str(log_path)
            )
            run_logger.info(
                "scheduled_run_started",
                harness=validate_harness(
                    str(schedule.get("harness") or DEFAULT_HARNESS)
                ),
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
                        run_logger.info(
                            "scheduled_run_output", output=line.rstrip("\n")
                        )
                exit_code = int(process.wait())
            run_logger.info(
                "scheduled_run_finished",
                exit_code=exit_code,
                duration_seconds=round((utc_now() - started_at).total_seconds(), 3),
            )
    except FileNotFoundError as exc:
        exit_code = 127
        error = str(exc)
        with (
            log_path.open("a", encoding="utf-8") as log_handle,
            contextlib.redirect_stdout(log_handle),
            contextlib.redirect_stderr(log_handle),
        ):
            logger().bind(
                schedule_name=name, run_id=run_id, log_path=str(log_path)
            ).error(
                "scheduled_run_execute_failed",
                error=str(exc),
                command=shlex.join(command) if command else None,
            )
    except Exception as exc:
        exit_code = 1
        error = str(exc)
        with (
            log_path.open("a", encoding="utf-8") as log_handle,
            contextlib.redirect_stdout(log_handle),
            contextlib.redirect_stderr(log_handle),
        ):
            logger().bind(
                schedule_name=name, run_id=run_id, log_path=str(log_path)
            ).error(
                "scheduled_run_failed",
                error=str(exc),
                command=shlex.join(command) if command else None,
            )
    finally:
        mark_run_finished(name, run_id, exit_code, error)
    return exit_code
