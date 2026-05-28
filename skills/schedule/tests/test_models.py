from __future__ import annotations

import pytest

from agentic_schedule import models


def test_build_schedule_accepts_compact_retention_duration():
    schedule = models.build_schedule(
        {
            "name": "retention-demo",
            "instruction": "Say hello",
            "when": "in 90 minutes",
            "retention": "30min",
        }
    )

    assert schedule["retention"] == "30min"
    assert schedule["retention_seconds"] == 30 * 60


def test_build_schedule_uses_harness_environment(monkeypatch):
    monkeypatch.setenv("SCHEDULE_SKILL_HARNESS", "claude")

    schedule = models.build_schedule(
        {"name": "env-harness", "instruction": "Say hello", "when": "in 90 minutes"}
    )

    assert schedule["harness"] == "claude"


def test_build_schedule_explicit_harness_overrides_environment(monkeypatch):
    monkeypatch.setenv("SCHEDULE_SKILL_HARNESS", "claude")

    schedule = models.build_schedule(
        {
            "name": "explicit-harness",
            "instruction": "Say hello",
            "harness": "codex",
            "when": "in 90 minutes",
        }
    )

    assert schedule["harness"] == "codex"


def test_build_schedule_rejects_invalid_harness_environment(monkeypatch):
    monkeypatch.setenv("SCHEDULE_SKILL_HARNESS", "unknown")

    with pytest.raises(ValueError, match="harness must be one of"):
        models.build_schedule(
            {"name": "bad-harness", "instruction": "Say hello", "when": "in 90 minutes"}
        )
