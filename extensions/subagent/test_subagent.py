from __future__ import annotations

import asyncio
import importlib.machinery
import importlib.util
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, ClassVar
from unittest import mock

RECURSION_GUARD_ENV = "KODELET_SUBAGENT_EXTENSION_CHILD"


def load_extension(module_name: str = "subagent_extension") -> ModuleType:
    path = Path(__file__).with_name("kodelet-extension-subagent")
    loader = importlib.machinery.SourceFileLoader(module_name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError(f"failed to load extension from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


guard_value = os.environ.pop(RECURSION_GUARD_ENV, None)
try:
    extension = load_extension()
finally:
    if guard_value is not None:
        os.environ[RECURSION_GUARD_ENV] = guard_value


class MutableClock:
    def __init__(self, value: float = 1_000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeUI:
    def __init__(self) -> None:
        self.widgets: dict[str, tuple[list[Any], dict[str, str] | None] | None] = {}
        self.updates: list[tuple[str, list[Any] | None]] = []

    async def set_widget(
        self,
        widget_id: str,
        lines: list[Any] | None,
        options: dict[str, str] | None = None,
    ) -> None:
        self.widgets[widget_id] = None if lines is None else (lines, options)
        self.updates.append((widget_id, lines))

    def text(self, widget_id: str) -> str:
        widget = self.widgets.get(widget_id)
        if widget is None:
            return ""
        lines, _options = widget
        rendered: list[str] = []
        for line in lines:
            if isinstance(line, str):
                rendered.append(line)
                continue
            rendered.append(
                "".join(str(span.get("text", "")) for span in line["spans"])
            )
        return "\n".join(rendered)


class FakeBackgroundTaskLease:
    def __init__(
        self,
        description: str | None,
        failures_remaining: int = 0,
    ) -> None:
        self.description = description
        self.failures_remaining = failures_remaining
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1
        if self.failures_remaining > 0:
            self.failures_remaining -= 1
            raise RuntimeError("temporary background lease release failure")


class FakeContext:
    def __init__(
        self,
        conversation_id: str,
        cwd: Path,
        data_dir: Path,
        *,
        invoked_by: str | None = None,
        block_fork: bool = False,
    ) -> None:
        self.conversation_id = conversation_id
        self.cwd = str(cwd)
        self.invoked_by = invoked_by
        self.storage = SimpleNamespace(data_dir=str(data_dir))
        self.ui = FakeUI()
        self.log = SimpleNamespace(warn=lambda _message: None)
        self.block_fork = block_fork
        self.fork_names: list[str | None] = []
        self.fork_started = asyncio.Event()
        self.fork_release = asyncio.Event()
        self.background_release_failures = 0
        self.background_leases: list[FakeBackgroundTaskLease] = []

    async def acquire_background_task(
        self,
        description: str | None = None,
    ) -> FakeBackgroundTaskLease:
        lease = FakeBackgroundTaskLease(
            description,
            self.background_release_failures,
        )
        self.background_leases.append(lease)
        return lease

    async def fork_conversation(self, name: str | None = None) -> str:
        self.fork_names.append(name)
        self.fork_started.set()
        if self.block_fork:
            await self.fork_release.wait()
        return f"child-{self.conversation_id}-{len(self.fork_names)}"


class FakeSession:
    def __init__(self, client: FakeClient, session_id: str) -> None:
        self.client = client
        self.id = session_id
        self.run_calls: list[str] = []
        self.steer_calls: list[str] = []
        self.close_calls = 0
        self.run_started = asyncio.Event()
        self.steer_received = asyncio.Event()

    async def run_and_wait(self, task: str) -> dict[str, str]:
        self.run_calls.append(task)
        self.run_started.set()
        type(self.client).run_started.set()
        if type(self.client).block_runs:
            await type(self.client).run_release.wait()
        failure = type(self.client).run_failure
        if failure is not None:
            raise failure
        return {"content": f"result for {task}"}

    async def steer(self, message: str) -> dict[str, str]:
        self.steer_calls.append(message)
        self.steer_received.set()
        outcome = (
            type(self.client).steer_outcomes.pop(0)
            if type(self.client).steer_outcomes
            else "injected"
            if type(self.client).accept_steering
            else "failed"
        )
        result = {"outcome": outcome}
        if outcome == "promptRequired":
            result["reason"] = "noRunningTurn"
        return result

    async def close(self) -> None:
        self.close_calls += 1


class FakeClient:
    instances: ClassVar[list[FakeClient]] = []
    block_runs: ClassVar[bool] = False
    block_startup: ClassVar[bool] = False
    block_close: ClassVar[bool] = False
    accept_steering: ClassVar[bool] = True
    steer_outcomes: ClassVar[list[str]] = []
    run_failure: ClassVar[Exception | None] = None
    run_release: ClassVar[asyncio.Event]
    run_started: ClassVar[asyncio.Event]
    startup_release: ClassVar[asyncio.Event]
    startup_started: ClassVar[asyncio.Event]
    close_release: ClassVar[asyncio.Event]
    close_started: ClassVar[asyncio.Event]
    fresh_session_count: ClassVar[int] = 0

    @classmethod
    def reset(cls) -> None:
        cls.instances = []
        cls.block_runs = False
        cls.block_startup = False
        cls.block_close = False
        cls.accept_steering = True
        cls.steer_outcomes = []
        cls.run_failure = None
        cls.run_release = asyncio.Event()
        cls.run_started = asyncio.Event()
        cls.startup_release = asyncio.Event()
        cls.startup_started = asyncio.Event()
        cls.close_release = asyncio.Event()
        cls.close_started = asyncio.Event()
        cls.fresh_session_count = 0

    def __init__(
        self,
        *,
        command: str,
        cwd: str,
        env: dict[str, str],
    ) -> None:
        self.command = command
        self.cwd = cwd
        self.env = env
        self.create_session_calls: list[dict[str, object]] = []
        self.sessions: list[FakeSession] = []
        self.close_calls = 0
        self.closed = False
        type(self).instances.append(self)

    async def create_session(self, **kwargs: object) -> FakeSession:
        self.create_session_calls.append(kwargs)
        type(self).startup_started.set()
        if type(self).block_startup:
            await type(self).startup_release.wait()
        resume = kwargs.get("resume")
        if isinstance(resume, str):
            session_id = resume
        else:
            type(self).fresh_session_count += 1
            session_id = f"fresh-{type(self).fresh_session_count}"
        session = FakeSession(self, session_id)
        self.sessions.append(session)
        return session

    async def close(self) -> None:
        self.close_calls += 1
        type(self).close_started.set()
        if type(self).block_close:
            await type(self).close_release.wait()
        for session in self.sessions:
            await session.close()
        self.closed = True


async def wait_until(
    predicate: Callable[[], Awaitable[bool]],
    *,
    timeout: float = 2.0,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not await predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("condition was not satisfied")
        await asyncio.sleep(0.01)


class SubagentExtensionTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.cwd = self.root / "workspace"
        self.cwd.mkdir()
        self.data_dir = self.root / "extension-data"
        self.data_dir.mkdir()

        extension._stores.clear()
        extension._live_runs.clear()
        extension._reservation_completions.clear()
        extension._widget_locks.clear()
        extension.__dict__["_shutting_down"] = False
        FakeClient.reset()
        self.client_patch = mock.patch.object(extension, "Client", FakeClient)
        self.client_patch.start()
        self.environment_patch = mock.patch.dict(
            os.environ,
            {extension.RECURSION_GUARD_ENV: "0"},
        )
        self.environment_patch.start()

    async def asyncTearDown(self) -> None:
        FakeClient.run_release.set()
        FakeClient.startup_release.set()
        FakeClient.close_release.set()
        for live in list(extension._live_runs.values()):
            if live.runner_task is not None and not live.runner_task.done():
                live.runner_task.cancel()
        await asyncio.gather(
            *(
                live.runner_task
                for live in list(extension._live_runs.values())
                if live.runner_task is not None
            ),
            return_exceptions=True,
        )
        extension._live_runs.clear()
        extension._stores.clear()
        extension._reservation_completions.clear()
        extension._widget_locks.clear()
        self.environment_patch.stop()
        self.client_patch.stop()
        self._temp.cleanup()

    def context(
        self,
        owner: str = "owner-1",
        *,
        data_dir: Path | None = None,
        invoked_by: str | None = None,
    ) -> FakeContext:
        return FakeContext(
            owner,
            self.cwd,
            data_dir or self.data_dir,
            invoked_by=invoked_by,
        )

    async def store(
        self,
        name: str,
        runtime_id: str,
        *,
        clock: Callable[[], float] = time.time,
    ) -> Any:
        path = self.root / name / extension.DATABASE_FILENAME
        store = extension.AgentStore(path, runtime_id, clock=clock)
        await store.initialize()
        return store

    async def wait_for_status(
        self,
        store: Any,
        owner: str,
        agent_id: str,
        *statuses: str,
    ) -> Any:
        record: Any = None

        async def has_status() -> bool:
            nonlocal record
            record = await store.get(owner, agent_id)
            return record.run.status in statuses

        await wait_until(has_status)
        return record

    def steering_rows(self, path: Path) -> list[tuple[str, int, str]]:
        with sqlite3.connect(path) as connection:
            rows = connection.execute(
                "SELECT run_id, generation, message FROM steering_messages ORDER BY id"
            ).fetchall()
        return [(str(row[0]), int(row[1]), str(row[2])) for row in rows]

    async def test_capability_gate_and_main_only_recursion_guards(self) -> None:
        params = {
            "extension": {
                "id": "subagent",
                "cwd": str(self.cwd),
                "dataDir": str(self.data_dir),
            },
            "capabilities": {"runtime": {"backgroundTasks": True}},
        }
        enabled = extension.ext.initialize(params)
        self.assertEqual(
            {tool["name"] for tool in enabled["tools"]},
            set(extension.AGENT_TOOL_NAMES),
        )

        for capabilities in (
            {"runtime": {"backgroundTasks": False}},
            {"runtime": {}},
            {},
        ):
            unavailable = extension.ext.initialize(
                {**params, "capabilities": capabilities}
            )
            self.assertEqual(unavailable["tools"], [])

        with mock.patch.dict(os.environ, {RECURSION_GUARD_ENV: "1"}):
            child_extension = load_extension("subagent_extension_child")
        child_initialized = child_extension.ext.initialize(params)
        self.assertEqual(child_initialized["tools"], [])

        child_context = self.context(invoked_by="spawn_agent")
        self.assertTrue(extension.is_agent_child(child_context))
        self.assertFalse(extension.is_agent_child(self.context(invoked_by="main")))
        disabled = await extension.disable_recursive_agents({}, child_context)
        self.assertEqual(
            disabled,
            {"tools": {"disable": list(extension.AGENT_TOOL_NAMES)}},
        )
        nested = await extension.spawn_agent(
            extension.SpawnAgentInput(task="nested work"),
            child_context,
        )
        self.assertIn("only available to the main agent", nested["error"])
        with mock.patch.dict(os.environ, {RECURSION_GUARD_ENV: "1"}):
            self.assertTrue(extension.is_agent_child(self.context()))

    async def test_schema_wal_and_records_survive_store_reconstruction(self) -> None:
        context = self.context()
        store = await extension.store_for_context(context)
        claim = await store.create(
            context.conversation_id,
            "persist this task",
            context.cwd,
            "fresh",
        )
        expected_path = self.data_dir / "subagents.sqlite"
        self.assertEqual(store.path, expected_path)
        self.assertTrue(expected_path.exists())

        with sqlite3.connect(expected_path) as connection:
            journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
            user_version = connection.execute("PRAGMA user_version").fetchone()[0]
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        self.assertEqual(journal_mode, "wal")
        self.assertEqual(user_version, 1)
        self.assertTrue({"agents", "runs", "steering_messages"} <= tables)

        extension._stores.clear()
        extension._live_runs.clear()
        reconstructed = extension.AgentStore(expected_path, "runtime-reconstructed")
        await reconstructed.initialize()
        persisted = await reconstructed.get(
            context.conversation_id,
            claim.agent.id,
            claim.lease.run_id,
        )
        self.assertEqual(persisted.run.task, "persist this task")
        self.assertEqual(persisted.status, "starting")

    async def test_spawn_fork_and_fresh_wait_and_list_ownership(self) -> None:
        context = self.context()
        FakeClient.block_runs = True
        forked = await extension.spawn_agent(
            extension.SpawnAgentInput(task="inspect authentication"),
            context,
        )
        forked_data = forked["data"]
        self.assertEqual(len(context.background_leases), 1)
        forked_lease = context.background_leases[0]
        self.assertIn(forked_data["agent_id"], forked_lease.description or "")
        self.assertEqual(forked_lease.close_calls, 0)
        await asyncio.wait_for(FakeClient.run_started.wait(), timeout=1)
        store = await extension.store_for_context(context)
        running = await self.wait_for_status(
            store,
            context.conversation_id,
            forked_data["agent_id"],
            "running",
        )
        self.assertEqual(running.conversation_id, "child-owner-1-1")
        self.assertEqual(running.context_mode, "fork")
        self.assertEqual(context.fork_names, ["inspect authentication"])
        self.assertEqual(
            FakeClient.instances[0].create_session_calls[0]["resume"],
            running.conversation_id,
        )
        self.assertIn("1 active", context.ui.text(extension.WIDGET_ID))
        self.assertIn("inspect authentication", context.ui.text(extension.WIDGET_ID))

        FakeClient.run_release.set()
        completed = await extension.wait_agent(
            extension.WaitAgentInput(
                agent_id=forked_data["agent_id"],
                run_id=forked_data["run_id"],
                timeout_ms=1_000,
            ),
            context,
        )
        self.assertEqual(completed["content"], "result for inspect authentication")
        self.assertEqual(completed["data"]["status"], "completed")
        self.assertIn("1 completed", context.ui.text(extension.WIDGET_ID))
        self.assertEqual(forked_lease.close_calls, 1)

        FakeClient.block_runs = False
        fresh = await extension.spawn_agent(
            extension.SpawnAgentInput(
                task="independent review",
                context_mode="fresh",
            ),
            context,
        )
        fresh_data = fresh["data"]
        fresh_result = await extension.wait_agent(
            extension.WaitAgentInput(
                agent_id=fresh_data["agent_id"],
                run_id=fresh_data["run_id"],
                timeout_ms=1_000,
            ),
            context,
        )
        self.assertEqual(fresh_result["content"], "result for independent review")
        fresh_client = FakeClient.instances[1]
        self.assertNotIn("resume", fresh_client.create_session_calls[0])
        self.assertEqual(fresh_result["data"]["context_mode"], "fresh")
        self.assertEqual(len(context.background_leases), 2)
        self.assertEqual(context.background_leases[1].close_calls, 1)

        listed = await extension.list_agents(extension.ListAgentsInput(), context)
        self.assertEqual(len(listed["data"]["agents"]), 2)
        other_context = self.context("owner-2")
        other_list = await extension.list_agents(
            extension.ListAgentsInput(),
            other_context,
        )
        self.assertEqual(other_list["data"]["agents"], [])
        hidden = await extension.wait_agent(
            extension.WaitAgentInput(
                agent_id=forked_data["agent_id"],
                timeout_ms=0,
            ),
            other_context,
        )
        self.assertIn("agent not found", hidden["error"])

        restored_context = self.context()
        await extension.restore_agent_widget({}, restored_context)
        restored_text = restored_context.ui.text(extension.WIDGET_ID)
        self.assertIn("2 completed", restored_text)
        self.assertIn("independent review", restored_text)

    async def test_wait_exact_historical_run_and_followup_resumes_child(self) -> None:
        context = self.context()
        spawned = await extension.spawn_agent(
            extension.SpawnAgentInput(task="first pass"),
            context,
        )
        agent_id = spawned["data"]["agent_id"]
        first_run_id = spawned["data"]["run_id"]
        first = await extension.wait_agent(
            extension.WaitAgentInput(
                agent_id=agent_id,
                run_id=first_run_id,
                timeout_ms=1_000,
            ),
            context,
        )
        child_id = first["data"]["conversation_id"]

        FakeClient.block_runs = True
        FakeClient.run_release = asyncio.Event()
        FakeClient.run_started = asyncio.Event()
        followup = await extension.followup_agent(
            extension.FollowupAgentInput(
                agent_id=agent_id,
                task="second pass",
            ),
            context,
        )
        second_run_id = followup["data"]["run_id"]
        await asyncio.wait_for(FakeClient.run_started.wait(), timeout=1)
        self.assertEqual(
            FakeClient.instances[1].create_session_calls[0]["resume"],
            child_id,
        )

        historical = await extension.wait_agent(
            extension.WaitAgentInput(
                agent_id=agent_id,
                run_id=first_run_id,
                timeout_ms=0,
            ),
            context,
        )
        self.assertEqual(historical["content"], "result for first pass")
        self.assertEqual(historical["data"]["run_id"], first_run_id)
        self.assertEqual(historical["data"]["status"], "completed")

        current = await extension.wait_agent(
            extension.WaitAgentInput(agent_id=agent_id, timeout_ms=0),
            context,
        )
        self.assertEqual(current["data"]["run_id"], second_run_id)
        self.assertEqual(current["data"]["status"], "running")
        FakeClient.run_release.set()
        second = await extension.wait_agent(
            extension.WaitAgentInput(
                agent_id=agent_id,
                run_id=second_run_id,
                timeout_ms=1_000,
            ),
            context,
        )
        self.assertEqual(second["content"], "result for second pass")

    async def test_followup_reforks_when_interrupted_before_attachment(self) -> None:
        context = self.context()
        store = await extension.store_for_context(context)
        initial = await store.create(
            context.conversation_id,
            "initial setup",
            context.cwd,
            "fork",
        )
        interrupted = await store.terminal(
            initial.lease,
            "interrupted",
            error="setup interrupted",
        )
        self.assertIsNone(interrupted.conversation_id)

        followup = await extension.followup_agent(
            extension.FollowupAgentInput(
                agent_id=initial.agent.id,
                task="retry setup",
            ),
            context,
        )
        self.assertEqual(context.fork_names, ["retry setup"])
        self.assertEqual(
            followup["data"]["conversation_id"],
            "child-owner-1-1",
        )
        completed = await extension.wait_agent(
            extension.WaitAgentInput(
                agent_id=initial.agent.id,
                run_id=followup["data"]["run_id"],
                timeout_ms=1_000,
            ),
            context,
        )
        self.assertEqual(completed["content"], "result for retry setup")

    async def test_store_limits_are_atomic_per_owner_and_globally(self) -> None:
        owner_path = self.root / "owner-limit" / extension.DATABASE_FILENAME
        owner_stores = [
            extension.AgentStore(owner_path, f"runtime-owner-{index}")
            for index in range(4)
        ]
        await asyncio.gather(*(store.initialize() for store in owner_stores))
        owner_results = await asyncio.gather(
            *(
                store.create("one-owner", f"task-{index}", str(self.cwd), "fresh")
                for index, store in enumerate(owner_stores)
            ),
            return_exceptions=True,
        )
        self.assertEqual(
            sum(isinstance(result, extension.Claim) for result in owner_results),
            3,
        )
        owner_errors = [
            result for result in owner_results if isinstance(result, BaseException)
        ]
        self.assertEqual(len(owner_errors), 1)
        self.assertIsInstance(owner_errors[0], extension.AgentLimitError)

        total_path = self.root / "total-limit" / extension.DATABASE_FILENAME
        total_stores = [
            extension.AgentStore(total_path, f"runtime-total-{index}")
            for index in range(9)
        ]
        await asyncio.gather(*(store.initialize() for store in total_stores))
        total_results = await asyncio.gather(
            *(
                store.create(
                    f"owner-{index // 3}",
                    f"task-{index}",
                    str(self.cwd),
                    "fresh",
                )
                for index, store in enumerate(total_stores)
            ),
            return_exceptions=True,
        )
        self.assertEqual(
            sum(isinstance(result, extension.Claim) for result in total_results),
            8,
        )
        total_errors = [
            result for result in total_results if isinstance(result, BaseException)
        ]
        self.assertEqual(len(total_errors), 1)
        self.assertIsInstance(total_errors[0], extension.AgentLimitError)
        self.assertIn("extension", str(total_errors[0]))

    async def test_expiry_reconciliation_and_generation_runtime_fencing(self) -> None:
        clock = MutableClock()
        store = await self.store("leases", "runtime-a", clock=clock)
        claim = await store.create("owner", "work", str(self.cwd), "fresh")
        await store.mark_running(claim.lease, "child-lease")
        clock.advance(extension.LEASE_DURATION_SECONDS + 0.1)
        self.assertEqual(await store.reconcile_expired(), 1)
        expired = await store.get("owner", claim.agent.id)
        self.assertEqual(expired.status, "interrupted")
        self.assertEqual(expired.run.status, "interrupted")
        self.assertIn("lease expired", expired.run.error)

        resumed = await store.claim("owner", claim.agent.id, "retry")
        with self.assertRaises(extension.LeaseLostError):
            await store.heartbeat(claim.lease)

        second_runtime = extension.AgentStore(
            store.path,
            "runtime-b",
            clock=clock,
        )
        await second_runtime.initialize()
        forged_runtime = replace(resumed.lease, runtime_id="runtime-b")
        with self.assertRaises(extension.LeaseLostError):
            await second_runtime.heartbeat(forged_runtime)

    async def test_session_end_interrupts_only_current_runtime_rows(self) -> None:
        path = self.root / "session-end" / extension.DATABASE_FILENAME
        current = extension.AgentStore(path, extension.RUNTIME_ID)
        other = extension.AgentStore(path, "other-runtime")
        await current.initialize()
        await other.initialize()
        current_claim = await current.create(
            "current-owner",
            "current task",
            str(self.cwd),
            "fresh",
        )
        other_claim = await other.create(
            "other-owner",
            "other task",
            str(self.cwd),
            "fresh",
        )
        extension._stores[path] = current

        await extension.interrupt_live_agents({}, self.context())

        current_record = await current.get("current-owner", current_claim.agent.id)
        other_record = await current.get("other-owner", other_claim.agent.id)
        self.assertEqual(current_record.status, "interrupted")
        self.assertEqual(current_record.run.status, "interrupted")
        self.assertEqual(other_record.status, "starting")
        self.assertEqual(other_record.run.status, "starting")

    async def test_steering_is_run_scoped_delivered_and_acknowledged(
        self,
    ) -> None:
        store = await self.store("steering", "runtime-steering")
        claim = await store.create("owner", "long task", str(self.cwd), "fresh")
        await store.mark_running(claim.lease, "child-steering")
        first = await store.enqueue_steering(
            "owner",
            claim.agent.id,
            "focus on the parser",
        )
        self.assertEqual(first, {"accepted": True, "alreadyPending": False})
        self.assertEqual(
            self.steering_rows(store.path),
            [(claim.lease.run_id, claim.lease.generation, "focus on the parser")],
        )

        client = FakeClient(command="kodelet", cwd=str(self.cwd), env={})
        session = FakeSession(client, "child-steering")
        live = extension.live_run_from_claim(claim, "long task", store)
        live.conversation_id = session.id
        FakeClient.steer_outcomes = ["promptRequired", "injected"]
        with (
            mock.patch.object(extension, "STEERING_POLL_SECONDS", 0.01),
            mock.patch.object(extension, "STEERING_RETRY_SECONDS", 0.01),
        ):
            pump = asyncio.create_task(extension.steering_pump(live, session))
            try:
                await asyncio.wait_for(session.steer_received.wait(), timeout=1)

                async def acknowledged() -> bool:
                    return not self.steering_rows(store.path)

                await wait_until(acknowledged)
            finally:
                pump.cancel()
                await asyncio.gather(pump, return_exceptions=True)
        self.assertEqual(
            session.steer_calls,
            ["focus on the parser", "focus on the parser"],
        )
        self.assertIsNone(await store.next_steering(claim.lease))

        await store.enqueue_steering(
            "owner",
            claim.agent.id,
            "message carried into the follow-up run",
        )
        terminal = await store.terminal(
            claim.lease,
            "idle",
            conversation_id=session.id,
            result="done",
        )
        self.assertEqual(terminal.run.status, "completed")
        self.assertEqual(
            self.steering_rows(store.path),
            [
                (
                    claim.lease.run_id,
                    claim.lease.generation,
                    "message carried into the follow-up run",
                )
            ],
        )

        followup = await store.claim("owner", claim.agent.id, "follow-up task")
        self.assertEqual(
            self.steering_rows(store.path),
            [
                (
                    followup.lease.run_id,
                    followup.lease.generation,
                    "message carried into the follow-up run",
                )
            ],
        )
        await store.mark_running(followup.lease, session.id)
        carried = await store.next_steering(followup.lease)
        self.assertIsNotNone(carried)
        assert carried is not None
        self.assertEqual(carried.message, "message carried into the follow-up run")
        self.assertTrue(await store.acknowledge_steering(followup.lease, carried.id))
        self.assertEqual(self.steering_rows(store.path), [])

    async def test_cancel_persists_cleanup(self) -> None:
        context = self.context()
        FakeClient.block_runs = True
        FakeClient.block_close = True
        spawned = await extension.spawn_agent(
            extension.SpawnAgentInput(task="cancel me", context_mode="fresh"),
            context,
        )
        self.assertEqual(len(context.background_leases), 1)
        background_lease = context.background_leases[0]
        self.assertEqual(background_lease.close_calls, 0)
        agent_id = spawned["data"]["agent_id"]
        await asyncio.wait_for(FakeClient.run_started.wait(), timeout=1)
        store = await extension.store_for_context(context)
        await store.enqueue_steering(context.conversation_id, agent_id, "pending")

        with mock.patch.object(extension, "CANCEL_CLEANUP_TIMEOUT_SECONDS", 0.01):
            canceled = await extension.cancel_agent(
                extension.CancelAgentInput(agent_id=agent_id),
                context,
            )
        self.assertIn("cleanup is still finishing", canceled["content"])
        persisted = await store.get(context.conversation_id, agent_id)
        self.assertEqual(persisted.status, "canceled")
        self.assertEqual(persisted.run.status, "canceled")
        self.assertEqual(self.steering_rows(store.path), [])

        client = FakeClient.instances[0]
        session = client.sessions[0]
        self.assertFalse(client.closed)
        await asyncio.wait_for(FakeClient.close_started.wait(), timeout=1)
        FakeClient.close_release.set()

        key = extension.live_run_key(store, agent_id)

        async def cleaned_up() -> bool:
            return key not in extension._live_runs

        await wait_until(cleaned_up)
        self.assertTrue(client.closed)
        self.assertEqual(client.close_calls, 1)
        self.assertEqual(session.close_calls, 1)
        self.assertEqual(background_lease.close_calls, 1)

    async def test_startup_timeout_is_persisted_as_failure(self) -> None:
        context = self.context()
        FakeClient.block_startup = True
        with mock.patch.object(extension, "AGENT_START_TIMEOUT_SECONDS", 0.02):
            spawned = await extension.spawn_agent(
                extension.SpawnAgentInput(
                    task="never starts",
                    context_mode="fresh",
                ),
                context,
            )
            await asyncio.wait_for(FakeClient.startup_started.wait(), timeout=1)
            store = await extension.store_for_context(context)
            failed = await self.wait_for_status(
                store,
                context.conversation_id,
                spawned["data"]["agent_id"],
                "failed",
            )
        self.assertEqual(failed.run.status, "failed")
        self.assertIn("timed out while starting", failed.run.error)

        async def background_lease_closed() -> bool:
            return bool(
                context.background_leases
                and context.background_leases[0].close_calls == 1
            )

        await wait_until(background_lease_closed)
        self.assertTrue(FakeClient.instances[0].closed)

    async def test_background_lease_release_retries_transient_failures(self) -> None:
        context = self.context()
        context.background_release_failures = 1
        with mock.patch.object(
            extension,
            "BACKGROUND_LEASE_RELEASE_RETRY_INITIAL_SECONDS",
            0.001,
        ):
            spawned = await extension.spawn_agent(
                extension.SpawnAgentInput(task="retry lease release", context_mode="fresh"),
                context,
            )
            await extension.wait_agent(
                extension.WaitAgentInput(
                    agent_id=spawned["data"]["agent_id"],
                    run_id=spawned["data"]["run_id"],
                    timeout_ms=1_000,
                ),
                context,
            )

            async def released() -> bool:
                return bool(
                    context.background_leases
                    and context.background_leases[0].close_calls == 2
                )

            await wait_until(released)

    async def test_terminal_retry_and_idempotency(self) -> None:
        clock = MutableClock()
        store = await self.store("terminal-retry", "runtime-retry", clock=clock)
        claim = await store.create("owner", "retry update", str(self.cwd), "fresh")
        await store.mark_running(claim.lease, "child-retry")
        live = extension.live_run_from_claim(claim, "retry update", store)
        live.conversation_id = "child-retry"
        original_terminal = store.terminal
        attempts = 0

        async def flaky_terminal(*args: Any, **kwargs: Any) -> Any:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise sqlite3.OperationalError("database is busy")
            return await original_terminal(*args, **kwargs)

        with (
            mock.patch.object(store, "terminal", new=flaky_terminal),
            mock.patch.object(
                extension,
                "WORKER_UPDATE_RETRY_INITIAL_SECONDS",
                0.001,
            ),
            mock.patch.object(extension, "WORKER_UPDATE_RETRY_MAX_SECONDS", 0.002),
        ):
            committed = await extension.safe_worker_terminal(
                live,
                "idle",
                result="final answer",
            )
        self.assertTrue(committed)
        self.assertEqual(attempts, 3)

        repeated = await store.terminal(
            claim.lease,
            "idle",
            conversation_id="child-retry",
            result="final answer",
        )
        self.assertEqual(repeated.run.status, "completed")
        self.assertEqual(repeated.run.result, "final answer")
        with self.assertRaises(extension.LeaseLostError):
            await store.terminal(
                claim.lease,
                "idle",
                conversation_id="child-retry",
                result="different answer",
            )

    async def test_canceled_reservation_is_compensated_after_commit(self) -> None:
        store = await self.store("canceled-reservation", "runtime-cancel")
        started = asyncio.Event()
        release = asyncio.Event()

        async def delayed_create() -> Any:
            started.set()
            await release.wait()
            return await store.create("owner", "delayed", str(self.cwd), "fresh")

        reservation = asyncio.create_task(
            extension.reserve_claim(
                delayed_create(),
                "delayed",
                store,
                initial=True,
            )
        )
        await started.wait()
        reservation.cancel()
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await reservation
        self.assertEqual(await store.list("owner"), [])

    async def test_shutdown_barrier_compensates_concurrent_reservation(self) -> None:
        context = self.context()
        store = await extension.store_for_context(context)
        started = asyncio.Event()
        release = asyncio.Event()

        async def delayed_create() -> Any:
            started.set()
            await release.wait()
            return await store.create(
                context.conversation_id,
                "during shutdown",
                context.cwd,
                "fresh",
            )

        reservation = asyncio.create_task(
            extension.reserve_claim(
                delayed_create(),
                "during shutdown",
                store,
                initial=True,
            )
        )
        await started.wait()
        shutdown = asyncio.create_task(extension.interrupt_live_agents({}, context))
        await asyncio.sleep(0)
        self.assertFalse(shutdown.done())
        release.set()
        await shutdown
        with self.assertRaisesRegex(RuntimeError, "shutting down"):
            await reservation
        self.assertEqual(await store.list(context.conversation_id), [])


if __name__ == "__main__":
    unittest.main()
