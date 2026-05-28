from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
from typing import Any

from .config import STATE_VERSION, fcntl


def schedule_dir() -> Path:
    raw_path = (
        os.environ.get("AGENTIC_SCHEDULE_DIR")
        or os.environ.get("KODELET_SCHEDULE_DIR")
        or os.environ.get("CUSTOM_TOOL_SCHEDULE_DIR")
    )
    if not raw_path:
        return Path.home() / ".agentic-schedule"
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
        schedules = {
            str(item.get("name")): item
            for item in schedules
            if isinstance(item, dict) and item.get("name")
        }
    if not isinstance(schedules, dict):
        raise ValueError(f"schedule file has invalid schedules object: {path}")

    state["version"] = int(state.get("version", STATE_VERSION))
    state["schedules"] = schedules
    return state


def save_state_unlocked(state: dict[str, Any]) -> None:
    path = schedule_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(
        json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp_path, path)
