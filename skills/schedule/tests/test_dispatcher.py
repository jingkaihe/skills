from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from agentic_schedule import dispatcher, timeparse


def utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def test_poll_seconds_defaults_to_five_seconds(monkeypatch):
    monkeypatch.delenv("AGENTIC_SCHEDULE_POLL_SECONDS", raising=False)
    monkeypatch.delenv("KODELET_SCHEDULE_POLL_SECONDS", raising=False)

    assert dispatcher.poll_seconds() == 5


def test_poll_seconds_invalid_value_uses_default(monkeypatch):
    monkeypatch.setenv("AGENTIC_SCHEDULE_POLL_SECONDS", "invalid")

    assert dispatcher.poll_seconds() == 5


def test_prepare_due_runs_skips_stale_one_time_schedule():
    now = utc("2026-05-25T12:00:00Z")
    scheduled_for = now - timedelta(minutes=11)
    schedule = {
        "name": "stale",
        "enabled": True,
        "status": "pending",
        "next_run_at": timeparse.format_dt(scheduled_for),
        "schedule": {"kind": "once", "at": timeparse.format_dt(scheduled_for)},
        "execution_deadline_seconds": 600,
    }
    state = {"version": 1, "schedules": {"stale": schedule}}

    due_runs, changed = dispatcher.prepare_due_runs(state, now)

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
        "next_run_at": timeparse.format_dt(scheduled_for),
        "schedule": {"kind": "once", "at": timeparse.format_dt(scheduled_for)},
        "execution_deadline_seconds": 600,
    }
    state = {"version": 1, "schedules": {"fresh": schedule}}

    due_runs, changed = dispatcher.prepare_due_runs(state, now)

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
        "next_run_at": timeparse.format_dt(scheduled_for),
        "schedule": {
            "kind": "interval",
            "seconds": 3600,
            "start_at": timeparse.format_dt(scheduled_for),
        },
        "execution_deadline_seconds": 600,
    }
    state = {"version": 1, "schedules": {"interval": schedule}}

    due_runs, changed = dispatcher.prepare_due_runs(state, now)

    assert due_runs == []
    assert changed is True
    assert schedule["enabled"] is True
    assert schedule["status"] == "active"
    assert schedule["last_run_status"] == "skipped"
    assert utc(schedule["next_run_at"]) > now


def test_cleanup_finished_runs_removes_records_and_logs_older_than_retention():
    now = utc("2026-05-25T12:00:00Z")
    old_run_id = "20260520T110000Z-old"
    fresh_run_id = "20260525T110000Z-fresh"
    state = {
        "version": 1,
        "schedules": {
            "cleanup-demo": {"name": "cleanup-demo", "retention_seconds": 24 * 60 * 60}
        },
    }

    old_record_path = dispatcher.run_record_path("cleanup-demo", old_run_id)
    old_log_path = dispatcher.log_path_for("cleanup-demo", old_run_id)
    fresh_record_path = dispatcher.run_record_path("cleanup-demo", fresh_run_id)
    fresh_log_path = dispatcher.log_path_for("cleanup-demo", fresh_run_id)
    old_record_path.parent.mkdir(parents=True)
    old_log_path.parent.mkdir(parents=True)
    fresh_record_path.parent.mkdir(parents=True, exist_ok=True)
    fresh_log_path.parent.mkdir(parents=True, exist_ok=True)
    old_record_path.write_text(
        json.dumps(
            {
                "name": "cleanup-demo",
                "run_id": old_run_id,
                "finished_at": "2026-05-20T11:00:00Z",
                "log_path": str(old_log_path),
            }
        ),
        encoding="utf-8",
    )
    fresh_record_path.write_text(
        json.dumps(
            {
                "name": "cleanup-demo",
                "run_id": fresh_run_id,
                "finished_at": "2026-05-25T11:00:00Z",
                "log_path": str(fresh_log_path),
            }
        ),
        encoding="utf-8",
    )
    old_log_path.write_text("old", encoding="utf-8")
    fresh_log_path.write_text("fresh", encoding="utf-8")

    removed_count = dispatcher.cleanup_finished_runs(state, now)

    assert removed_count == 1
    assert not old_record_path.exists()
    assert not old_log_path.exists()
    assert fresh_record_path.exists()
    assert fresh_log_path.exists()
