from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone

import pytest
from click.testing import CliRunner

from kodelet_schedule import core
from kodelet_schedule.cli import main


def utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


@pytest.fixture(autouse=True)
def isolated_schedule_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("KODELET_SCHEDULE_DIR", str(tmp_path))
    monkeypatch.setenv("KODELET_SCHEDULE_DISABLE_DISPATCHER", "1")
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
            "--env",
            "TOKEN=secret",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["schedule"]["name"] == "cli-demo"
    assert payload["schedule"]["environment"] == {"TOKEN": "<redacted>"}


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

    record_path = tmp_path / "record.json"
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
