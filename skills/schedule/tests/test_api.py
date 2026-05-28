from __future__ import annotations

import json

from agentic_schedule import api, dispatcher, models, store


def test_create_list_get_delete_round_trip(capsys):
    assert (
        api.create_schedule_tool(
            json.dumps(
                {
                    "name": "daily-demo",
                    "instruction": "Say hello",
                    "when": "daily 09:00",
                    "timezone": "UTC",
                    "environment": {"TOKEN": "secret"},
                }
            )
        )
        == 0
    )
    created = json.loads(capsys.readouterr().out)
    assert created["status"] == "success"
    assert created["schedule"]["schedule"] == {
        "kind": "daily",
        "time": "09:00:00",
        "timezone": "UTC",
    }
    assert created["schedule"]["environment"] == {"TOKEN": "<redacted>"}
    assert created["schedule"]["retention"] == "5d"
    assert created["schedule"]["retention_seconds"] == 5 * 24 * 60 * 60
    assert "kodelet_flags" not in created["schedule"]

    assert api.list_schedule_tool("{}") == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["count"] == 1
    assert listed["schedules"][0]["name"] == "daily-demo"

    assert (
        api.get_schedule_tool(
            json.dumps({"name": "daily-demo", "include_environment": True})
        )
        == 0
    )
    fetched = json.loads(capsys.readouterr().out)
    assert fetched["schedule"]["environment"] == {"TOKEN": "secret"}

    assert api.delete_schedule_tool(json.dumps({"name": "daily-demo"})) == 0
    deleted = json.loads(capsys.readouterr().out)
    assert deleted["deleted_schedule"]["name"] == "daily-demo"


def test_create_rejects_hidden_kodelet_flags(capsys):
    assert (
        api.create_schedule_tool(
            json.dumps(
                {
                    "name": "bad",
                    "instruction": "Say hello",
                    "when": "in 90 minutes",
                    "kodelet_flags": [],
                }
            )
        )
        == 1
    )
    payload = json.loads(capsys.readouterr().out)
    assert "unsupported parameter" in payload["error"]
    assert "kodelet_flags" in payload["error"]


def test_create_rejects_invalid_retention(capsys):
    assert (
        api.create_schedule_tool(
            json.dumps(
                {
                    "name": "bad",
                    "instruction": "Say hello",
                    "when": "in 90 minutes",
                    "retention": "0d",
                }
            )
        )
        == 1
    )

    payload = json.loads(capsys.readouterr().out)
    assert "invalid duration" in payload["error"]


def test_create_does_not_start_dispatcher(capsys, monkeypatch):
    def fail_dispatcher_start() -> dict[str, object]:
        raise AssertionError("create must not start the dispatcher")

    monkeypatch.setattr(dispatcher, "ensure_dispatcher_running", fail_dispatcher_start)

    assert (
        api.create_schedule_tool(
            json.dumps(
                {"name": "passive-create", "instruction": "Say hello", "when": "now"}
            )
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["dispatcher"]["running"] is False


def test_list_does_not_start_dispatcher(capsys, monkeypatch):
    def fail_dispatcher_start() -> dict[str, object]:
        raise AssertionError("list must not start the dispatcher")

    monkeypatch.setattr(dispatcher, "ensure_dispatcher_running", fail_dispatcher_start)
    schedule = models.build_schedule(
        {"name": "passive-list", "instruction": "Say hello", "when": "now"}
    )
    with store.state_lock():
        store.save_state_unlocked(
            {"version": 1, "schedules": {"passive-list": schedule}}
        )

    assert api.list_schedule_tool("{}") == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["active_count"] == 1
    assert payload["dispatcher"]["running"] is False
