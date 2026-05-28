from __future__ import annotations

import copy
import json

from agentic_schedule import dispatcher, models, runner, service, store


def test_build_kodelet_command_uses_internal_headless_default():
    schedule = models.build_schedule(
        {"name": "demo", "instruction": "Say hello", "when": "in 90 minutes"}
    )

    assert schedule["harness"] == "kodelet"
    assert runner.build_kodelet_command(schedule) == [
        "kodelet",
        "run",
        "--headless",
        "Say hello",
    ]
    assert runner.build_harness_command(schedule) == [
        "kodelet",
        "run",
        "--headless",
        "Say hello",
    ]
    redacted = models.redact_schedule(schedule)
    assert "kodelet_command" not in redacted
    assert "kodelet_flags" not in redacted


def test_build_harness_command_supports_claude_and_codex():
    claude_schedule = models.build_schedule(
        {
            "name": "claude-demo",
            "instruction": "Say hello",
            "harness": "claude",
            "when": "in 90 minutes",
        }
    )
    codex_schedule = models.build_schedule(
        {
            "name": "codex-demo",
            "instruction": "Say hello",
            "harness": "codex",
            "when": "in 90 minutes",
        }
    )

    assert runner.build_harness_command(claude_schedule) == [
        "claude",
        "--dangerously-skip-permissions",
        "-p",
        "--output-format",
        "stream-json",
        "Say hello",
    ]
    assert runner.build_harness_command(codex_schedule) == [
        "codex",
        "--yolo",
        "exec",
        "--json",
        "Say hello",
    ]


def test_run_record_writes_json_logs_and_updates_schedule(tmp_path, monkeypatch):
    fake_kodelet = tmp_path / "kodelet"
    fake_kodelet.write_text(
        '#!/usr/bin/env bash\necho fake-kodelet args: "$@"\n', encoding="utf-8"
    )
    fake_kodelet.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{service.os.environ['PATH']}")

    schedule = models.build_schedule(
        {
            "name": "run-demo",
            "instruction": "Say hello",
            "when": "now",
            "working_directory": str(tmp_path),
        }
    )
    schedule["last_run_id"] = "run-1"
    state = {"version": 1, "schedules": {"run-demo": copy.deepcopy(schedule)}}
    with store.state_lock():
        store.save_state_unlocked(state)

    record_path = dispatcher.run_record_path("run-demo", "run-1")
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

    assert runner.run_record(str(record_path)) == 0

    updated_record = json.loads(record_path.read_text(encoding="utf-8"))
    assert updated_record["status"] == "succeeded"
    assert updated_record["exit_code"] == 0
    assert updated_record["finished_at"]

    log_events = [
        json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [event["event"] for event in log_events] == [
        "scheduled_run_started",
        "scheduled_run_output",
        "scheduled_run_finished",
    ]
    assert log_events[1]["output"] == "fake-kodelet args: run --headless Say hello"

    with store.state_lock():
        stored = store.load_state_unlocked()["schedules"]["run-demo"]
    assert stored["last_run_status"] == "succeeded"
    assert stored["last_exit_code"] == 0


def test_run_record_uses_configured_harness(tmp_path, monkeypatch):
    fake_codex = tmp_path / "codex"
    fake_codex.write_text(
        '#!/usr/bin/env bash\necho fake-codex args: "$@"\n', encoding="utf-8"
    )
    fake_codex.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{service.os.environ['PATH']}")

    schedule = models.build_schedule(
        {
            "name": "codex-run-demo",
            "instruction": "Say hello",
            "harness": "codex",
            "when": "now",
            "working_directory": str(tmp_path),
        }
    )
    schedule["last_run_id"] = "run-1"
    with store.state_lock():
        store.save_state_unlocked(
            {"version": 1, "schedules": {"codex-run-demo": copy.deepcopy(schedule)}}
        )

    record_path = dispatcher.run_record_path("codex-run-demo", "run-1")
    record_path.parent.mkdir(parents=True)
    log_path = tmp_path / "codex-run.log"
    record_path.write_text(
        json.dumps(
            {
                "name": "codex-run-demo",
                "run_id": "run-1",
                "schedule": schedule,
                "log_path": str(log_path),
                "record_path": str(record_path),
            }
        ),
        encoding="utf-8",
    )

    assert runner.run_record(str(record_path)) == 0

    log_events = [
        json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()
    ]
    assert log_events[0]["harness"] == "codex"
    assert log_events[1]["output"] == "fake-codex args: --yolo exec --json Say hello"
