from __future__ import annotations

import asyncio
import importlib.machinery
import importlib.util
import os
import sys
import unittest
from pathlib import Path
from types import ModuleType
from typing import ClassVar
from unittest import mock

RECURSION_GUARD_ENV = "KODELET_SUBAGENT_EXTENSION_CHILD"


def load_extension(module_name: str = "async_agent_extension") -> ModuleType:
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


class FakeContext:
    def __init__(
        self,
        conversation_id: str,
        cwd: str,
        *,
        invoked_by: str | None = None,
        fork_unavailable: bool = False,
        block_fork: bool = False,
    ) -> None:
        self.conversation_id = conversation_id
        self.cwd = cwd
        self.invoked_by = invoked_by
        self.fork_unavailable = fork_unavailable
        self.block_fork = block_fork
        self.fork_names: list[str | None] = []
        self.fork_started = asyncio.Event()
        self.fork_release = asyncio.Event()

    async def fork_conversation(self, name: str | None = None) -> str:
        self.fork_names.append(name)
        self.fork_started.set()
        if self.block_fork:
            await self.fork_release.wait()
        if self.fork_unavailable:
            raise extension.ConversationForkUnavailableError("fork unavailable")
        return f"child-{self.conversation_id}-{len(self.fork_names)}"


class FakeSession:
    def __init__(self, client: FakeClient, session_id: str) -> None:
        self.client = client
        self.id = session_id

    async def run_and_wait(self, task: str) -> dict[str, str]:
        FakeClient.started_count += 1
        FakeClient.started.set()
        if FakeClient.block_runs:
            await FakeClient.release.wait()
        if FakeClient.failure is not None:
            raise FakeClient.failure
        return {"content": f"result for {task}"}


class FakeClient:
    instances: ClassVar[list[FakeClient]] = []
    block_runs: ClassVar[bool] = False
    failure: ClassVar[Exception | None] = None
    release: ClassVar[asyncio.Event]
    started: ClassVar[asyncio.Event]
    started_count: ClassVar[int] = 0
    fresh_session_count: ClassVar[int] = 0

    @classmethod
    def reset(cls) -> None:
        cls.instances = []
        cls.block_runs = False
        cls.failure = None
        cls.release = asyncio.Event()
        cls.started = asyncio.Event()
        cls.started_count = 0
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
        self.closed = False
        self.create_session_kwargs: list[dict[str, object]] = []
        type(self).instances.append(self)

    async def create_session(self, **kwargs: object) -> FakeSession:
        self.create_session_kwargs.append(kwargs)
        resume = kwargs.get("resume")
        if isinstance(resume, str):
            session_id = resume
        else:
            type(self).fresh_session_count += 1
            session_id = f"fresh-{type(self).fresh_session_count}"
        return FakeSession(self, session_id)

    async def close(self) -> None:
        self.closed = True


class AsyncAgentExtensionTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        extension._jobs.clear()
        FakeClient.reset()
        self.client_patch = mock.patch.object(extension, "Client", FakeClient)
        self.client_patch.start()
        self.environment_patch = mock.patch.dict(
            os.environ,
            {extension.RECURSION_GUARD_ENV: "0"},
        )
        self.environment_patch.start()
        self.cwd = str(Path(__file__).parent)

    async def asyncTearDown(self) -> None:
        tasks = [
            job.runner_task
            for job in extension._jobs.values()
            if job.runner_task is not None and not job.runner_task.done()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        extension._jobs.clear()
        self.environment_patch.stop()
        self.client_patch.stop()

    async def test_exposes_only_async_agent_tools_and_disables_them_for_children(
        self,
    ) -> None:
        initialized = extension.ext.initialize({"extension": {"id": "subagent"}})
        tool_names = {tool["name"] for tool in initialized["tools"]}
        self.assertEqual(tool_names, set(extension.AGENT_TOOL_NAMES))
        self.assertNotIn("subagent", tool_names)

        with mock.patch.dict(os.environ, {RECURSION_GUARD_ENV: "1"}):
            child_extension = load_extension("async_agent_extension_child")
        child_initialized = child_extension.ext.initialize(
            {"extension": {"id": "subagent"}}
        )
        self.assertEqual(child_initialized["tools"], [])

        child_context = FakeContext(
            "child-owner",
            self.cwd,
            invoked_by="other_delegator",
        )
        event_result = await extension.disable_recursive_agents({}, child_context)
        self.assertEqual(
            event_result,
            {"tools": {"disable": list(extension.AGENT_TOOL_NAMES)}},
        )

        result = await extension.spawn_agent(
            extension.SpawnAgentInput(task="nested task"),
            child_context,
        )
        self.assertIn("only available to the main agent", result["error"])

    async def test_spawn_returns_while_running_and_wait_returns_result(self) -> None:
        FakeClient.block_runs = True
        context = FakeContext("owner-1", self.cwd)

        spawned = await extension.spawn_agent(
            extension.SpawnAgentInput(task="inspect authentication"),
            context,
        )
        agent_id = spawned["data"]["agent_id"]
        self.assertEqual(spawned["data"]["status"], "running")
        self.assertEqual(spawned["data"]["conversation_id"], "child-owner-1-1")
        self.assertEqual(spawned["data"]["context_mode"], "fork")
        self.assertFalse(extension._jobs[agent_id].done.is_set())

        polled = await extension.wait_agent(
            extension.WaitAgentInput(agent_id=agent_id, timeout_ms=0),
            context,
        )
        self.assertEqual(polled["data"]["status"], "running")

        FakeClient.release.set()
        completed = await extension.wait_agent(
            extension.WaitAgentInput(agent_id=agent_id, timeout_ms=1_000),
            context,
        )
        self.assertEqual(completed["content"], "result for inspect authentication")
        self.assertEqual(completed["data"]["status"], "completed")
        self.assertEqual(completed["data"]["result"], completed["content"])
        self.assertTrue(FakeClient.instances[0].closed)

    async def test_list_and_wait_are_scoped_to_the_owner_conversation(self) -> None:
        FakeClient.block_runs = True
        owner = FakeContext("owner-1", self.cwd)
        other = FakeContext("owner-2", self.cwd)

        spawned = await extension.spawn_agent(
            extension.SpawnAgentInput(task="private task"),
            owner,
        )
        agent_id = spawned["data"]["agent_id"]

        owner_list = await extension.list_agents(extension.ListAgentsInput(), owner)
        other_list = await extension.list_agents(extension.ListAgentsInput(), other)
        self.assertEqual(len(owner_list["data"]["agents"]), 1)
        self.assertEqual(other_list["data"]["agents"], [])

        inaccessible = await extension.wait_agent(
            extension.WaitAgentInput(agent_id=agent_id, timeout_ms=0),
            other,
        )
        self.assertEqual(inaccessible["error"], f"agent not found: {agent_id}")

        await extension.cancel_agent(
            extension.CancelAgentInput(agent_id=agent_id),
            owner,
        )

    async def test_cancel_stops_the_child_and_closes_its_client(self) -> None:
        FakeClient.block_runs = True
        context = FakeContext("owner-1", self.cwd)
        spawned = await extension.spawn_agent(
            extension.SpawnAgentInput(task="long running task"),
            context,
        )
        agent_id = spawned["data"]["agent_id"]
        await asyncio.wait_for(FakeClient.started.wait(), timeout=1)

        canceled = await extension.cancel_agent(
            extension.CancelAgentInput(agent_id=agent_id),
            context,
        )
        self.assertEqual(canceled["data"]["status"], "canceled")
        self.assertTrue(extension._jobs[agent_id].done.is_set())
        self.assertTrue(FakeClient.instances[0].closed)

        waited = await extension.wait_agent(
            extension.WaitAgentInput(agent_id=agent_id, timeout_ms=0),
            context,
        )
        self.assertIn("was canceled", waited["content"])

    async def test_limits_each_conversation_to_three_active_agents(self) -> None:
        FakeClient.block_runs = True
        context = FakeContext("owner-1", self.cwd)
        agent_ids: list[str] = []

        for index in range(extension.MAX_ACTIVE_AGENTS_PER_CONVERSATION):
            spawned = await extension.spawn_agent(
                extension.SpawnAgentInput(task=f"task {index}"),
                context,
            )
            agent_ids.append(spawned["data"]["agent_id"])

        rejected = await extension.spawn_agent(
            extension.SpawnAgentInput(task="one task too many"),
            context,
        )
        self.assertIn("maximum of 3 active agents", rejected["error"])
        self.assertEqual(len(context.fork_names), 3)

        for agent_id in agent_ids:
            await extension.cancel_agent(
                extension.CancelAgentInput(agent_id=agent_id),
                context,
            )

    async def test_limits_total_active_agents_across_conversations(self) -> None:
        FakeClient.block_runs = True
        agents: list[tuple[FakeContext, str]] = []

        for index in range(extension.MAX_ACTIVE_AGENTS_TOTAL):
            context = FakeContext(f"owner-{index // 3}", self.cwd)
            spawned = await extension.spawn_agent(
                extension.SpawnAgentInput(task=f"global task {index}"),
                context,
            )
            agents.append((context, spawned["data"]["agent_id"]))

        rejected = await extension.spawn_agent(
            extension.SpawnAgentInput(task="one global task too many"),
            FakeContext("another-owner", self.cwd),
        )
        self.assertIn("maximum of 8 active agents", rejected["error"])

        for context, agent_id in agents:
            await extension.cancel_agent(
                extension.CancelAgentInput(agent_id=agent_id),
                context,
            )

    async def test_canceling_spawn_during_fork_releases_the_reservation(self) -> None:
        context = FakeContext("owner-1", self.cwd, block_fork=True)
        spawn_task = asyncio.create_task(
            extension.spawn_agent(
                extension.SpawnAgentInput(task="blocked fork"),
                context,
            )
        )
        await asyncio.wait_for(context.fork_started.wait(), timeout=1)

        spawn_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await spawn_task

        self.assertEqual(extension._jobs, {})

    async def test_fresh_context_updates_the_conversation_id_after_start(self) -> None:
        context = FakeContext("owner-1", self.cwd)
        spawned = await extension.spawn_agent(
            extension.SpawnAgentInput(task="fresh task", context_mode="fresh"),
            context,
        )
        agent_id = spawned["data"]["agent_id"]
        self.assertIsNone(spawned["data"]["conversation_id"])
        self.assertEqual(spawned["data"]["context_mode"], "fresh")

        completed = await extension.wait_agent(
            extension.WaitAgentInput(agent_id=agent_id, timeout_ms=1_000),
            context,
        )
        self.assertEqual(completed["data"]["conversation_id"], "fresh-1")
        self.assertEqual(completed["data"]["status"], "completed")
        self.assertEqual(context.fork_names, [])

    async def test_fork_mode_does_not_silently_fall_back_to_fresh_context(self) -> None:
        context = FakeContext("owner-1", self.cwd, fork_unavailable=True)
        result = await extension.spawn_agent(
            extension.SpawnAgentInput(task="forked task"),
            context,
        )

        self.assertIn(
            "context_mode='fork' requires live conversation forking", result["error"]
        )
        self.assertEqual(extension._jobs, {})
        self.assertEqual(FakeClient.instances, [])


if __name__ == "__main__":
    unittest.main()
