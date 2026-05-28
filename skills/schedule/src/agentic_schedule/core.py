from __future__ import annotations

# Historical facade exports for callers that still import agentic_schedule.core.
# New implementation code should import the focused modules directly.
# ruff: noqa: F401
from pathlib import Path

from . import service as _service
from .api import (
    create_schedule_tool,
    delete_schedule_tool,
    get_schedule_tool,
    list_schedule_tool,
)
from .config import (
    DEFAULT_CLAUDE_FLAGS,
    DEFAULT_CODEX_FLAGS,
    DEFAULT_EXECUTION_DEADLINE_SECONDS,
    DEFAULT_HARNESS,
    DEFAULT_KODELET_FLAGS,
    DEFAULT_POLL_SECONDS,
    DEFAULT_RETENTION,
    DEFAULT_RETENTION_SECONDS,
    LAUNCHD_LABEL,
    STATE_VERSION,
    SUPPORTED_HARNESSES,
    SYSTEMD_UNIT_NAME,
)
from .dispatcher import (
    clean_env,
    cleanup_finished_runs,
    dispatch_due_schedules,
    dispatcher_disabled,
    dispatcher_loop,
    ensure_dispatcher_running,
    log_path_for,
    poll_seconds,
    prepare_due_runs,
    run_id_for,
    run_record_path,
    start_runner,
)
from .io import emit_error, emit_json, logger
from .models import (
    active_schedule_count,
    build_schedule,
    is_active_schedule,
    redact_schedule,
    sorted_schedules,
    validate_harness,
)
from .runner import (
    build_claude_command,
    build_codex_command,
    build_harness_command,
    build_kodelet_command,
    run_record,
)
from .service import (
    current_command,
    detect_service_manager,
    dispatcher_status,
    launchd_agents_dir,
    launchd_plist_path,
    launchd_plist_payload,
    launchd_service_status,
    process_running,
    read_pid_payload,
    run_command,
    service_already_absent,
    service_environment,
    service_error_log_file,
    service_log_file,
    service_status,
    start_daemon,
    start_launchd_daemon,
    start_systemd_daemon,
    status_payload,
    stop_daemon,
    stop_launchd_daemon,
    stop_systemd_daemon,
    systemd_service_status,
    systemd_unit_content,
    systemd_unit_path,
    systemd_user_dir,
    uninstall_daemon,
    uninstall_launchd_daemon,
    uninstall_systemd_daemon,
)
from .store import (
    dispatcher_log_file,
    load_state_unlocked,
    logs_dir,
    pid_file,
    run_records_dir,
    save_state_unlocked,
    schedule_dir,
    schedule_file,
    state_lock,
)
from .timeparse import (
    compute_next_run,
    format_dt,
    parse_datetime,
    parse_duration_seconds,
    parse_retention_seconds,
    parse_utc,
    utc_now,
)

# Re-export modules that existing tests/downstream callers monkeypatch through core.
os = _service.os
plistlib = _service.plistlib
shutil = _service.shutil
