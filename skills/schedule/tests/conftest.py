from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolated_schedule_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTIC_SCHEDULE_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTIC_SCHEDULE_DISABLE_DISPATCHER", "1")
    monkeypatch.delenv("SCHEDULE_SKILL_HARNESS", raising=False)
    monkeypatch.setenv("TZ", "UTC")
