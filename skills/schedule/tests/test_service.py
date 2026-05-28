from __future__ import annotations

from pathlib import Path

from agentic_schedule import config, service


def test_status_payload_reports_service_and_dispatcher(monkeypatch):
    monkeypatch.setattr(
        service, "service_status", lambda: {"manager": "test", "installed": True}
    )

    payload = service.status_payload()

    assert payload["status"] == "success"
    assert payload["dispatcher"]["running"] is False
    assert payload["service"] == {"manager": "test", "installed": True}
    assert payload["total_count"] == 0
    assert payload["active_count"] == 0


def test_current_command_prefers_uv_project(monkeypatch):
    monkeypatch.setattr(
        service.shutil, "which", lambda name: "/usr/bin/uv" if name == "uv" else None
    )

    command = service.current_command()

    assert command[:3] == ["/usr/bin/uv", "run", "--project"]
    assert command[-1] == "agentic-schedule"
    assert ".venv" not in " ".join(command)


def test_systemd_daemon_writes_user_unit_and_uses_no_sudo(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("PATH", "/opt/demo/bin:/usr/bin")
    commands: list[list[str]] = []

    def fake_run_command(command: list[str]) -> tuple[int, str, str]:
        commands.append(command)
        return 0, "", ""

    monkeypatch.setattr(service, "run_command", fake_run_command)
    monkeypatch.setattr(
        service.shutil, "which", lambda name: "/usr/bin/uv" if name == "uv" else None
    )

    result = service.start_systemd_daemon()

    assert result["installed"] is True
    unit_text = service.systemd_unit_path().read_text(encoding="utf-8")
    assert "ExecStart=/usr/bin/uv run --project" in unit_text
    assert "agentic-schedule dispatch-loop" in unit_text
    assert "--daemon" not in unit_text
    assert ".venv" not in unit_text
    assert "Restart=always" in unit_text
    assert "Environment=PATH=/opt/demo/bin:/usr/bin" in unit_text
    assert "PassEnvironment=PATH" not in unit_text
    assert commands == [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "import-environment", "PATH"],
        ["systemctl", "--user", "enable", config.SYSTEMD_UNIT_NAME],
        ["systemctl", "--user", "restart", config.SYSTEMD_UNIT_NAME],
    ]
    assert all("sudo" not in command for command in commands for command in command)


def test_systemd_unit_snapshots_scheduler_environment_and_path(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("PATH", "/opt/tools:/usr/bin")
    monkeypatch.setenv("SCHEDULE_SKILL_HARNESS", "codex")
    monkeypatch.setattr(
        service.shutil, "which", lambda name: "/usr/bin/uv" if name == "uv" else None
    )

    unit_text = service.systemd_unit_content()

    assert "Environment=SCHEDULE_SKILL_HARNESS=codex" in unit_text
    assert "Environment=PATH=/opt/tools:/usr/bin" in unit_text
    assert "PassEnvironment=PATH" not in unit_text


def test_launchd_daemon_writes_user_plist_and_uses_no_sudo(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    commands: list[list[str]] = []

    def fake_run_command(command: list[str]) -> tuple[int, str, str]:
        commands.append(command)
        return 0, "", ""

    monkeypatch.setattr(service, "run_command", fake_run_command)
    monkeypatch.setattr(
        service.shutil, "which", lambda name: "/usr/bin/uv" if name == "uv" else None
    )

    result = service.start_launchd_daemon()

    assert result["installed"] is True
    plist = service.plistlib.loads(service.launchd_plist_path().read_bytes())
    assert plist["Label"] == config.LAUNCHD_LABEL
    assert plist["ProgramArguments"][:3] == ["/usr/bin/uv", "run", "--project"]
    assert plist["ProgramArguments"][-2:] == ["agentic-schedule", "dispatch-loop"]
    assert ".venv" not in " ".join(plist["ProgramArguments"])
    assert plist["KeepAlive"] is True
    assert commands == [
        [
            "launchctl",
            "bootstrap",
            f"gui/{service.os.getuid()}",
            str(service.launchd_plist_path()),
        ],
        [
            "launchctl",
            "kickstart",
            "-k",
            f"gui/{service.os.getuid()}/{config.LAUNCHD_LABEL}",
        ],
    ]
    assert all("sudo" not in command for command in commands for command in command)


def test_stop_systemd_daemon_uses_user_service_and_no_sudo(monkeypatch):
    commands: list[list[str]] = []

    def fake_run_command(command: list[str]) -> tuple[int, str, str]:
        commands.append(command)
        return 0, "", ""

    monkeypatch.setattr(service, "run_command", fake_run_command)

    result = service.stop_systemd_daemon()

    assert result["stopped"] is True
    assert commands == [["systemctl", "--user", "stop", config.SYSTEMD_UNIT_NAME]]
    assert all("sudo" not in command for command in commands for command in command)


def test_stop_systemd_daemon_is_idempotent_for_absent_service(monkeypatch):
    monkeypatch.setattr(
        service,
        "run_command",
        lambda command: (1, "", f"Unit {config.SYSTEMD_UNIT_NAME} not loaded."),
    )

    result = service.stop_systemd_daemon()

    assert result["stopped"] is True
    assert "error" not in result


def test_uninstall_systemd_daemon_removes_user_unit_and_uses_no_sudo(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    unit_path = service.systemd_unit_path()
    unit_path.parent.mkdir(parents=True)
    unit_path.write_text("[Unit]\nDescription=test\n", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_run_command(command: list[str]) -> tuple[int, str, str]:
        commands.append(command)
        return 0, "", ""

    monkeypatch.setattr(service, "run_command", fake_run_command)

    result = service.uninstall_systemd_daemon()

    assert result["uninstalled"] is True
    assert result["removed_unit"] is True
    assert not unit_path.exists()
    assert commands == [
        ["systemctl", "--user", "disable", "--now", config.SYSTEMD_UNIT_NAME],
        ["systemctl", "--user", "daemon-reload"],
    ]
    assert all("sudo" not in command for command in commands for command in command)


def test_stop_launchd_daemon_uses_user_agent_and_no_sudo(monkeypatch):
    commands: list[list[str]] = []

    def fake_run_command(command: list[str]) -> tuple[int, str, str]:
        commands.append(command)
        return 0, "", ""

    monkeypatch.setattr(service, "run_command", fake_run_command)

    result = service.stop_launchd_daemon()

    assert result["stopped"] is True
    assert commands == [
        ["launchctl", "bootout", f"gui/{service.os.getuid()}/{config.LAUNCHD_LABEL}"]
    ]
    assert all("sudo" not in command for command in commands for command in command)


def test_uninstall_launchd_daemon_removes_user_plist_and_uses_no_sudo(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    plist_path = service.launchd_plist_path()
    plist_path.parent.mkdir(parents=True)
    plist_path.write_text("plist", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_run_command(command: list[str]) -> tuple[int, str, str]:
        commands.append(command)
        return 0, "", ""

    monkeypatch.setattr(service, "run_command", fake_run_command)

    result = service.uninstall_launchd_daemon()

    assert result["uninstalled"] is True
    assert result["removed_plist"] is True
    assert not plist_path.exists()
    assert commands == [
        ["launchctl", "bootout", f"gui/{service.os.getuid()}/{config.LAUNCHD_LABEL}"]
    ]
    assert all("sudo" not in command for command in commands for command in command)
