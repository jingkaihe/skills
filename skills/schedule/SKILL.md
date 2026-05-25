---
name: schedule
description: Manage scheduled, background agentic tasks. Use when users ask to create, list, inspect, or delete schedules, recurring agent tasks, reminders, or background dispatch of natural-language instructions.
---

# Agentic Schedule Manager

Use the skill-local scheduler command to manage background agentic work. Run commands from this skill directory through the wrapper script:

```bash
skills/schedule/scripts/agentic-schedule <command> [options]
```

The wrapper executes a uv project in `skills/schedule`, so dependencies are declared in `skills/schedule/pyproject.toml` and the Python source lives under `skills/schedule/src/`.

## Commands

| Command | Purpose |
| --- | --- |
| `list` | List schedules and ensure the dispatcher is running when active schedules exist |
| `get NAME` | Get one schedule by name |
| `create NAME --when WHEN --instruction INSTRUCTION` | Create or replace a schedule |
| `delete NAME` | Delete one schedule by name |
| `start` | Install and start a persistent user-level scheduler daemon |
| `stop` | Stop the scheduler daemon without removing its user-level config |
| `uninstall` | Stop the scheduler daemon and remove its user-level config |
| `status` | Show daemon, dispatcher, and schedule counts |

Examples:

```bash
skills/schedule/scripts/agentic-schedule list
skills/schedule/scripts/agentic-schedule status
skills/schedule/scripts/agentic-schedule start
skills/schedule/scripts/agentic-schedule stop
skills/schedule/scripts/agentic-schedule uninstall
skills/schedule/scripts/agentic-schedule get daily-repo-health
skills/schedule/scripts/agentic-schedule delete daily-repo-health
```

## When to use

Use this skill when the user wants an agentic task to happen later or repeatedly, for example:

- “Run this check every morning”
- “Create a weekly agent task”
- “List my schedules”
- “Delete the nightly cleanup schedule”

## Creating schedules

Use `skills/schedule/scripts/agentic-schedule create` with:

- positional `NAME`: stable snake/kebab/dot-style name using only letters, numbers, `.`, `_`, `-`.
- `--instruction`: natural-language task for the scheduled agentic work.
- `--when`: one of the supported schedule expressions below.
- Optional `--timezone`: IANA name such as `America/New_York` for timezone-less times.
- Optional `--working-directory`: where the scheduled task should run. Defaults to the current working directory.
- Optional `--overwrite` to replace an existing schedule.
- Optional repeated `--env KEY=VALUE` for extra environment variables.

The scheduler intentionally hides execution flags, so agents only need to decide the natural-language instruction and timing.

Supported `--when` formats:

| Format | Meaning |
| --- | --- |
| `now` | Run once immediately |
| `in 90 minutes` | Run once after a dateparser-readable relative time |
| `2026-05-25T14:00:00-04:00` | Run once at an absolute time |
| `every 1 hour 30 minutes` | Run repeatedly at that interval; first run after the interval |
| `every 1d starting now` | Run repeatedly, first run now |
| `every 2h starting 2026-05-25T15:00:00Z` | Run repeatedly from an anchor time |
| `daily 09:00` | Run every day at local or specified timezone time |
| `weekly monday 09:00` | Run every week at local or specified timezone time |

One-time dates and interval durations are parsed with `dateparser`, so natural relative phrases such as `in 90 minutes` work. Daily and weekly recurring schedules remain explicit wrappers around the parsed time of day.

If the user gives a relative or informal time (“tomorrow morning”, “every weekday”), translate it to one of these explicit expressions before calling the command. If the requested cadence cannot be represented exactly, explain the nearest supported option and ask a narrow clarification.

Create example:

```bash
skills/schedule/scripts/agentic-schedule create daily-repo-health \
  --instruction "Inspect this repository for failing tests or obvious regressions and summarize what needs attention." \
  --when "daily 09:00" \
  --timezone America/New_York
```

## Dispatcher behavior

- Run `skills/schedule/scripts/agentic-schedule start` to install and start a persistent user-level daemon. This uses `systemd --user` on Linux or LaunchAgents on macOS and does not require sudo.
- Use `skills/schedule/scripts/agentic-schedule status` to inspect daemon state, dispatcher state, and active schedule counts.
- Use `skills/schedule/scripts/agentic-schedule stop` to stop the daemon while keeping its config installed.
- Use `skills/schedule/scripts/agentic-schedule uninstall` to stop the daemon and remove its user-level config. This does not delete stored schedules or logs.
- Creating an enabled schedule still starts a background dispatcher opportunistically if no daemon is running.
- `list` also starts the dispatcher if active schedules exist and it is not running. Use this after a reboot if a daemon has not been installed yet.
- Schedules have a fixed execution deadline of 10 minutes. If the dispatcher notices a run more than 10 minutes after its scheduled time, it marks that occurrence `skipped` instead of executing stale work.
- State lives under the scheduler state directory by default.
- Set `AGENTIC_SCHEDULE_DIR` to use a specific state directory.
- Set `AGENTIC_SCHEDULE_POLL_SECONDS` to change dispatcher polling frequency; default is 30 seconds.
- Run logs are stored under the scheduler state directory in `logs/<schedule-name>/`.

For reliable reboot/login behavior, install the user-level daemon with `start`.

## Output and secrets

Schedule output is JSON and includes next run times, recent run status, log paths, and dispatcher status. Environment values are redacted by default. Only pass `--include-environment` to `list` or `get` if the user explicitly needs to inspect stored environment values.

Deleting a schedule does not stop already-running work that was launched before deletion.
