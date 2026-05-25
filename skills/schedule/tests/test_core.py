from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone

import pytest
from click.testing import CliRunner

from agentic_schedule import core
from agentic_schedule.cli import main


def utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


@pytest.fixture(autouse=True)
def isolated_schedule_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTIC_SCHEDULE_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTIC_SCHEDULE_DISABLE_DISPATCHER", "1")
    monkeypatch.setenv("TZ", "UTC")


def test_create_list_get_delete_round_trip(capsys):
    assert core.create_schedule_tool(
        json.dumps(
            {
                "name": "daily-demo",
                "instruction": "Say hello",
                "when": "daily 09:00",
                "timezone": "UTC",
                "environment": {"TOKEN": "secret"},
            }
        )
    ) == 0
    created = json.loads(capsys.readouterr().out)
    assert created["status"] == "success"
    assert created["schedule"]["schedule"] == {"kind": "daily", "time": "09:00:00", "timezone": "UTC"}
    assert created["schedule"]["environment"] == {"TOKEN": "<redacted>"}
    assert created["schedule"]["retention"] == "5d"
    assert created["schedule"]["retention_seconds"] == 5 * 24 * 60 * 60
    assert "kodelet_flags" not in created["schedule"]

    assert core.list_schedule_tool("{}") == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["count"] == 1
    assert listed["schedules"][0]["name"] == "daily-demo"

    assert core.get_schedule_tool(json.dumps({"name": "daily-demo", "include_environment": True})) == 0
    fetched = json.loads(capsys.readouterr().out)
    assert fetched["schedule"]["environment"] == {"TOKEN": "secret"}

    assert core.delete_schedule_tool(json.dumps({"name": "daily-demo"})) == 0
    deleted = json.loads(capsys.readouterr().out)
    assert deleted["deleted_schedule"]["name"] == "daily-demo"


def test_create_rejects_hidden_kodelet_flags(capsys):
    assert core.create_schedule_tool(
        json.dumps({"name": "bad", "instruction": "Say hello", "when": "in 90 minutes", "kodelet_flags": []})
    ) == 1
    payload = json.loads(capsys.readouterr().out)
    assert "unsupported parameter" in payload["error"]
    assert "kodelet_flags" in payload["error"]


def test_build_schedule_accepts_compact_retention_duration():
    schedule = core.build_schedule({"name": "retention-demo", "instruction": "Say hello", "when": "in 90 minutes", "retention": "30min"})

    assert schedule["retention"] == "30min"
    assert schedule["retention_seconds"] == 30 * 60


def test_create_rejects_invalid_retention(capsys):
    assert core.create_schedule_tool(json.dumps({"name": "bad", "instruction": "Say hello", "when": "in 90 minutes", "retention": "0d"})) == 1

    payload = json.loads(capsys.readouterr().out)
    assert "invalid duration" in payload["error"]


def test_create_does_not_start_dispatcher(capsys, monkeypatch):
    def fail_dispatcher_start() -> dict[str, object]:
        raise AssertionError("create must not start the dispatcher")

    monkeypatch.setattr(core, "ensure_dispatcher_running", fail_dispatcher_start)

    assert core.create_schedule_tool(json.dumps({"name": "passive-create", "instruction": "Say hello", "when": "now"})) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["dispatcher"]["running"] is False


def test_list_does_not_start_dispatcher(capsys, monkeypatch):
    def fail_dispatcher_start() -> dict[str, object]:
        raise AssertionError("list must not start the dispatcher")

    monkeypatch.setattr(core, "ensure_dispatcher_running", fail_dispatcher_start)
    schedule = core.build_schedule({"name": "passive-list", "instruction": "Say hello", "when": "now"})
    with core.state_lock():
        core.save_state_unlocked({"version": 1, "schedules": {"passive-list": schedule}})

    assert core.list_schedule_tool("{}") == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["active_count"] == 1
    assert payload["dispatcher"]["running"] is False


def test_poll_seconds_defaults_to_five_seconds(monkeypatch):
    monkeypatch.delenv("AGENTIC_SCHEDULE_POLL_SECONDS", raising=False)
    monkeypatch.delenv("KODELET_SCHEDULE_POLL_SECONDS", raising=False)

    assert core.poll_seconds() == 5


def test_poll_seconds_invalid_value_uses_default(monkeypatch):
    monkeypatch.setenv("AGENTIC_SCHEDULE_POLL_SECONDS", "invalid")

    assert core.poll_seconds() == 5


def test_prepare_due_runs_skips_stale_one_time_schedule():
    now = utc("2026-05-25T12:00:00Z")
    scheduled_for = now - timedelta(minutes=11)
    schedule = {
        "name": "stale",
        "enabled": True,
        "status": "pending",
        "next_run_at": core.format_dt(scheduled_for),
        "schedule": {"kind": "once", "at": core.format_dt(scheduled_for)},
        "execution_deadline_seconds": 600,
    }
    state = {"version": 1, "schedules": {"stale": schedule}}

    due_runs, changed = core.prepare_due_runs(state, now)

    assert due_runs == []
    assert changed is True
    assert schedule["enabled"] is False
    assert schedule["status"] == "skipped"
    assert schedule["last_run_status"] == "skipped"
    assert schedule["next_run_at"] is None
    assert "missed execution deadline" in schedule["last_error"]


def test_prepare_due_runs_dispatches_due_schedule_within_deadline(tmp_path):
    now = utc("2026-05-25T12:00:00Z")
    scheduled_for = now - timedelta(minutes=5)
    schedule = {
        "name": "fresh",
        "instruction": "Say hello",
        "enabled": True,
        "status": "pending",
        "working_directory": str(tmp_path),
        "next_run_at": core.format_dt(scheduled_for),
        "schedule": {"kind": "once", "at": core.format_dt(scheduled_for)},
        "execution_deadline_seconds": 600,
    }
    state = {"version": 1, "schedules": {"fresh": schedule}}

    due_runs, changed = core.prepare_due_runs(state, now)

    assert changed is True
    assert len(due_runs) == 1
    assert due_runs[0]["name"] == "fresh"
    assert schedule["enabled"] is False
    assert schedule["status"] == "running"
    assert schedule["run_count"] == 1
    assert schedule["next_run_at"] is None


def test_recurring_stale_schedule_skips_to_next_occurrence():
    now = utc("2026-05-25T12:00:00Z")
    scheduled_for = now - timedelta(minutes=11)
    schedule = {
        "name": "interval",
        "enabled": True,
        "status": "active",
        "next_run_at": core.format_dt(scheduled_for),
        "schedule": {"kind": "interval", "seconds": 3600, "start_at": core.format_dt(scheduled_for)},
        "execution_deadline_seconds": 600,
    }
    state = {"version": 1, "schedules": {"interval": schedule}}

    due_runs, changed = core.prepare_due_runs(state, now)

    assert due_runs == []
    assert changed is True
    assert schedule["enabled"] is True
    assert schedule["status"] == "active"
    assert schedule["last_run_status"] == "skipped"
    assert utc(schedule["next_run_at"]) > now


def test_build_kodelet_command_uses_internal_headless_default():
    schedule = core.build_schedule({"name": "demo", "instruction": "Say hello", "when": "in 90 minutes"})

    assert core.build_kodelet_command(schedule) == ["kodelet", "run", "--headless", "Say hello"]
    redacted = core.redact_schedule(schedule)
    assert "kodelet_command" not in redacted
    assert "kodelet_flags" not in redacted


def test_cli_create_uses_click_options(capsys):
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "create",
            "cli-demo",
            "--instruction",
            "Say hello",
            "--when",
            "in 90 minutes",
            "--timezone",
            "UTC",
            "--retention",
            "30h",
            "--env",
            "TOKEN=secret",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["schedule"]["name"] == "cli-demo"
    assert payload["schedule"]["retention"] == "30h"
    assert payload["schedule"]["retention_seconds"] == 30 * 60 * 60
    assert payload["schedule"]["environment"] == {"TOKEN": "<redacted>"}


def test_cleanup_finished_runs_removes_records_and_logs_older_than_retention():
    now = utc("2026-05-25T12:00:00Z")
    old_run_id = "20260520T110000Z-old"
    fresh_run_id = "20260525T110000Z-fresh"
    state = {"version": 1, "schedules": {"cleanup-demo": {"name": "cleanup-demo", "retention_seconds": 24 * 60 * 60}}}

    old_record_path = core.run_record_path("cleanup-demo", old_run_id)
    old_log_path = core.log_path_for("cleanup-demo", old_run_id)
    fresh_record_path = core.run_record_path("cleanup-demo", fresh_run_id)
    fresh_log_path = core.log_path_for("cleanup-demo", fresh_run_id)
    old_record_path.parent.mkdir(parents=True)
    old_log_path.parent.mkdir(parents=True)
    fresh_record_path.parent.mkdir(parents=True, exist_ok=True)
    fresh_log_path.parent.mkdir(parents=True, exist_ok=True)
    old_record_path.write_text(
        json.dumps({"name": "cleanup-demo", "run_id": old_run_id, "finished_at": "2026-05-20T11:00:00Z", "log_path": str(old_log_path)}),
        encoding="utf-8",
    )
    fresh_record_path.write_text(
        json.dumps({"name": "cleanup-demo", "run_id": fresh_run_id, "finished_at": "2026-05-25T11:00:00Z", "log_path": str(fresh_log_path)}),
        encoding="utf-8",
    )
    old_log_path.write_text("old", encoding="utf-8")
    fresh_log_path.write_text("fresh", encoding="utf-8")

    removed_count = core.cleanup_finished_runs(state, now)

    assert removed_count == 1
    assert not old_record_path.exists()
    assert not old_log_path.exists()
    assert fresh_record_path.exists()
    assert fresh_log_path.exists()



def test_status_payload_reports_service_and_dispatcher(monkeypatch):
    monkeypatch.setattr(core, "service_status", lambda: {"manager": "test", "installed": True})

    payload = core.status_payload()

    assert payload["status"] == "success"
    assert payload["dispatcher"]["running"] is False
    assert payload["service"] == {"manager": "test", "installed": True}
    assert payload["total_count"] == 0
    assert payload["active_count"] == 0


def test_current_command_prefers_uv_project_when_wrapper_is_unset(monkeypatch):
    monkeypatch.delenv("AGENTIC_SCHEDULE_WRAPPER", raising=False)
    monkeypatch.setattr(core.shutil, "which", lambda name: "/usr/bin/uv" if name == "uv" else None)

    command = core.current_command()

    assert command[:3] == ["/usr/bin/uv", "run", "--project"]
    assert command[-1] == "agentic-schedule"
    assert ".venv" not in " ".join(command)


def test_systemd_daemon_writes_user_unit_and_uses_no_sudo(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    commands: list[list[str]] = []

    def fake_run_command(command: list[str]) -> tuple[int, str, str]:
        commands.append(command)
        return 0, "", ""

    monkeypatch.setattr(core, "run_command", fake_run_command)
    monkeypatch.setenv("AGENTIC_SCHEDULE_WRAPPER", "/example/agentic-schedule")

    result = core.start_systemd_daemon()

    assert result["installed"] is True
    unit_text = core.systemd_unit_path().read_text(encoding="utf-8")
    assert "ExecStart=/example/agentic-schedule dispatch-loop --daemon" in unit_text
    assert "Restart=always" in unit_text
    assert commands == [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "--now", core.SYSTEMD_UNIT_NAME],
    ]
    assert all("sudo" not in command for command in commands for command in command)


def test_launchd_daemon_writes_user_plist_and_uses_no_sudo(tmp_path, monkeypatch):
    monkeypatch.setattr(core.Path, "home", staticmethod(lambda: tmp_path))
    commands: list[list[str]] = []

    def fake_run_command(command: list[str]) -> tuple[int, str, str]:
        commands.append(command)
        return 0, "", ""

    monkeypatch.setattr(core, "run_command", fake_run_command)
    monkeypatch.setenv("AGENTIC_SCHEDULE_WRAPPER", "/example/agentic-schedule")

    result = core.start_launchd_daemon()

    assert result["installed"] is True
    plist = core.plistlib.loads(core.launchd_plist_path().read_bytes())
    assert plist["Label"] == core.LAUNCHD_LABEL
    assert plist["ProgramArguments"] == ["/example/agentic-schedule", "dispatch-loop", "--daemon"]
    assert plist["KeepAlive"] is True
    assert commands == [
        ["launchctl", "bootstrap", f"gui/{core.os.getuid()}", str(core.launchd_plist_path())],
        ["launchctl", "kickstart", "-k", f"gui/{core.os.getuid()}/{core.LAUNCHD_LABEL}"],
    ]
    assert all("sudo" not in command for command in commands for command in command)


def test_stop_systemd_daemon_uses_user_service_and_no_sudo(monkeypatch):
    commands: list[list[str]] = []

    def fake_run_command(command: list[str]) -> tuple[int, str, str]:
        commands.append(command)
        return 0, "", ""

    monkeypatch.setattr(core, "run_command", fake_run_command)

    result = core.stop_systemd_daemon()

    assert result["stopped"] is True
    assert commands == [["systemctl", "--user", "stop", core.SYSTEMD_UNIT_NAME]]
    assert all("sudo" not in command for command in commands for command in command)


def test_stop_systemd_daemon_is_idempotent_for_absent_service(monkeypatch):
    monkeypatch.setattr(core, "run_command", lambda command: (1, "", f"Unit {core.SYSTEMD_UNIT_NAME} not loaded."))

    result = core.stop_systemd_daemon()

    assert result["stopped"] is True
    assert "error" not in result


def test_uninstall_systemd_daemon_removes_user_unit_and_uses_no_sudo(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    unit_path = core.systemd_unit_path()
    unit_path.parent.mkdir(parents=True)
    unit_path.write_text("[Unit]\nDescription=test\n", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_run_command(command: list[str]) -> tuple[int, str, str]:
        commands.append(command)
        return 0, "", ""

    monkeypatch.setattr(core, "run_command", fake_run_command)

    result = core.uninstall_systemd_daemon()

    assert result["uninstalled"] is True
    assert result["removed_unit"] is True
    assert not unit_path.exists()
    assert commands == [
        ["systemctl", "--user", "disable", "--now", core.SYSTEMD_UNIT_NAME],
        ["systemctl", "--user", "daemon-reload"],
    ]
    assert all("sudo" not in command for command in commands for command in command)


def test_stop_launchd_daemon_uses_user_agent_and_no_sudo(monkeypatch):
    commands: list[list[str]] = []

    def fake_run_command(command: list[str]) -> tuple[int, str, str]:
        commands.append(command)
        return 0, "", ""

    monkeypatch.setattr(core, "run_command", fake_run_command)

    result = core.stop_launchd_daemon()

    assert result["stopped"] is True
    assert commands == [["launchctl", "bootout", f"gui/{core.os.getuid()}/{core.LAUNCHD_LABEL}"]]
    assert all("sudo" not in command for command in commands for command in command)


def test_uninstall_launchd_daemon_removes_user_plist_and_uses_no_sudo(tmp_path, monkeypatch):
    monkeypatch.setattr(core.Path, "home", staticmethod(lambda: tmp_path))
    plist_path = core.launchd_plist_path()
    plist_path.parent.mkdir(parents=True)
    plist_path.write_text("plist", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_run_command(command: list[str]) -> tuple[int, str, str]:
        commands.append(command)
        return 0, "", ""

    monkeypatch.setattr(core, "run_command", fake_run_command)

    result = core.uninstall_launchd_daemon()

    assert result["uninstalled"] is True
    assert result["removed_plist"] is True
    assert not plist_path.exists()
    assert commands == [["launchctl", "bootout", f"gui/{core.os.getuid()}/{core.LAUNCHD_LABEL}"]]
    assert all("sudo" not in command for command in commands for command in command)


def test_cli_status_outputs_status_payload(monkeypatch):
    monkeypatch.setattr(core, "status_payload", lambda: {"status": "success", "service": {"installed": True}})
    runner = CliRunner()

    result = runner.invoke(main, ["status"])

    assert result.exit_code == 0
    assert json.loads(result.output) == {"status": "success", "service": {"installed": True}}


def test_cli_start_invokes_daemon_start(monkeypatch):
    monkeypatch.setattr(core, "start_daemon", lambda: {"installed": True, "manager": "systemd"})
    runner = CliRunner()

    result = runner.invoke(main, ["start"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["status"] == "success"
    assert payload["daemon"] == {"installed": True, "manager": "systemd"}


def test_cli_stop_invokes_daemon_stop(monkeypatch):
    monkeypatch.setattr(core, "stop_daemon", lambda: {"stopped": True, "manager": "systemd"})
    runner = CliRunner()

    result = runner.invoke(main, ["stop"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["status"] == "success"
    assert payload["daemon"] == {"stopped": True, "manager": "systemd"}


def test_cli_uninstall_invokes_daemon_uninstall(monkeypatch):
    monkeypatch.setattr(core, "uninstall_daemon", lambda: {"uninstalled": True, "manager": "systemd"})
    runner = CliRunner()

    result = runner.invoke(main, ["uninstall"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["status"] == "success"
    assert payload["daemon"] == {"uninstalled": True, "manager": "systemd"}


def test_run_record_writes_json_logs_and_updates_schedule(tmp_path, monkeypatch):
    fake_kodelet = tmp_path / "kodelet"
    fake_kodelet.write_text("#!/usr/bin/env bash\necho fake-kodelet args: \"$@\"\n", encoding="utf-8")
    fake_kodelet.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{core.os.environ['PATH']}")

    schedule = core.build_schedule(
        {
            "name": "run-demo",
            "instruction": "Say hello",
            "when": "now",
            "working_directory": str(tmp_path),
        }
    )
    schedule["last_run_id"] = "run-1"
    state = {"version": 1, "schedules": {"run-demo": copy.deepcopy(schedule)}}
    with core.state_lock():
        core.save_state_unlocked(state)

    record_path = core.run_record_path("run-demo", "run-1")
    record_path.parent.mkdir(parents=True)
    log_path = tmp_path / "run.log"
    record_path.write_text(
        json.dumps(
            {
                "name": "run-demo",
                "run_id": "run-1",
                "schedule": schedule,
                "log_path": str(log_path),
                "record_path": str(record_path),
            }
        ),
        encoding="utf-8",
    )

    assert core.run_record(str(record_path)) == 0

    updated_record = json.loads(record_path.read_text(encoding="utf-8"))
    assert updated_record["status"] == "succeeded"
    assert updated_record["exit_code"] == 0
    assert updated_record["finished_at"]

    log_events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert [event["event"] for event in log_events] == [
        "scheduled_run_started",
        "scheduled_run_output",
        "scheduled_run_finished",
    ]
    assert log_events[1]["output"] == "fake-kodelet args: run --headless Say hello"

    with core.state_lock():
        stored = core.load_state_unlocked()["schedules"]["run-demo"]
    assert stored["last_run_status"] == "succeeded"
    assert stored["last_exit_code"] == 0
