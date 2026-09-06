# /// script
# requires-python = ">=3.11"
# dependencies = ["kodelet-sdk==0.2.1", "filetype", "google-genai", "pillow"]
# ///

"""Run with `uv run --script tests/test_extensions.py`; no provider calls."""

from __future__ import annotations

import asyncio
import json
import runpy
import sys
import tempfile
import unittest
from importlib.metadata import distribution
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from kodelet_sdk import ChildClient, ToolContext

ROOT = Path(__file__).resolve().parents[1]
EXTENSIONS = ROOT / "extensions"
SEARCH = runpy.run_path(str(EXTENSIONS / "code-search" / "kodelet-extension-code-search"))


class Host:
    """Exercise the real SDK ChildClient against the bounded child RPC contract."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.reading = asyncio.Event()
        self.allow_read = asyncio.Event()
        self.allow_read.set()
        self.start_error: Exception | None = None
        self.initial = {
            "conversationId": "child-conversation",
            "runId": "child-run",
            "done": False,
            "events": [
                {"sequence": 1, "kind": "tool-use", "toolName": "grep_tool", "toolCallId": "a"},
                {"sequence": 2, "kind": "tool-result", "toolName": "grep_tool", "toolCallId": "a"},
            ],
        }
        self.result = {
            **self.initial,
            "done": True,
            "output": "  Found src/handler.go:20-40.\n",
            "events": [{"sequence": 3, "kind": "text-delta", "text": "Found the handler."}],
        }

    async def request(self, method: str, params: Any = None) -> Any:
        self.calls.append((method, params))
        if method == "kodelet.child.start":
            if self.start_error is not None:
                raise self.start_error
            return self.initial
        if method == "kodelet.child.read":
            self.reading.set()
            await self.allow_read.wait()
            return self.result
        if method == "kodelet.child.cancel":
            return {"cancelled": True}
        raise AssertionError(f"unexpected RPC: {method}")


class ExtensionSmokeTests(unittest.IsolatedAsyncioTestCase):
    def test_published_minimum_sdk_is_not_an_editable_checkout(self) -> None:
        package = distribution("kodelet-sdk")
        self.assertEqual(package.version, "0.2.1")
        self.assertIsNone(package.read_text("direct_url.json"))

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
                    self.assertIn("kodelet-sdk>=0.2.1,<0.3", script.read_text())
                    payload = json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "extension.initialize",
                            "params": {
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
                        self.assertEqual(manifest["profiles"][0]["name"], "code_search")
            self.assertEqual(state.read_text(), "unusable client store")
            self.assertFalse((root / "data").exists())

    def test_search_profile_is_strict_read_only_and_snapshotted(self) -> None:
        extension = SEARCH["ext"]
        manifest = extension.initialize({"extension": {"id": "code-search"}})
        profile = manifest["profiles"][0]
        self.assertEqual(
            profile["options"],
            {
                "provider": "openai",
                "model": "gpt-5.6-luna",
                "reasoningEffort": "none",
                "allowedTools": ["file_read", "grep_tool", "glob_tool"],
                "enableFSSearchTools": True,
                "noExtensions": True,
                "noSkills": True,
            },
        )
        self.assertEqual(profile["systemPrompt"], SEARCH["build_sysprompt_text"](3))
        self.assertNotIn("systemPromptPath", profile)
        profile["options"]["allowedTools"].append("bash")
        self.assertNotIn("bash", extension.initialize({})["profiles"][0]["options"]["allowedTools"])


class CodeSearchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.host = Host()
        self.ctx = ToolContext({"extension": {"cwd": str(self.root)}})
        self.ctx.children = ChildClient(self.host)
        self.updates = AsyncMock()
        self.update_patch = patch.object(self.ctx, "update", self.updates)
        self.update_patch.start()
        self.addCleanup(self.update_patch.stop)

    async def search(self, **kwargs: Any) -> dict[str, Any]:
        with (
            patch("subprocess.Popen", side_effect=AssertionError("must not spawn a CLI")),
            patch("tempfile.NamedTemporaryFile", side_effect=AssertionError("no prompt files")),
        ):
            return await SEARCH["code_search"](SEARCH["CodeSearchInput"](**kwargs), self.ctx)

    async def test_child_request_progress_and_authoritative_result(self) -> None:
        (self.root / "src").mkdir()
        result = await self.search(query="  Find the handler.  ", cwd="src", max_turns=5)
        self.assertEqual(result["content"], "Found src/handler.go:20-40.")
        self.assertNotIn("error", result)
        method, request = self.host.calls[0]
        self.assertEqual(method, "kodelet.child.start")
        self.assertEqual(request["message"], "Find the handler.")
        self.assertEqual(request["profile"], "code_search")
        self.assertEqual(request["cwd"], str(self.root / "src"))
        self.assertEqual(request["options"], {"maxTurns": 5})
        self.assertTrue(request["requestId"])
        self.assertEqual(request["systemPrompt"], SEARCH["build_sysprompt_text"](5))
        self.assertEqual(
            set(request), {"profile", "message", "cwd", "options", "requestId", "systemPrompt"}
        )
        self.assertIn(
            (
                "kodelet.child.read",
                {"childId": "child-conversation", "childRunId": "child-run", "after": 2},
            ),
            self.host.calls,
        )
        progress = result["data"]["taskRun"]
        self.assertEqual(progress["status"], "completed")
        self.assertEqual(progress["counts"], {"succeeded": 1, "failed": 0, "running": 0})
        self.assertEqual(progress["activities"][0]["id"], "child-run")
        self.assertEqual(progress["activities"][0]["label"], "Search code: grep_tool")
        phases = [call.args[1]["taskRun"]["phase"] for call in self.updates.call_args_list]
        self.assertIn("working", phases)
        self.assertIn("responding", phases)
        self.assertEqual(list(self.root.iterdir()), [self.root / "src"])

    async def test_default_turn_limit_and_workspace(self) -> None:
        await self.search(query="Find code")
        request = self.host.calls[0][1]
        self.assertEqual(request["options"], {"maxTurns": 3})
        self.assertEqual(request["cwd"], str(self.root))

    async def test_invalid_queries_and_paths_do_not_submit(self) -> None:
        (self.root / "file").write_text("text")
        (self.root / "outside").symlink_to(self.root.parent, target_is_directory=True)
        inputs = [{"query": "   "}] + [
            {"query": "find", "cwd": cwd} for cwd in ("", "missing", "file", "..", "outside")
        ]
        for value in inputs:
            with self.subTest(value=value):
                self.assertIn("error", await self.search(**value))
        self.assertEqual(self.host.calls, [])
        self.updates.assert_not_called()

    async def test_child_failure_does_not_report_partial_output_as_success(self) -> None:
        self.host.result.update(error="provider failed", output="partial answer")
        result = await self.search(query="find")
        self.assertIn("provider failed", result["error"])
        self.assertNotIn("partial answer", result["content"])
        self.assertEqual(result["data"]["taskRun"]["status"], "failed")

    async def test_empty_output_is_an_error(self) -> None:
        self.host.result["output"] = " \n "
        result = await self.search(query="find")
        self.assertEqual(result["error"], "code_search returned an empty response")
        self.assertEqual(result["data"]["taskRun"]["counts"]["failed"], 1)

    async def test_rejected_policy_and_unavailable_host_never_fallback(self) -> None:
        self.host.start_error = RuntimeError("child tools exceed parent policy")
        result = await self.search(query="find")
        self.assertIn("exceed parent policy", result["error"])
        self.assertEqual(len(self.host.calls), 1)
        self.ctx.children = ChildClient(None)
        result = await self.search(query="find")
        self.assertIn("no local fallback", result["error"])

    async def test_timeout_cancels_only_the_submitted_child(self) -> None:
        self.host.allow_read.clear()
        with patch.dict(SEARCH["run_search_agent"].__globals__, AGENT_TIMEOUT_SECONDS=0.1):
            result = await self.search(query="find")
        self.assertIn("timed out", result["error"])
        self.assertEqual(
            self.host.calls[-1],
            ("kodelet.child.cancel", {"childId": "child-conversation", "childRunId": "child-run"}),
        )
        self.assertEqual(result["data"]["taskRun"]["status"], "failed")

    async def test_handler_cancellation_is_not_returned_as_success(self) -> None:
        self.host.allow_read.clear()
        task = asyncio.create_task(self.search(query="find"))
        await asyncio.wait_for(self.host.reading.wait(), timeout=2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(
            self.host.calls[-1],
            ("kodelet.child.cancel", {"childId": "child-conversation", "childRunId": "child-run"}),
        )


if __name__ == "__main__":
    unittest.main()
