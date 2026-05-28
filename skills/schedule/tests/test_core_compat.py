from __future__ import annotations

from agentic_schedule import (
    api,
    config,
    core,
    dispatcher,
    models,
    runner,
    service,
    store,
    timeparse,
)


def test_core_facade_reexports_public_scheduler_api():
    assert core.create_schedule_tool is api.create_schedule_tool
    assert core.DEFAULT_RETENTION == config.DEFAULT_RETENTION
    assert core.SUPPORTED_HARNESSES == config.SUPPORTED_HARNESSES
    assert core.build_schedule is models.build_schedule
    assert core.prepare_due_runs is dispatcher.prepare_due_runs
    assert core.build_harness_command is runner.build_harness_command
    assert core.start_daemon is service.start_daemon
    assert core.state_lock is store.state_lock
    assert core.format_dt is timeparse.format_dt
