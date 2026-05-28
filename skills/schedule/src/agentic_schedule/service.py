from __future__ import annotations

import json
import os
import plistlib
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from .config import (
    LAUNCHD_LABEL,
    LEGACY_SCHEDULE_ENV_KEYS,
    SCHEDULE_ENV_KEYS,
    SYSTEMD_IMPORT_ENVIRONMENT_KEYS,
    SYSTEMD_UNIT_NAME,
)
from .models import active_schedule_count
from .store import (
    dispatcher_log_file,
    load_state_unlocked,
    pid_file,
    schedule_dir,
    schedule_file,
    state_lock,
)


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
        return {
            "running": False,
            "pid_file": str(pid_file()),
            "log_path": str(dispatcher_log_file()),
        }
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


def service_log_file() -> Path:
    return schedule_dir() / "service.log"


def service_error_log_file() -> Path:
    return schedule_dir() / "service.err.log"


def detect_service_manager() -> str | None:
    if sys.platform == "darwin" and shutil.which("launchctl"):
        return "launchd"
    if shutil.which("systemctl"):
        return "systemd"
    return None


def systemd_user_dir() -> Path:
    raw_config_home = os.environ.get("XDG_CONFIG_HOME")
    if raw_config_home:
        return Path(raw_config_home).expanduser() / "systemd" / "user"
    return Path.home() / ".config" / "systemd" / "user"


def systemd_unit_path() -> Path:
    return systemd_user_dir() / SYSTEMD_UNIT_NAME


def launchd_agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def launchd_plist_path() -> Path:
    return launchd_agents_dir() / f"{LAUNCHD_LABEL}.plist"


def current_command() -> list[str]:
    project_dir = Path(__file__).resolve().parents[2]
    uv_binary = shutil.which("uv") or "uv"
    if (project_dir / "pyproject.toml").exists():
        return [uv_binary, "run", "--project", str(project_dir), "agentic-schedule"]
    return [sys.executable, "-m", "agentic_schedule.cli"]


def service_environment() -> dict[str, str]:
    environment: dict[str, str] = {}
    for key in (
        *SCHEDULE_ENV_KEYS,
        *LEGACY_SCHEDULE_ENV_KEYS,
        *SYSTEMD_IMPORT_ENVIRONMENT_KEYS,
    ):
        if key in os.environ:
            environment[key] = os.environ[key]
    return environment


def systemd_import_environment_command() -> list[str] | None:
    keys = [key for key in SYSTEMD_IMPORT_ENVIRONMENT_KEYS if key in os.environ]
    if not keys:
        return None
    return ["systemctl", "--user", "import-environment", *keys]


def systemd_unit_content() -> str:
    schedule_dir().mkdir(parents=True, exist_ok=True)
    environment = service_environment()
    environment_lines = "".join(
        f"Environment={shlex.quote(f'{key}={value}')}\n"
        for key, value in sorted(environment.items())
    )
    command = shlex.join([*current_command(), "dispatch-loop"])
    return (
        "[Unit]\n"
        "Description=Agentic schedule dispatcher\n"
        "After=default.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={command}\n"
        "Restart=always\n"
        "RestartSec=10\n"
        f"WorkingDirectory={shlex.quote(str(Path.cwd()))}\n"
        f"StandardOutput=append:{service_log_file()}\n"
        f"StandardError=append:{service_error_log_file()}\n"
        f"{environment_lines}"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def run_command(command: list[str]) -> tuple[int, str, str]:
    completed = subprocess.run(
        command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False
    )
    return completed.returncode, completed.stdout.strip(), completed.stderr.strip()


def service_already_absent(return_code: int, stdout: str, stderr: str) -> bool:
    if return_code == 0:
        return False
    output = f"{stdout}\n{stderr}".lower()
    return any(
        phrase in output
        for phrase in (
            "could not be found",
            "does not exist",
            "not found",
            "not loaded",
            "no such process",
            "service is not loaded",
        )
    )


def start_systemd_daemon() -> dict[str, Any]:
    unit_path = systemd_unit_path()
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    unit_path.write_text(systemd_unit_content(), encoding="utf-8")

    commands = [["systemctl", "--user", "daemon-reload"]]
    import_environment_command = systemd_import_environment_command()
    if import_environment_command:
        commands.append(import_environment_command)
    commands.extend(
        [
            ["systemctl", "--user", "enable", SYSTEMD_UNIT_NAME],
            ["systemctl", "--user", "restart", SYSTEMD_UNIT_NAME],
        ]
    )
    results = []
    for command in commands:
        return_code, stdout, stderr = run_command(command)
        results.append(
            {
                "command": command,
                "return_code": return_code,
                "stdout": stdout,
                "stderr": stderr,
            }
        )
        if return_code != 0:
            return {
                "installed": False,
                "manager": "systemd",
                "unit_path": str(unit_path),
                "error": stderr or stdout or f"command failed: {shlex.join(command)}",
                "commands": results,
            }

    return {
        "installed": True,
        "manager": "systemd",
        "unit_path": str(unit_path),
        "commands": results,
    }


def launchd_plist_payload() -> dict[str, Any]:
    schedule_dir().mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": [*current_command(), "dispatch-loop"],
        "RunAtLoad": True,
        "KeepAlive": True,
        "WorkingDirectory": str(Path.cwd()),
        "StandardOutPath": str(service_log_file()),
        "StandardErrorPath": str(service_error_log_file()),
    }
    environment = service_environment()
    if environment:
        payload["EnvironmentVariables"] = environment
    return payload


def start_launchd_daemon() -> dict[str, Any]:
    plist_path = launchd_plist_path()
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    with plist_path.open("wb") as handle:
        plistlib.dump(launchd_plist_payload(), handle, sort_keys=True)

    commands = []
    bootstrap = ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist_path)]
    return_code, stdout, stderr = run_command(bootstrap)
    commands.append(
        {
            "command": bootstrap,
            "return_code": return_code,
            "stdout": stdout,
            "stderr": stderr,
        }
    )
    if (
        return_code != 0
        and "already bootstrapped" not in stderr.lower()
        and "service already loaded" not in stderr.lower()
    ):
        return {
            "installed": False,
            "manager": "launchd",
            "plist_path": str(plist_path),
            "error": stderr or stdout or f"command failed: {shlex.join(bootstrap)}",
            "commands": commands,
        }

    kickstart = ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{LAUNCHD_LABEL}"]
    return_code, stdout, stderr = run_command(kickstart)
    commands.append(
        {
            "command": kickstart,
            "return_code": return_code,
            "stdout": stdout,
            "stderr": stderr,
        }
    )
    if return_code != 0:
        return {
            "installed": False,
            "manager": "launchd",
            "plist_path": str(plist_path),
            "error": stderr or stdout or f"command failed: {shlex.join(kickstart)}",
            "commands": commands,
        }

    return {
        "installed": True,
        "manager": "launchd",
        "plist_path": str(plist_path),
        "commands": commands,
    }


def start_daemon() -> dict[str, Any]:
    manager = detect_service_manager()
    if manager == "systemd":
        return start_systemd_daemon()
    if manager == "launchd":
        return start_launchd_daemon()
    return {
        "installed": False,
        "manager": None,
        "error": "no supported user service manager found; expected systemd --user on Linux or launchd on macOS",
    }


def stop_systemd_daemon() -> dict[str, Any]:
    command = ["systemctl", "--user", "stop", SYSTEMD_UNIT_NAME]
    return_code, stdout, stderr = run_command(command)
    stopped = return_code == 0 or service_already_absent(return_code, stdout, stderr)
    return {
        "stopped": stopped,
        "manager": "systemd",
        "unit_path": str(systemd_unit_path()),
        "commands": [
            {
                "command": command,
                "return_code": return_code,
                "stdout": stdout,
                "stderr": stderr,
            }
        ],
        **(
            {}
            if stopped
            else {"error": stderr or stdout or f"command failed: {shlex.join(command)}"}
        ),
    }


def uninstall_systemd_daemon() -> dict[str, Any]:
    results = []
    success = True
    error = None

    disable_command = ["systemctl", "--user", "disable", "--now", SYSTEMD_UNIT_NAME]
    return_code, stdout, stderr = run_command(disable_command)
    results.append(
        {
            "command": disable_command,
            "return_code": return_code,
            "stdout": stdout,
            "stderr": stderr,
        }
    )
    if return_code != 0 and not service_already_absent(return_code, stdout, stderr):
        success = False
        error = stderr or stdout or f"command failed: {shlex.join(disable_command)}"

    unit_path = systemd_unit_path()
    removed = False
    if unit_path.exists():
        unit_path.unlink()
        removed = True

    reload_command = ["systemctl", "--user", "daemon-reload"]
    return_code, stdout, stderr = run_command(reload_command)
    results.append(
        {
            "command": reload_command,
            "return_code": return_code,
            "stdout": stdout,
            "stderr": stderr,
        }
    )
    if return_code != 0 and error is None:
        success = False
        error = stderr or stdout or f"command failed: {shlex.join(reload_command)}"

    return {
        "uninstalled": success,
        "manager": "systemd",
        "unit_path": str(unit_path),
        "removed_unit": removed,
        "commands": results,
        **({} if success else {"error": error}),
    }


def stop_launchd_daemon() -> dict[str, Any]:
    command = ["launchctl", "bootout", f"gui/{os.getuid()}/{LAUNCHD_LABEL}"]
    return_code, stdout, stderr = run_command(command)
    already_stopped = service_already_absent(return_code, stdout, stderr)
    stopped = return_code == 0 or already_stopped
    return {
        "stopped": stopped,
        "manager": "launchd",
        "plist_path": str(launchd_plist_path()),
        "commands": [
            {
                "command": command,
                "return_code": return_code,
                "stdout": stdout,
                "stderr": stderr,
            }
        ],
        **(
            {}
            if stopped
            else {"error": stderr or stdout or f"command failed: {shlex.join(command)}"}
        ),
    }


def uninstall_launchd_daemon() -> dict[str, Any]:
    stop_result = stop_launchd_daemon()
    plist_path = launchd_plist_path()
    removed = False
    if plist_path.exists():
        plist_path.unlink()
        removed = True
    return {
        "uninstalled": bool(stop_result.get("stopped")),
        "manager": "launchd",
        "plist_path": str(plist_path),
        "removed_plist": removed,
        "commands": stop_result.get("commands", []),
        **(
            {}
            if stop_result.get("stopped")
            else {"error": stop_result.get("error", "failed to stop launchd service")}
        ),
    }


def stop_daemon() -> dict[str, Any]:
    manager = detect_service_manager()
    if manager == "systemd":
        return stop_systemd_daemon()
    if manager == "launchd":
        return stop_launchd_daemon()
    return {
        "stopped": False,
        "manager": None,
        "error": "no supported user service manager found; expected systemd --user on Linux or launchd on macOS",
    }


def uninstall_daemon() -> dict[str, Any]:
    manager = detect_service_manager()
    if manager == "systemd":
        return uninstall_systemd_daemon()
    if manager == "launchd":
        return uninstall_launchd_daemon()
    return {
        "uninstalled": False,
        "manager": None,
        "error": "no supported user service manager found; expected systemd --user on Linux or launchd on macOS",
    }


def systemd_service_status() -> dict[str, Any]:
    unit_path = systemd_unit_path()
    status: dict[str, Any] = {
        "manager": "systemd",
        "unit_path": str(unit_path),
        "installed": unit_path.exists(),
    }
    if not shutil.which("systemctl"):
        status["available"] = False
        return status
    status["available"] = True
    for field, command in {
        "active_state": ["systemctl", "--user", "is-active", SYSTEMD_UNIT_NAME],
        "enabled_state": ["systemctl", "--user", "is-enabled", SYSTEMD_UNIT_NAME],
    }.items():
        return_code, stdout, stderr = run_command(command)
        status[field] = stdout or stderr or "unknown"
        status[f"{field}_return_code"] = return_code
    return status


def launchd_service_status() -> dict[str, Any]:
    plist_path = launchd_plist_path()
    status: dict[str, Any] = {
        "manager": "launchd",
        "plist_path": str(plist_path),
        "installed": plist_path.exists(),
    }
    if not shutil.which("launchctl"):
        status["available"] = False
        return status
    status["available"] = True
    command = ["launchctl", "print", f"gui/{os.getuid()}/{LAUNCHD_LABEL}"]
    return_code, stdout, stderr = run_command(command)
    status["loaded"] = return_code == 0
    status["return_code"] = return_code
    status["details"] = stdout if return_code == 0 else stderr
    return status


def service_status() -> dict[str, Any]:
    if sys.platform == "darwin":
        return launchd_service_status()
    return systemd_service_status()


def status_payload() -> dict[str, Any]:
    try:
        with state_lock():
            state = load_state_unlocked()
            total_count = len(state.get("schedules", {}))
            active_count = active_schedule_count(state)
    except ValueError as exc:
        return {"status": "error", "error": str(exc)}

    return {
        "status": "success",
        "schedule_file": str(schedule_file()),
        "dispatcher": dispatcher_status(),
        "service": service_status(),
        "total_count": total_count,
        "active_count": active_count,
    }
