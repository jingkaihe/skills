from __future__ import annotations

import json

from click.testing import CliRunner

from agentic_schedule import service
from agentic_schedule.cli import main


def test_cli_create_uses_click_options(capsys):
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "create",
            "cli-demo",
            "--instruction",
            "Say hello",
            "--harness",
            "codex",
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
    assert payload["schedule"]["harness"] == "codex"
    assert payload["schedule"]["retention"] == "30h"
    assert payload["schedule"]["retention_seconds"] == 30 * 60 * 60
    assert payload["schedule"]["environment"] == {"TOKEN": "<redacted>"}


def test_cli_status_outputs_status_payload(monkeypatch):
    monkeypatch.setattr(
        service,
        "status_payload",
        lambda: {"status": "success", "service": {"installed": True}},
    )
    runner = CliRunner()

    result = runner.invoke(main, ["status"])

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "status": "success",
        "service": {"installed": True},
    }


def test_cli_start_invokes_daemon_start(monkeypatch):
    monkeypatch.setattr(
        service, "start_daemon", lambda: {"installed": True, "manager": "systemd"}
    )
    runner = CliRunner()

    result = runner.invoke(main, ["start"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["status"] == "success"
    assert payload["daemon"] == {"installed": True, "manager": "systemd"}


def test_cli_stop_invokes_daemon_stop(monkeypatch):
    monkeypatch.setattr(
        service, "stop_daemon", lambda: {"stopped": True, "manager": "systemd"}
    )
    runner = CliRunner()

    result = runner.invoke(main, ["stop"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["status"] == "success"
    assert payload["daemon"] == {"stopped": True, "manager": "systemd"}


def test_cli_uninstall_invokes_daemon_uninstall(monkeypatch):
    monkeypatch.setattr(
        service, "uninstall_daemon", lambda: {"uninstalled": True, "manager": "systemd"}
    )
    runner = CliRunner()

    result = runner.invoke(main, ["uninstall"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["status"] == "success"
    assert payload["daemon"] == {"uninstalled": True, "manager": "systemd"}
