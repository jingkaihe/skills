# /// script
# requires-python = ">=3.11"
# dependencies = ["kodelet-sdk==0.5.1", "filetype", "google-genai", "pillow"]
# ///

"""Run with `uv run --script tests/test_extensions.py`; no provider calls.

For an unpublished local SDK: KODELET_TEST_LOCAL_SDK=1 uv run --project
../kodelet-python-sdk --with filetype --with google-genai --with pillow
-- python tests/test_extensions.py
"""

from __future__ import annotations

import asyncio
import json
import os
import runpy
import sys
import tempfile
import unittest
from importlib.metadata import distribution
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import kodelet_sdk
from kodelet_sdk import Client, ToolContext, create_test_harness
from kodelet_sdk.agent.transport import ACP_MESSAGE_LIMIT

ROOT = Path(__file__).resolve().parents[1]
EXTENSIONS = ROOT / "extensions"
SEARCH = runpy.run_path(str(EXTENSIONS / "code-search" / "kodelet-extension-code-search"))


def tool_call(call_id: str, name: str, tool_input: Any) -> dict[str, Any]:
    return {
        "sessionUpdate": "tool_call", "toolCallId": call_id,
        "toolName": name, "rawInput": tool_input,
    }


def tool_result(
    call_id: str, name: str, status: str = "completed", text: str = "",
) -> dict[str, Any]:
    return {
        "sessionUpdate": "tool_call_update", "toolCallId": call_id,
        "toolName": name, "status": status,
        "content": [{"type": "content", "content": {"type": "text", "text": text}}],
    }


def message(text: str) -> dict[str, Any]:
    return {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}}


class ACPProcess:
    """Provider-free line transport; Client, Session, RPC, and progress are real SDK code."""

    def __init__(self) -> None:
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.stdin = self
        self.requests: list[dict[str, Any]] = []
        self.tasks: set[asyncio.Task[None]] = set()
        self.closed = asyncio.Event()
        self.terminating = asyncio.Event()
        self.killed = asyncio.Event()
        self.reaped = asyncio.Event()
        self.ignore_terminate = False
        self.loading = asyncio.Event()
        self.prompt_started = asyncio.Event()
        self.allow_load = asyncio.Event()
        self.allow_load.set()
        self.allow_result = asyncio.Event()
        self.allow_result.set()
        self.init_error: str | None = None
        self.prompt_error: str | None = None
        self.stop_reason = "end_turn"
        self.extension_version = 1
        self.events = [
            tool_call("a", "grep_tool", {"pattern": "parser", "path": "."}),
            tool_result("a", "grep_tool", text="src/handler.go:20: parser"),
            message("  Found src/handler.go:20-40.\n"),
        ]

    def write(self, chunk: bytes) -> None:
        for line in chunk.splitlines():
            request = json.loads(line)
            self.requests.append(request)
            if "id" in request:
                task = asyncio.create_task(self.handle(request))
                self.tasks.add(task)
                task.add_done_callback(self.tasks.discard)

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        self.terminate()

    def terminate(self) -> None:
        self.terminating.set()
        if not self.ignore_terminate:
            self._exit()

    def _exit(self) -> None:
        if not self.closed.is_set():
            self.closed.set()
            self.stdout.feed_eof()
            self.stderr.feed_eof()
            for task in self.tasks:
                task.cancel()

    def kill(self) -> None:
        self.killed.set()
        self._exit()

    async def wait(self) -> int:
        await self.closed.wait()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.reaped.set()
        return 0

    def send(self, value: dict[str, Any]) -> None:
        self.stdout.feed_data((json.dumps({"jsonrpc": "2.0", **value}) + "\n").encode())

    async def handle(self, request: dict[str, Any]) -> None:
        method = request["method"]
        error: str | None = None
        if method == "initialize":
            result = {"protocolVersion": 1, "_meta": {
                "sessionExtensions": {"version": self.extension_version},
            }}
        elif method == "session/new":
            self.loading.set()
            await self.allow_load.wait()
            error = self.init_error
            result = {"sessionId": "search-conversation"}
        elif method == "session/prompt":
            self.prompt_started.set()
            for event in self.events:
                self.send({"method": "session/update", "params": {
                    "sessionId": "search-conversation", "update": event,
                }})
                # Let the real reader and TaskProgress publish intermediate snapshots.
                await asyncio.sleep(0)
                await asyncio.sleep(0)
            await self.allow_result.wait()
            error = self.prompt_error
            result = {"stopReason": self.stop_reason}
        else:
            error = f"Unexpected ACP method: {method}"
            result = {}
        self.send({"id": request["id"], **(
            {"error": {"code": -1, "message": error}} if error else {"result": result}
        )})


class ExtensionSmokeTests(unittest.IsolatedAsyncioTestCase):
    def test_nano_banana_uses_image_model_default_and_override(self) -> None:
        module = runpy.run_path(str(EXTENSIONS / "nano-banana" / "kodelet-extension-nano-banana"))
        generate = module["generate_image"]
        for model, expected in [(None, "gemini-3.1-flash-image"), (" custom-model ", "custom-model")]:
            with self.subTest(model=model):
                client = Mock()
                output_path = Path("/runner/cache/generated.png")
                save_image = Mock(return_value=output_path)
                with patch.dict(generate.__globals__, {
                    "build_client": Mock(return_value=client), "save_generated_image": save_image,
                }):
                    result = generate(module["NanoBananaInput"](prompt=" A drawing ", model=model))
                self.assertEqual(result, output_path)
                client.models.generate_content.assert_called_once()
                self.assertEqual(client.models.generate_content.call_args.kwargs["model"], expected)
                self.assertEqual(client.models.generate_content.call_args.kwargs["contents"], "A drawing")
                save_image.assert_called_once_with(client.models.generate_content.return_value, "A drawing")

    async def test_nano_banana_declares_image_attachment(self) -> None:
        module = runpy.run_path(str(EXTENSIONS / "nano-banana" / "kodelet-extension-nano-banana"))
        handler = module["nano_banana"]
        output_path = Path("/runner/cache/generated.png")
        with patch.dict(handler.__globals__, {"generate_image": lambda _input: output_path}):
            harness = await create_test_harness(module["ext"])
            result = await harness.execute_tool({"name": "nano_banana", "input": {"prompt": " A drawing "}})
            long_prompt = "  " + "\U0001f3a8" * 1500 + "  "
            long_result = await harness.execute_tool({"name": "nano_banana", "input": {"prompt": long_prompt}})
        self.assertEqual(result["attachments"], [{
            "type": "image", "path": str(output_path), "mimeType": "image/png", "alt": "A drawing",
        }])
        self.assertEqual(result["data"], {"success": True})
        self.assertEqual(result["content"], "Generated image.")
        self.assertNotIn(str(output_path), json.dumps({
            key: value for key, value in result.items() if key != "attachments"
        }), "The runner path must only appear in the host-ingested attachment")
        self.assertEqual(long_result["attachments"], [{
            "type": "image", "path": str(output_path), "mimeType": "image/png",
            "alt": long_prompt.strip()[:1000],
        }])
        self.assertEqual(len(long_result["attachments"][0]["alt"]), 1000)
        self.assertLess(len(long_result["attachments"][0]["alt"].encode("utf-8")), 4096)
        self.assertEqual(long_result["data"], {"success": True})
        self.assertEqual(long_result["content"], result["content"])

    def test_sdk_distribution_and_transport_support(self) -> None:
        package = distribution("kodelet-sdk")
        direct_url = package.read_text("direct_url.json")
        self.assertEqual(package.version, "0.5.1")
        if os.environ.get("KODELET_TEST_LOCAL_SDK") == "1":
            self.assertIsNotNone(direct_url)
            self.assertTrue(json.loads(direct_url)["dir_info"]["editable"])
            self.assertEqual(
                Path(kodelet_sdk.__file__).resolve(),
                ROOT.parent / "kodelet-python-sdk" / "src" / "kodelet_sdk" / "__init__.py",
            )
            self.assertFalse(hasattr(ToolContext(None), "children"))
        else:
            self.assertIsNone(direct_url, "Default run must test the published minimum wheel")
        self.assertEqual(ACP_MESSAGE_LIMIT, 64 * 1024 * 1024)

    async def test_all_extensions_initialize_over_real_stdio(self) -> None:
        expected = {
            "code-search": ["code_search"],
            "last-word": [],
            "look-at": ["look_at"],
            "nano-banana": ["nano_banana"],
            "todo": ["todo_read", "todo_write"],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "not-a-state-directory"
            state.write_text("unusable client store", encoding="utf-8")
            # No CLI executable or client/provider credentials are needed to register.
            env = {"HOME": directory, "PATH": "", "KODELET_HOME": str(state)}
            for name, tools in expected.items():
                with self.subTest(extension=name):
                    script = EXTENSIONS / name / f"kodelet-extension-{name}"
                    self.assertIn("kodelet-sdk>=0.5.1,<0.6", script.read_text())
                    payload = json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "extension.initialize",
                            "params": {
                                "capabilities": {"profiles": {"remote": True}},
                                "extension": {
                                    "id": name,
                                    "cwd": directory,
                                    "dataDir": str(root / "data"),
                                }
                            },
                        }
                    ).encode()
                    request = f"Content-Length: {len(payload)}\r\n\r\n".encode() + payload
                    process = await asyncio.create_subprocess_exec(
                        sys.executable,
                        str(script),
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        cwd=directory,
                        env=env,
                    )
                    assert process.stdin is not None and process.stdout is not None
                    try:
                        process.stdin.write(request)
                        await process.stdin.drain()
                        # Keep stdin open until initialization is acknowledged:
                        # EOF correctly cancels unfinished SDK request handlers.
                        async with asyncio.timeout(15):
                            header = await process.stdout.readuntil(b"\r\n\r\n")
                            length = int(header.decode().split(":", 1)[1].strip())
                            response = json.loads(await process.stdout.readexactly(length))
                    finally:
                        process.stdin.close()
                        try:
                            _, stderr = await asyncio.wait_for(process.communicate(), timeout=5)
                        except TimeoutError:
                            process.kill()
                            await process.communicate()
                            raise
                    self.assertEqual(process.returncode, 0, stderr.decode())
                    self.assertNotIn("error", response)
                    manifest = response["result"]
                    self.assertEqual(manifest["name"], name)
                    self.assertEqual([tool["name"] for tool in manifest["tools"]], tools)
                    if name == "last-word":
                        self.assertEqual(manifest["commands"][0]["name"], "last-word")
                        self.assertEqual(manifest["shortcuts"][0]["key"], "ctrl+alt+w")
                    if name == "code-search":
                        self.assertEqual(manifest["profiles"], [{
                            "name": "code-search", "hidden": True,
                            "options": {
                                "provider": "openai",
                                "model": "gpt-5.6-luna",
                                "reasoning_effort": "none",
                                "openai": {
                                    "api_mode": "responses",
                                    "platform": "codex",
                                    "service_tier": "fast",
                                },
                                "allowed_tools": ["file_read", "grep_tool", "glob_tool"],
                                "enable_fs_search_tools": True,
                                "skills": {"enabled": False},
                            },
                        }])
            self.assertEqual(state.read_text(), "unusable client store")
            self.assertFalse((root / "data").exists())


class CodeSearchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.peer = ACPProcess()
        self.clients: list[Client] = []
        self.launches: list[dict[str, Any]] = []
        self.session_options: list[dict[str, Any]] = []
        self.closed_clients: list[Client] = []
        self.close_started = asyncio.Event()
        self.responding = asyncio.Event()
        self.ctx = ToolContext({"extension": {"cwd": str(self.root), "runnerId": "search-runner"}})
        # The published SDK still has this namespace; the restored extension must not use it.
        if hasattr(self.ctx, "children"):
            delattr(self.ctx, "children")

        async def update(_content: str, data: dict[str, Any]) -> None:
            if data["taskRun"]["phase"] == "responding":
                self.responding.set()

        self.updates = AsyncMock(side_effect=update)
        self.update_patch = patch.object(self.ctx, "update", self.updates)
        self.update_patch.start()
        self.addCleanup(self.update_patch.stop)
        owner = self

        class RecordingClient(Client):
            def __init__(self, **kwargs: Any) -> None:
                owner.clients.append(self)
                owner.client_kwargs = kwargs
                super().__init__(spawn=owner.spawn, **kwargs)

            async def create_session(self, **kwargs: Any) -> Any:
                owner.session_options.append(kwargs)
                return await super().create_session(**kwargs)

            async def close(self) -> None:
                owner.close_started.set()
                try:
                    await super().close()
                finally:
                    owner.closed_clients.append(self)

        self.client_patch = patch.dict(
            SEARCH["run_search_agent"].__globals__, Client=RecordingClient,
        )
        self.client_patch.start()
        self.addCleanup(self.client_patch.stop)

    def spawn(self, command: str, args: Any, options: Any) -> ACPProcess:
        self.launches.append({"command": command, "args": list(args), "options": options})
        return self.peer

    async def asyncTearDown(self) -> None:
        # Every submitted search must own cleanup, including failed initialization.
        for client in self.clients:
            self.assertIn(client, self.closed_clients)
            self.assertFalse(client._sessions)
            self.assertFalse(client._rpcs)
        if self.clients:
            self.assertTrue(self.peer.closed.is_set())
        self.peer.terminate()
        await self.peer.wait()

    async def search(self, **kwargs: Any) -> dict[str, Any]:
        with patch("tempfile.NamedTemporaryFile", side_effect=AssertionError("no prompt files")):
            return await SEARCH["code_search"](SEARCH["CodeSearchInput"](**kwargs), self.ctx)

    async def prompt_patch(self, max_turns: int) -> dict[str, Any]:
        extension = self.session_options[-1]["extensions"][0]
        manifest = extension.initialize({})
        self.assertEqual(manifest["tools"], [])
        self.assertEqual(manifest["commands"], [])
        self.assertEqual([row["event"] for row in manifest["subscriptions"]], ["agent.init"])
        harness = await create_test_harness(extension)
        result = await harness.handle_event({
            "id": "init", "event": "agent.init",
            "payload": {"systemPrompt": "Runner instructions and tool guidance"},
        })
        self.assertEqual(
            result, {"systemPrompt": {"append": SEARCH["build_sysprompt_text"](max_turns)}},
        )
        return result

    async def test_acp_profile_prompt_hook_progress_and_result(self) -> None:
        (self.root / "src").mkdir()
        result = await self.search(query="  Find the handler.  ", cwd="src", max_turns=5)
        self.assertEqual(result["content"], "Found src/handler.go:20-40.")
        self.assertNotIn("error", result)
        self.assertEqual(
            self.client_kwargs, {"cwd": str(self.root / "src"), "runner": "search-runner"},
        )
        self.assertFalse(hasattr(self.ctx, "children"))
        self.assertIsNone(self.ctx._host_rpc_client)
        self.assertEqual(self.launches[0]["command"], "kodelet")
        self.assertEqual(self.launches[0]["args"], [
            "acp", "--runner", "search-runner", "--profile=code-search",
        ])
        # No noExtensions flag: the inline prompt hook must remain attached.
        self.assertEqual(set(self.session_options[0]), {"profile", "extensions"})
        self.assertEqual(self.session_options[0]["profile"], "code-search")
        await self.prompt_patch(5)
        new = next(row for row in self.peer.requests if row.get("method") == "session/new")
        self.assertEqual(new["params"], {
            "cwd": str(self.root / "src"),
            "_meta": {"sessionExtensions": {"version": 1, "extensionIds": ["inline-1"]}},
        })
        prompt = next(row for row in self.peer.requests if row.get("method") == "session/prompt")
        self.assertEqual(
            prompt["params"]["prompt"], [{"type": "text", "text": "Find the handler."}],
        )
        progress = result["data"]["taskRun"]
        self.assertEqual(progress["status"], "completed")
        self.assertEqual(progress["counts"], {"succeeded": 1, "failed": 0, "running": 0})
        self.assertEqual(progress["activities"][0]["label"], 'Search "parser" in .')
        phases = [call.args[1]["taskRun"]["phase"] for call in self.updates.call_args_list]
        self.assertIn("working", phases)
        self.assertIn("responding", phases)
        self.assertEqual(list(self.root.iterdir()), [self.root / "src"])

    async def test_dedicated_profile_overrides_parent_and_environment_selection(self) -> None:
        with patch.object(self.ctx, "profile", "team-research"), patch.dict(
            os.environ, {"KODELET_PROFILE": "host-other"},
        ):
            result = await self.search(query="Find code")
        self.assertNotIn("error", result)
        self.assertEqual(self.session_options[0]["profile"], "code-search")
        self.assertEqual(
            [arg for arg in self.launches[0]["args"] if arg.startswith("--profile=")],
            ["--profile=code-search"],
        )
        self.assertEqual(self.launches[0]["options"]["env"]["KODELET_PROFILE"], "host-other")
        self.assertFalse(
            any(arg.startswith("--reasoning-effort") for arg in self.launches[0]["args"]),
        )

    async def test_absent_parent_profile_still_selects_registered_search_profile(self) -> None:
        with patch.dict(os.environ, {"KODELET_PROFILE": "host-other"}):
            result = await self.search(query="Find code")
        self.assertNotIn("error", result)
        self.assertEqual(self.session_options[0]["profile"], "code-search")
        self.assertIn("--profile=code-search", self.launches[0]["args"])

    async def test_missing_runner_context_does_not_use_default_runner(self) -> None:
        with patch.object(self.ctx, "runner_id", None):
            result = await self.search(query="Find code")
        self.assertIn("requires a runner-backed extension context", result["error"])
        self.assertFalse(self.launches)

    async def test_parallel_tool_results_and_errors_before_summary_finishes(self) -> None:
        self.peer.events = [
            tool_call("a", "grep_tool", {"pattern": "parser", "path": "."}),
            tool_call("b", "glob_tool", {"pattern": "*.go", "path": "."}),
            tool_call("c", "file_read", {"file_path": "src/handler.go"}),
            tool_result("a", "grep_tool", "in_progress", "partial matches"),
            tool_result("c", "file_read"),
            tool_result("a", "grep_tool"),
            tool_result("b", "glob_tool", "failed", "permission denied"),
            message("Writing the answer"),
        ]
        self.peer.allow_result.clear()
        task = asyncio.create_task(self.search(query="Find code"))
        try:
            await asyncio.wait_for(self.responding.wait(), timeout=2)
            snapshots = [call.args[1]["taskRun"] for call in self.updates.call_args_list]
            self.assertTrue(any(snapshot["counts"]["running"] == 3 for snapshot in snapshots))
            summary = snapshots[-1]
            self.assertEqual(summary["status"], "running", "the answer is still in flight")
            self.assertEqual(summary["phase"], "responding")
            self.assertEqual(summary["detail"], "writing summary")
            self.assertEqual(summary["counts"], {"succeeded": 2, "failed": 1, "running": 0})
            self.assertEqual(
                [(row["label"], row["status"]) for row in summary["activities"]],
                [('Search "parser" in .', "succeeded"),
                 ('Find files "*.go" in .', "failed"), ("Read src/handler.go", "succeeded")],
            )
            self.assertEqual(summary["activities"][1]["preview"], "permission denied")
            self.assertNotIn("preview", summary["activities"][0])
            self.assertFalse(task.done())
        finally:
            self.peer.allow_result.set()
            result = await task
        self.assertEqual(result["data"]["taskRun"]["counts"], summary["counts"])

    async def test_truncated_tool_input_and_result_without_start_are_safe(self) -> None:
        self.peer.events[0]["rawInput"] = '{"pattern":"truncated'
        self.peer.events.insert(2, tool_result("lost", "file_read", "failed", "file missing"))
        result = await self.search(query="Find code")
        progress = result["data"]["taskRun"]
        # TaskProgress's ACP adapter ignores results with no observed start.
        self.assertEqual(progress["counts"], {"succeeded": 1, "failed": 0, "running": 0})
        self.assertEqual(progress["activities"][0]["label"], "Search in .")

    async def test_default_turn_budget_is_advisory_and_workspace_is_inherited(self) -> None:
        await self.search(query="Find code")
        self.assertEqual(self.client_kwargs, {"cwd": str(self.root), "runner": "search-runner"})
        self.assertNotIn("max_turns", self.session_options[0])
        self.assertFalse(any(arg.startswith("--max-turns") for arg in self.launches[0]["args"]))
        await self.prompt_patch(3)

    async def test_invalid_queries_and_paths_do_not_start_acp(self) -> None:
        (self.root / "file").write_text("text")
        inputs = [{"query": "   "}] + [
            {"query": "find", "cwd": cwd} for cwd in ("", "missing", "file")
        ]
        for value in inputs:
            with self.subTest(value=value):
                self.assertIn("error", await self.search(**value))
        self.assertEqual(self.clients, [])
        self.assertEqual(self.launches, [])
        self.updates.assert_not_called()

    async def test_relative_absolute_and_symlink_cwd_outside_workspace_reach_runner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            outside = Path(directory).resolve()
            (self.root / "outside").symlink_to(outside, target_is_directory=True)
            for cwd in (str(outside), os.path.relpath(outside, self.root), "outside", ".."):
                with self.subTest(cwd=cwd):
                    self.peer = ACPProcess()
                    result = await self.search(query="find", cwd=cwd)
                    self.assertNotIn("error", result)
                    expected = self.root.parent if cwd == ".." else outside
                    new = next(
                        row for row in self.peer.requests if row.get("method") == "session/new"
                    )
                    self.assertEqual(new["params"]["cwd"], str(expected))
                    self.assertTrue(self.peer.closed.is_set())
            self.assertEqual(list(outside.iterdir()), [])

    async def test_provider_failure_does_not_return_partial_output_as_success(self) -> None:
        self.peer.events.append(message("partial answer"))
        self.peer.prompt_error = "provider failed"
        result = await self.search(query="find")
        self.assertIn("provider failed", result["error"])
        self.assertNotIn("partial answer", result["content"])
        self.assertEqual(result["data"]["taskRun"]["status"], "failed")

    async def test_daemon_cancellation_does_not_return_partial_output_as_success(self) -> None:
        self.peer.events = [message("partial answer")]
        self.peer.stop_reason = "cancelled"
        result = await self.search(query="find")
        self.assertEqual(result["error"], "code_search failed: code_search was canceled")
        self.assertNotIn("partial answer", result["content"])
        self.assertEqual(result["data"]["taskRun"]["status"], "failed")

    async def test_empty_or_missing_output_is_an_error(self) -> None:
        for content in ("", " \n ", None):
            with self.subTest(content=content):
                self.peer = ACPProcess()
                self.peer.events = self.peer.events[:2]
                if content is not None:
                    self.peer.events.append(message(content))
                result = await self.search(query="find")
                self.assertEqual(result["error"], "code_search returned an empty response")
                self.assertEqual(result["data"]["taskRun"]["status"], "failed")
                self.assertEqual(result["data"]["taskRun"]["counts"], {
                    "succeeded": 1, "failed": 0, "running": 0,
                })
                self.assertTrue(self.peer.closed.is_set())

    async def test_failed_session_initialization_closes_client(self) -> None:
        self.peer.init_error = "runner rejected workspace"
        result = await self.search(query="find")
        self.assertIn("runner rejected workspace", result["error"])
        self.assertEqual(result["data"]["taskRun"]["status"], "failed")
        self.assertFalse(self.peer.prompt_started.is_set())
        self.assertEqual(len(self.launches), 1, "No fallback or retry")

    async def test_missing_inline_capability_fails_without_fallback(self) -> None:
        self.peer.extension_version = 0
        result = await self.search(query="find")
        self.assertIn("sessionExtensions", result["error"])
        self.assertFalse(self.peer.loading.is_set())
        self.assertEqual(len(self.launches), 1)

    async def test_timeout_closes_client_and_cancels_active_session(self) -> None:
        self.peer.allow_result.clear()
        with patch.dict(SEARCH["run_search_agent"].__globals__, AGENT_TIMEOUT_SECONDS=0.1):
            result = await self.search(query="find")
        self.assertIn("timed out", result["error"])
        self.assertEqual(result["data"]["taskRun"]["status"], "failed")
        cancel = next(row for row in self.peer.requests if row.get("method") == "session/cancel")
        self.assertEqual(cancel["params"], {"sessionId": "search-conversation"})

    async def test_session_initialization_timeout_closes_client(self) -> None:
        self.peer.allow_load.clear()
        with patch.dict(SEARCH["run_search_agent"].__globals__, AGENT_TIMEOUT_SECONDS=0.1):
            result = await self.search(query="find")
        self.assertIn("timed out", result["error"])
        self.assertTrue(self.peer.loading.is_set())
        self.assertFalse(self.peer.prompt_started.is_set())

    async def test_handler_cancellation_is_propagated_and_closes_client(self) -> None:
        self.peer.allow_result.clear()
        task = asyncio.create_task(self.search(query="find"))
        await asyncio.wait_for(self.peer.prompt_started.wait(), timeout=2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(self.peer.closed.is_set())

    async def test_cancellation_during_session_initialization_closes_client(self) -> None:
        self.peer.allow_load.clear()
        task = asyncio.create_task(self.search(query="find"))
        await asyncio.wait_for(self.peer.loading.wait(), timeout=2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(self.peer.prompt_started.is_set())
        self.assertTrue(self.peer.closed.is_set())

    async def assert_repeated_cancellation_waits_for_cleanup(
        self, task: asyncio.Task[dict[str, Any]],
    ) -> None:
        await asyncio.wait_for(self.close_started.wait(), timeout=2)
        for _ in range(3):
            task.cancel()
            await asyncio.sleep(0)
            self.assertFalse(task.done(), "cancellation must wait for the owned client close")
        self.assertFalse(self.peer.closed.is_set(), "SIGTERM was intentionally ignored")
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(task), timeout=3)
        self.assertTrue(self.peer.killed.is_set(), "the SDK must escalate to SIGKILL")
        self.assertTrue(self.peer.reaped.is_set(), "the SDK must await process exit")
        self.assertEqual(self.closed_clients, self.clients, "close runs once per client")
        self.assertFalse(self.clients[0]._sessions)
        self.assertFalse(self.clients[0]._rpcs)
        self.assertTrue(task.cancelled(), "cleanup must not turn cancellation into success")

    async def test_repeated_cancellation_during_prompt_waits_for_escalation_and_reaping(
        self,
    ) -> None:
        self.peer.ignore_terminate = True
        self.peer.allow_result.clear()
        task = asyncio.create_task(self.search(query="find"))
        try:
            await asyncio.wait_for(self.peer.prompt_started.wait(), timeout=2)
            task.cancel()
            await asyncio.wait_for(self.peer.terminating.wait(), timeout=2)
            await self.assert_repeated_cancellation_waits_for_cleanup(task)
        finally:
            self.peer.kill()
            await asyncio.gather(task, return_exceptions=True)

    async def test_repeated_cancellation_during_startup_waits_for_escalation_and_reaping(
        self,
    ) -> None:
        self.peer.ignore_terminate = True
        self.peer.allow_load.clear()
        task = asyncio.create_task(self.search(query="find"))
        try:
            await asyncio.wait_for(self.peer.loading.wait(), timeout=2)
            task.cancel()
            await asyncio.wait_for(self.peer.terminating.wait(), timeout=2)
            # Interrupt create_session's own cleanup before the outer client-close barrier.
            task.cancel()
            await self.assert_repeated_cancellation_waits_for_cleanup(task)
            self.assertFalse(self.peer.prompt_started.is_set())
        finally:
            self.peer.kill()
            await asyncio.gather(task, return_exceptions=True)

    async def test_cancellation_during_success_cleanup_does_not_return_success(self) -> None:
        self.peer.ignore_terminate = True
        task = asyncio.create_task(self.search(query="find"))
        try:
            await asyncio.wait_for(self.peer.terminating.wait(), timeout=2)
            await self.assert_repeated_cancellation_waits_for_cleanup(task)
        finally:
            self.peer.kill()
            await asyncio.gather(task, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
