"""Run: uv run --with 'kodelet-sdk==0.6.1' -- python -m unittest discover -s extensions/caffeinate"""

import asyncio
import importlib.machinery
import importlib.util
import runpy
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

loader = importlib.machinery.SourceFileLoader(
    "caffeinate_extension",
    str(Path(__file__).with_name("kodelet-extension-caffeinate")),
)
spec = importlib.util.spec_from_loader(loader.name, loader)
extension = importlib.util.module_from_spec(spec)
sys.modules[loader.name] = extension
with patch.object(sys, "platform", "darwin"):
    loader.exec_module(extension)


class CaffeinateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        extension._processes.clear()
        self.platform = patch.object(extension.sys, "platform", "darwin")
        self.platform.start()
        self.addCleanup(self.platform.stop)
        self.spawn_patch = patch.object(extension.subprocess, "Popen")
        self.spawn = self.spawn_patch.start()
        self.addCleanup(self.spawn_patch.stop)
        self.processes = []

        def spawn(*args, **kwargs):
            process = MagicMock(pid=100 + len(self.processes))
            process.poll.return_value = None
            self.processes.append(process)
            return process

        self.spawn.side_effect = spawn
        self.metadata = extension.ext.initialize({})
        self.a = {"sessionId": "host", "conversationId": "a"}
        self.b = {"sessionId": "host", "conversationId": "b"}

    async def event(self, name, context=None, payload=None):
        if payload is None and name == "agent.finished":
            payload = {"status": "succeeded"}
        return await extension.ext.handle_event(
            {
                "event": name,
                "context": self.a if context is None else context,
                "payload": payload or {},
            }
        )

    async def test_idle_and_registration(self):
        self.assertEqual(self.metadata["tools"], [])
        self.assertEqual(
            {s["event"] for s in self.metadata["subscriptions"]},
            {"agent.start", "turn.start", "agent.finished", "session.end"},
        )
        await self.event("session.start")
        self.assertFalse(extension._processes)
        self.spawn.assert_not_called()

    async def test_lifecycle_is_idempotent(self):
        await asyncio.gather(self.event("agent.start"), self.event("agent.start"))
        await self.event("turn.start")
        self.spawn.assert_called_once_with(
            ["/usr/bin/caffeinate", "-i", "-w", str(extension.os.getpid())],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.assertIs(extension._processes[("host", "a")], self.processes[0])
        await self.event(
            "agent.finished", payload={"status": "canceled", "reason": "canceled"}
        )
        await self.event("agent.finished")
        self.processes[0].terminate.assert_called_once()
        self.processes[0].wait.assert_called_once_with(timeout=1)
        self.assertFalse(extension._processes)

    async def test_concurrent_conversations_are_independent(self):
        await asyncio.gather(
            self.event("agent.start", self.a), self.event("agent.start", self.b)
        )
        await self.event("agent.finished", self.a)
        self.processes[0].terminate.assert_called_once()
        self.processes[1].terminate.assert_not_called()
        self.assertEqual(extension._processes, {("host", "b"): self.processes[1]})
        await self.event("agent.finished", self.b)
        self.assertFalse(extension._processes)

    async def test_same_conversation_in_distinct_sessions(self):
        other = {"sessionId": "other-host", "conversationId": "a"}
        await self.event("agent.start")
        await self.event("agent.start", other)
        await self.event("agent.finished")
        self.assertEqual(extension._processes, {("other-host", "a"): self.processes[1]})
        self.assertEqual(self.spawn.call_count, 2)

    async def test_followup_keeps_existing_protection(self):
        await self.event("agent.start")
        await self.event("agent.end")
        await self.event("turn.start")
        self.spawn.assert_called_once()
        self.processes[0].terminate.assert_not_called()

    async def test_reload_restores_protection(self):
        await self.event("agent.start")
        await self.event("session.end")
        await self.event("turn.start")
        self.assertEqual(self.spawn.call_count, 2)
        self.assertIs(extension._processes[("host", "a")], self.processes[1])

    async def test_shutdown_releases_all_owned_assertions(self):
        await self.event("agent.start")
        await self.event("agent.start", self.b)
        await self.event("session.end", {})
        for process in self.processes:
            process.terminate.assert_called_once()
        self.assertFalse(extension._processes)

    async def test_dead_process_is_replaced(self):
        await self.event("agent.start")
        self.processes[0].poll.return_value = 1
        await self.event("turn.start")
        self.assertEqual(self.spawn.call_count, 2)
        self.assertIs(extension._processes[("host", "a")], self.processes[1])

    async def test_non_macos_registers_nothing(self):
        for platform in ("linux", "win32"):
            with self.subTest(platform=platform):
                with patch.object(extension.sys, "platform", platform):
                    module = runpy.run_path(loader.path)
                    metadata = module["ext"].initialize({})
                    module["start"]((None, "a"))
                self.assertEqual(metadata["tools"], [])
                self.assertEqual(metadata["subscriptions"], [])
        self.spawn.assert_not_called()

    async def test_spawn_failure_does_not_block_agent(self):
        self.spawn.side_effect = OSError("unavailable")
        await self.event("agent.start")
        self.assertFalse(extension._processes)

    async def test_stop_escalates_only_owned_process(self):
        await self.event("agent.start")
        process = self.processes[0]
        process.wait.side_effect = [subprocess.TimeoutExpired("caffeinate", 1), 0]
        await self.event("agent.finished")
        process.kill.assert_called_once()
        self.assertEqual(process.wait.call_count, 2)
