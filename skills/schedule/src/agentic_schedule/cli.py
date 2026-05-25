from __future__ import annotations

import json
from typing import Any

import click

from agentic_schedule import core


def emit_payload(handler: Any, payload: dict[str, Any]) -> None:
    raise SystemExit(handler(json.dumps(payload)))


def parse_env(values: tuple[str, ...]) -> dict[str, str]:
    environment: dict[str, str] = {}
    for value in values:
        key, separator, item = value.partition("=")
        if not separator or not key:
            raise click.BadParameter("environment entries must use KEY=VALUE", param_hint="--env")
        environment[key] = item
    return environment


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def main() -> None:
    """Manage background agentic schedules."""


@main.command("list")
@click.option("--active-only", is_flag=True, help="Only include active schedules.")
@click.option("--include-environment", is_flag=True, help="Include stored environment values instead of redacting them.")
def list_command(active_only: bool, include_environment: bool) -> None:
    """List schedules and ensure the dispatcher is running when needed."""
    emit_payload(
        core.list_schedule_tool,
        {
            "include_inactive": not active_only,
            "include_environment": include_environment,
        },
    )


@main.command("get")
@click.argument("name")
@click.option("--include-environment", is_flag=True, help="Include stored environment values instead of redacting them.")
def get_command(name: str, include_environment: bool) -> None:
    """Get one schedule by NAME."""
    emit_payload(core.get_schedule_tool, {"name": name, "include_environment": include_environment})


@main.command("create")
@click.argument("name")
@click.option("--instruction", required=True, help="Natural-language task to run when due.")
@click.option("--when", "when_value", required=True, help="Schedule expression, e.g. 'in 90 minutes', 'daily 09:00'.")
@click.option("--timezone", help="IANA timezone for timezone-less schedule expressions.")
@click.option("--working-directory", help="Directory where the scheduled task should run.")
@click.option("--env", "environment", multiple=True, help="Extra environment variable as KEY=VALUE. May be repeated.")
@click.option("--overwrite", is_flag=True, help="Replace an existing schedule with the same name.")
@click.option("--disabled", is_flag=True, help="Create the schedule disabled.")
def create_command(
    name: str,
    instruction: str,
    when_value: str,
    timezone: str | None,
    working_directory: str | None,
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
    }
    if timezone:
        payload["timezone"] = timezone
    if working_directory:
        payload["working_directory"] = working_directory
    parsed_environment = parse_env(environment)
    if parsed_environment:
        payload["environment"] = parsed_environment
    emit_payload(core.create_schedule_tool, payload)


@main.command("delete")
@click.argument("name")
@click.option("--missing-ok", is_flag=True, help="Treat an absent schedule as success.")
def delete_command(name: str, missing_ok: bool) -> None:
    """Delete one schedule by NAME."""
    emit_payload(core.delete_schedule_tool, {"name": name, "missing_ok": missing_ok})


@main.command("dispatch-loop", hidden=True)
def dispatch_loop_command() -> None:
    raise SystemExit(core.dispatcher_loop())


@main.command("run-record", hidden=True)
@click.argument("record_path")
def run_record_command(record_path: str) -> None:
    raise SystemExit(core.run_record(record_path))


if __name__ == "__main__":
    main()
