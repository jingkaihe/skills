from __future__ import annotations

import re

try:
    import fcntl
except ImportError:  # pragma: no cover - fcntl is unavailable on Windows.
    fcntl = None  # type: ignore[assignment]

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python 3.11 includes zoneinfo.
    ZoneInfo = None  # type: ignore[assignment]

STATE_VERSION = 1
DEFAULT_HARNESS = "kodelet"
SUPPORTED_HARNESSES = ("kodelet", "claude", "codex")
DEFAULT_KODELET_FLAGS = ["--headless"]
DEFAULT_CLAUDE_FLAGS = [
    "--dangerously-skip-permissions",
    "-p",
    "--output-format",
    "stream-json",
]
DEFAULT_CODEX_FLAGS = ["--yolo", "exec", "--json"]
DEFAULT_POLL_SECONDS = 5
DEFAULT_EXECUTION_DEADLINE_SECONDS = 10 * 60
DEFAULT_RETENTION = "5d"
DEFAULT_RETENTION_SECONDS = 5 * 24 * 60 * 60
SERVICE_NAME = "agentic-schedule"
SYSTEMD_UNIT_NAME = f"{SERVICE_NAME}.service"
LAUNCHD_LABEL = "com.agentic-schedule.dispatcher"
SCHEDULE_ENV_KEYS = (
    "AGENTIC_SCHEDULE_DIR",
    "AGENTIC_SCHEDULE_POLL_SECONDS",
    "SCHEDULE_SKILL_HARNESS",
)
LEGACY_SCHEDULE_ENV_KEYS = ("KODELET_SCHEDULE_DIR", "KODELET_SCHEDULE_POLL_SECONDS")
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
