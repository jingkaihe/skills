from __future__ import annotations

import json
from typing import Any

import click

from agentic_schedule import api, config, dispatcher, runner, service


def emit_payload(handler: Any, payload: dict[str, Any]) -> None:
    raise SystemExit(handler(json.dumps(payload)))


def echo_json(payload: dict[str, Any]) -> None:
    click.echo(json.dumps(payload, indent=2, ensure_ascii=False))


def parse_env(values: tuple[str, ...]) -> dict[str, str]:
    environment: dict[str, str] = {}
    for value in values:
        key, separator, item = value.partition("=")
        if not separator or not key:
            raise click.BadParameter(
                "environment entries must use KEY=VALUE", param_hint="--env"
            )
        environment[key] = item
    return environment


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def main() -> None:
    """Manage background agentic schedules."""


@main.command("list")
@click.option("--active-only", is_flag=True, help="Only include active schedules.")
@click.option(
    "--include-environment",
    is_flag=True,
    help="Include stored environment values instead of redacting them.",
)
def list_command(active_only: bool, include_environment: bool) -> None:
    """List schedules."""
    emit_payload(
        api.list_schedule_tool,
        {
            "include_inactive": not active_only,
            "include_environment": include_environment,
        },
    )


@main.command("get")
@click.argument("name")
@click.option(
    "--include-environment",
    is_flag=True,
    help="Include stored environment values instead of redacting them.",
)
def get_command(name: str, include_environment: bool) -> None:
    """Get one schedule by NAME."""
    emit_payload(
        api.get_schedule_tool,
        {"name": name, "include_environment": include_environment},
    )


@main.command("create")
@click.argument("name")
@click.option(
    "--instruction", required=True, help="Natural-language task to run when due."
)
@click.option(
    "--harness",
    type=click.Choice(config.SUPPORTED_HARNESSES, case_sensitive=False),
    help="Agent harness to run. Defaults to SCHEDULE_SKILL_HARNESS or kodelet.",
)
@click.option(
    "--when",
    "when_value",
    required=True,
    help="Schedule expression, e.g. 'in 90 minutes', 'daily 09:00'.",
)
@click.option(
    "--timezone", help="IANA timezone for timezone-less schedule expressions."
)
@click.option(
    "--working-directory", help="Directory where the scheduled task should run."
)
@click.option(
    "--retention",
    default=config.DEFAULT_RETENTION,
    show_default=True,
    help="Finished run retention, e.g. 5d, 30h, 30min.",
)
@click.option(
    "--env",
    "environment",
    multiple=True,
    help="Extra environment variable as KEY=VALUE. May be repeated.",
)
@click.option(
    "--overwrite", is_flag=True, help="Replace an existing schedule with the same name."
)
@click.option("--disabled", is_flag=True, help="Create the schedule disabled.")
def create_command(
    name: str,
    instruction: str,
    harness: str | None,
    when_value: str,
    timezone: str | None,
    working_directory: str | None,
    retention: str,
    environment: tuple[str, ...],
    overwrite: bool,
    disabled: bool,
) -> None:
    """Create or replace a schedule."""
    payload: dict[str, Any] = {
        "name": name,
        "instruction": instruction,
        "when": when_value,
        "overwrite": overwrite,
        "enabled": not disabled,
        "retention": retention,
    }
    if harness:
        payload["harness"] = harness
    if timezone:
        payload["timezone"] = timezone
    if working_directory:
        payload["working_directory"] = working_directory
    parsed_environment = parse_env(environment)
    if parsed_environment:
        payload["environment"] = parsed_environment
    emit_payload(api.create_schedule_tool, payload)


@main.command("delete")
@click.argument("name")
@click.option("--missing-ok", is_flag=True, help="Treat an absent schedule as success.")
def delete_command(name: str, missing_ok: bool) -> None:
    """Delete one schedule by NAME."""
    emit_payload(api.delete_schedule_tool, {"name": name, "missing_ok": missing_ok})


@main.command("start")
def start_command() -> None:
    """Install and start the user-level daemon/service."""
    payload = service.start_daemon()
    echo_json(
        {
            "status": "success" if payload.get("installed") else "error",
            "daemon": payload,
        }
    )


@main.command("stop")
def stop_command() -> None:
    """Stop the user-level daemon/service without removing its config."""
    payload = service.stop_daemon()
    echo_json(
        {"status": "success" if payload.get("stopped") else "error", "daemon": payload}
    )


@main.command("uninstall")
def uninstall_command() -> None:
    """Stop and remove the user-level daemon/service config."""
    payload = service.uninstall_daemon()
    echo_json(
        {
            "status": "success" if payload.get("uninstalled") else "error",
            "daemon": payload,
        }
    )


@main.command("status")
def status_command() -> None:
    """Show dispatcher and daemon status."""
    echo_json(service.status_payload())


@main.command("dispatch-loop", hidden=True)
def dispatch_loop_command() -> None:
    raise SystemExit(dispatcher.dispatcher_loop())


@main.command("run-record", hidden=True)
@click.argument("record_path")
def run_record_command(record_path: str) -> None:
    raise SystemExit(runner.run_record(record_path))


if __name__ == "__main__":
    main()
