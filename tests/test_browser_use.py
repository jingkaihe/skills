# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "kodelet-sdk==0.5.5",
#   "typesafe-sdk==0.7.1",
#   "playwright==1.63.0",
# ]
# ///

"""Run with `uv run --script tests/test_browser_use.py`; no provider calls.

SDK requests use httpx2.MockTransport. Chromium tests launch a temporary,
headless profile, block external requests, and never use the host's browser.
Only that optional class skips when /usr/bin/chromium-browser is unavailable.
"""

from __future__ import annotations

import asyncio
import copy
import inspect
import itertools
import json
import logging
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx2
from kodelet_sdk import create_test_harness
from playwright.async_api import async_playwright
from pydantic import ValidationError
from typesafe_sdk import AsyncTypeSafeClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "extensions" / "browser-use"))
import browser_dom
import browser_goal
import browser_use

ACCOUNT = {
    "ref": "e1",
    "role": "button",
    "name": "Settings",
    "context": "Account menu",
    "editable": False,
    "enabled": True,
}
PROJECT = {**ACCOUNT, "ref": "e2", "context": "Project: kodelet"}
GOAL = {
    "action": "run",
    "goal": "Open the current project's settings",
    "successCriteria": "The Project settings heading is visible",
}


def choice(selected, criteria, confidence=0.95, probabilities=None):
    return {
        "type": "choice",
        "choice": selected,
        "confidence": confidence,
        "probabilities": probabilities
        if probabilities is not None
        else {key: int(key == selected) for key in criteria},
    }


def request_body(kwargs):
    """Capture the same JSON-shaped state/questions as the real SDK transport."""
    return copy.deepcopy(
        {
            "model": kwargs["model"],
            "state": kwargs["state"],
            "questions": {
                name: question.model_dump(mode="json")
                for name, question in kwargs["questions"].items()
            },
        }
    )


def target_reply(body, ref="e2", confidence=0.95):
    return {"target": choice(ref, body["questions"]["target"]["criteria"], confidence)}


def goal_reply(body, status="act", action="none", blocker="uncertain", evidence=1):
    selections = {"status": status, "nextAction": action, "blocker": blocker}
    return {
        name: {"type": "noul", "noul": evidence}
        if name == "evidence"
        else choice(
            selections[name],
            question["criteria"],
        )
        for name, question in body["questions"].items()
    }


def action_id(body, operation, name=None, input_ref=None, **fields):
    for key, candidate in body["questions"]["nextAction"]["criteria"].items():
        if not isinstance(candidate, dict) or candidate.get("operation") != operation:
            continue
        if name is not None and candidate.get("element", {}).get("name") != name:
            continue
        if input_ref is not None and candidate.get("inputRef") != input_ref:
            continue
        if all(candidate.get(key) == value for key, value in fields.items()):
            return key
    raise AssertionError(f"Missing {operation} action for {name!r}: {body}")


def span_reply(body, start, end, confidence=0.95):
    return {
        name: choice(selected, body["questions"][name]["criteria"], confidence)
        for name, selected in (("start", start), ("end", end))
    }


class FakeClient:
    def __init__(self, respond=target_reply):
        self.respond = respond
        self.calls = []
        self.aclose = AsyncMock()

    async def system_one(self, **kwargs):
        body = request_body(kwargs)
        self.calls.append(body)
        answers = self.respond(body)
        if inspect.isawaitable(answers):
            answers = await answers
        return SimpleNamespace(answers=answers)


class Control:
    """Mutable fake page state, separate from each retained ElementHandle."""

    def __init__(
        self,
        name="Settings",
        *,
        context="",
        editable=False,
        enabled=True,
        tag=None,
        kind="",
        role=None,
        value="",
        identity="",
    ):
        self.description = {
            "role": role or ("textbox" if editable else "button"),
            "name": name,
            "context": context,
            "editable": editable,
            "enabled": enabled,
            "identity": {
                "tag": tag or ("input" if editable else "button"),
                "id": identity,
                "type": kind,
                "href": None,
                "fieldName": None,
            },
        }
        self.value = value
        self.visible = True
        self.attached = True
        self.invalid = False
        self.after_action = None


class FakeHandle:
    def __init__(self, page, control):
        self.page = page
        self.control = control
        self.disposals = 0

    async def evaluate(self, expression, argument=None):
        if expression == browser_dom._FIELD_FACTS:
            return {
                "value": self.control.value,
                "invalid": self.control.invalid,
                "matches": [
                    name for name, value in argument.items() if value == self.control.value
                ],
            }
        if expression != browser_dom._DESCRIBE_ELEMENT:
            raise AssertionError("Unexpected fake DOM evaluation")
        if not self.control.attached:
            return None
        description = copy.deepcopy(self.control.description)
        for key in ("role", "name", "context"):
            description[key] = browser_dom.redact_input_values(description[key], argument or {})
        return description

    async def is_visible(self):
        return self.control.visible and self.control.attached

    async def is_enabled(self):
        return self.control.description["enabled"]

    async def is_editable(self):
        return self.control.description["editable"]

    async def dispose(self):
        self.disposals += 1

    async def dispatch(self, operation, **extra):
        self.page.dispatched.append(
            {
                "operation": operation,
                "name": self.control.description["name"],
                **extra,
            }
        )
        if self.control.after_action:
            pending = self.control.after_action(operation)
            if inspect.isawaitable(pending):
                await pending

    async def click(self, **kwargs):
        await self.dispatch("click")

    async def fill(self, value, **kwargs):
        self.control.value = value
        await self.dispatch("fill", value=value)

    async def press(self, key, **kwargs):
        await self.dispatch("press", key=key)


class FakeLocator:
    def __init__(self, page, controls):
        self.page, self.controls = page, controls

    async def count(self):
        return len(self.controls)

    def nth(self, index):
        async def element_handle(**kwargs):
            handle = FakeHandle(self.page, self.controls[index])
            self.page.handles.append(handle)
            return handle

        return SimpleNamespace(element_handle=element_handle)

    async def evaluate_all(self, expression):
        if expression != browser_dom._FIELD_VALUES:
            raise AssertionError("Unexpected fake form evaluation")
        if len(self.controls) > 512:
            return None
        return json.dumps(
            [
                [control.description["identity"]["id"], None, control.value]
                for control in self.controls
            ]
        )


class FakePage:
    def __init__(self, target_id="shared-page"):
        self.target_id = target_id
        self.url = "http://localhost/private-fixture"
        self.closed = False
        self.controls = [
            Control(context="Account menu"),
            Control(context="Project: kodelet"),
        ]
        self.evidence = [Control("Project settings", role="heading")]
        self.handles = []
        self.dispatched = []
        self.listeners = {}
        self.cdp = SimpleNamespace(
            send=AsyncMock(return_value={"targetInfo": {"targetId": target_id}}),
            detach=AsyncMock(),
        )
        self.context = SimpleNamespace(new_cdp_session=AsyncMock(return_value=self.cdp))
        self.set_default_timeout = Mock()
        self.set_default_navigation_timeout = Mock()
        self.goto = AsyncMock()
        self.reload = AsyncMock()
        self.go_back = AsyncMock()
        self.wait_for_load_state = AsyncMock()

    async def title(self):
        return "Fixture"

    def is_closed(self):
        return self.closed

    def on(self, event, callback):
        self.listeners.setdefault(event, []).append(callback)

    def remove_listener(self, event, callback):
        self.listeners[event].remove(callback)

    def navigate(self):
        for callback in self.listeners.get("framenavigated", [])[:]:
            callback(self)

    def locator(self, selector):
        if selector == browser_dom._CANDIDATE_SELECTOR:
            controls = [c for c in self.controls if c.attached and c.visible]
        elif selector.startswith("h1,"):
            controls = self.evidence
        elif selector == "input:not([type=hidden]),textarea,[contenteditable=true]":
            controls = [c for c in self.controls if c.description["editable"]]
        else:
            raise AssertionError(f"Unexpected selector: {selector}")
        return FakeLocator(self, controls)


class Harness:
    sessions = itertools.count()

    def __init__(self, respond=target_reply, key="test-key"):
        self.page = FakePage()
        self.other = FakePage("unrelated-page")
        self.browser = SimpleNamespace(
            contexts=[SimpleNamespace(pages=[self.other, self.page])],
            close=AsyncMock(),
        )
        self.lease = SimpleNamespace(
            session_id=f"test-session-{next(self.sessions)}",
            lease_id="test-lease",
            cdp_url="ws://127.0.0.1:1/devtools/browser/private",
            page_target_id="shared-page",
            release=AsyncMock(),
        )
        self.client = FakeClient(respond)
        self.factory = Mock(return_value=self.client)
        self.ctx = SimpleNamespace(
            env={"TYPESAFE_API_KEY": key},
            browser=SimpleNamespace(acquire=AsyncMock(return_value=self.lease)),
            update=AsyncMock(),
        )

        async def connect(url, *, timeout, no_defaults):
            assert url == self.lease.cdp_url
            assert no_defaults is True
            assert 0 < timeout <= browser_dom.ACTION_TIMEOUT
            return self.browser

        self.connect = AsyncMock(side_effect=connect)

    async def execute(self, input_dict=None):
        return await browser_use.execute_browser_use(
            input_dict or GOAL,
            self.ctx,
            connect=self.connect,
            client_factory=self.factory,
        )


class BrowserTestCase(unittest.IsolatedAsyncioTestCase):
    def assert_cleaned(self, h, *, semantic=True):
        h.lease.release.assert_awaited_once()
        h.browser.close.assert_awaited_once()
        if semantic:
            h.client.aclose.assert_awaited_once()
        else:
            h.factory.assert_not_called()
        self.assertFalse(h.page.closed)
        self.assertFalse(h.page.listeners.get("framenavigated"))
        self.assertTrue(all(handle.disposals == 1 for handle in h.page.handles))
        self.assertNotIn(h.lease.session_id, browser_use._active_sessions)

    def assert_private(self, h, output, *secrets):
        captured = json.dumps([h.client.calls, output, h.ctx.update.await_args_list], default=str)
        for secret in (
            "test-key",
            "devtools/browser",
            "private-fixture",
            "localValuesDigest",
            "value_digest",
            "valueDigest",
            *secrets,
        ):
            self.assertNotIn(secret, captured)

    def sdk_client(self, handler):
        # Exercise the production factory/retry configuration, not a lookalike.
        logger = logging.getLogger("typesafe_sdk")
        self.addCleanup(setattr, logger, "disabled", logger.disabled)
        with patch.object(
            browser_use,
            "AsyncTypeSafeClient",
            side_effect=lambda **kwargs: AsyncTypeSafeClient(
                **kwargs, transport=httpx2.MockTransport(handler)
            ),
        ):
            client = browser_use.jev_client("test-key")
        self.addAsyncCleanup(client.aclose)
        return client


class SDKSelectionTests(BrowserTestCase):
    async def test_real_sdk_minimizes_payload_and_uses_authentication(self):
        requests = []

        def respond(request):
            requests.append(request)
            return httpx2.Response(
                200,
                json={
                    "answers": {
                        "target": choice(
                            "e2",
                            ("e1", "e2", "none"),
                            probabilities={"e1": 0.02, "e2": 0.96, "none": 0.02},
                        )
                    }
                },
            )

        client = self.sdk_client(respond)
        selected = await browser_use.select_target(
            "click",
            "Project settings",
            [
                ACCOUNT,
                {
                    **PROJECT,
                    "value": "form-secret",
                    "cookies": "session-secret",
                    "identity": {"href": "private-link"},
                },
            ],
            client,
        )
        self.assertEqual(selected, {"kind": "selected", "ref": "e2", "confidence": 0.95})
        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertEqual(str(request.url), "https://api.typesafe.ai/v1/systemone")
        self.assertEqual(request.headers["authorization"], "Bearer test-key")
        self.assertRegex(request.headers["x-typesafe-sdk"], r"^typesafe-sdk/")
        body = json.loads(request.content)
        self.assertEqual(body["model"], "jev-1.13.0")
        self.assertEqual(body["state"], {"action": "click", "target": "Project settings"})
        criteria = body["questions"]["target"]["criteria"]
        self.assertEqual(set(criteria), {"none", "e1", "e2"})
        self.assertEqual(set(criteria["e2"]), {"role", "name", "context", "editable", "enabled"})
        self.assertEqual(criteria["e2"]["context"], "Project: kodelet")
        for secret in ("form-secret", "session-secret", "private-link", "test-key"):
            self.assertNotIn(secret, request.content.decode())

    async def test_no_match_and_low_confidence_never_select(self):
        cases = [
            ("none", {"e1": 0, "none": 1}, 0.95, "no_match"),
            ("e1", {"e1": 0.81, "none": 0.19}, 0.61, "ambiguous"),
            ("e1", {"e1": 0.74, "none": 0.26}, 0.95, "ambiguous"),
        ]
        for selected, probabilities, confidence, reason in cases:
            with self.subTest(reason=reason, confidence=confidence):
                client = self.sdk_client(
                    lambda request, selected=selected, probabilities=probabilities, confidence=confidence: (
                        httpx2.Response(
                            200,
                            json={
                                "answers": {
                                    "target": choice(
                                        selected, probabilities, confidence, probabilities
                                    ),
                                }
                            },
                        )
                    )
                )
                result = await browser_use.select_target(
                    "fill",
                    "Project name",
                    [{**ACCOUNT, "editable": True}],
                    client,
                )
                self.assertEqual(
                    result, {"kind": "abstained", "reason": reason, "confidence": confidence}
                )
        client = FakeClient(lambda body: self.fail("Empty candidates must not call inference"))
        self.assertEqual(
            await browser_use.select_target("click", "Absent", [], client),
            {
                "kind": "abstained",
                "reason": "no_match",
            },
        )
        self.assertEqual(client.calls, [])

    async def test_rounded_distributions_do_not_normalize_confidence_gates(self):
        for count in (2, 80):
            candidates = [{**ACCOUNT, "ref": f"e{i + 1}"} for i in range(count)]
            zeros = {candidate["ref"]: 0 for candidate in candidates}
            for second in (0.23, 0.25, 0.04, 0.16):
                with self.subTest(count=count, second=second):
                    valid = second in (0.23, 0.25)
                    probabilities = {
                        **zeros,
                        "e1": 0.75 if valid else 0.9,
                        "e2": second,
                        "none": 0.01 if valid else 0,
                    }
                    client = self.sdk_client(
                        lambda request, probabilities=probabilities: httpx2.Response(
                            200,
                            json={
                                "answers": {
                                    "target": choice(
                                        "e1", probabilities, probabilities=probabilities
                                    ),
                                }
                            },
                        )
                    )
                    if valid:
                        result = await browser_use.select_target(
                            "click", "Settings", candidates, client
                        )
                        self.assertEqual(result["kind"], "selected")
                    else:
                        with self.assertRaises(browser_dom.BrowserUseError) as caught:
                            await browser_use.select_target("click", "Settings", candidates, client)
                        self.assertEqual(caught.exception.code, "jev_invalid_response")
            probabilities = {**zeros, "e1": 0.74, "e2": 0.23, "none": 0.01}
            client = self.sdk_client(
                lambda request, probabilities=probabilities: httpx2.Response(
                    200,
                    json={
                        "answers": {
                            "target": choice("e1", probabilities, probabilities=probabilities),
                        }
                    },
                )
            )
            if count == 2:
                with self.assertRaises(browser_dom.BrowserUseError):
                    await browser_use.select_target("click", "Settings", candidates, client)
            else:
                result = await browser_use.select_target("click", "Settings", candidates, client)
                self.assertEqual(result["reason"], "ambiguous")

    async def test_malformed_responses_and_transport_failures_are_sanitized_without_retry(self):
        responses = [
            httpx2.Response(200, json={"answers": {"target": answer}})
            for answer in (
                choice("not-observed", ("e1", "none")),
                choice("e1", ("e1",)),
                choice("e1", ("e1", "none"), probabilities={"e1": 0.1, "none": 0.9}),
                choice("e1", ("e1", "none"), probabilities={"e1": 1, "none": 1}),
                choice("e1", ("e1", "none"), confidence="0.95"),
                None,
            )
        ] + [
            httpx2.Response(200, json={"answers": {}}),
            httpx2.Response(200, json={"answers": []}),
            httpx2.Response(200, text="sensitive malformed JSON"),
            *[
                httpx2.Response(status, text="sensitive upstream response")
                for status in (401, 429, 503)
            ],
            httpx2.ConnectError("sensitive connection details"),
            httpx2.ReadTimeout("sensitive timeout"),
        ]
        for response in responses:
            with self.subTest(response=repr(response)):
                attempts = []

                def respond(request, attempts=attempts, response=response):
                    attempts.append(request)
                    if isinstance(response, Exception):
                        raise response
                    return response

                client = self.sdk_client(respond)
                with self.assertRaises(browser_dom.BrowserUseError) as caught:
                    await browser_use.select_target("click", "Settings", [ACCOUNT], client)
                self.assertIn(caught.exception.code, ("jev_invalid_response", "jev_unavailable"))
                self.assertNotIn("sensitive", str(caught.exception))
                self.assertNotIn("api.typesafe", str(caught.exception))
                self.assertEqual(len(attempts), 1)

    async def test_real_sdk_preserves_unused_goal_branches(self):
        requests = []

        def respond(request):
            body = json.loads(request.content)
            requests.append(body)
            answers = goal_reply(body, "give_up", blocker="needs_confirmation")
            answers["nextAction"] = {"choice": "stale-action"}
            del answers["evidence"]
            return httpx2.Response(200, json={"answers": answers})

        h = Harness()
        client = self.sdk_client(respond)
        h.factory.return_value = client
        output = await h.execute()
        self.assertEqual(output["data"]["status"], "give_up", output)
        self.assertEqual(output["data"]["reason"], "needs_confirmation")
        self.assertEqual(len(requests), 1)
        self.assertEqual(h.page.dispatched, [])
        h.lease.release.assert_awaited_once()
        h.browser.close.assert_awaited_once()


class LifecycleTests(BrowserTestCase):
    async def test_sdk_validation_errors_never_echo_rejected_secret_values(self):
        secret = "never-echo-this-private-input"
        cases = [
            {"value": secret},
            {"action": "fill", "target": None, "value": secret},
            {"action": "fill", "target": "Name", "value": {"nested": secret}},
            {"action": "fill", "target": "Name", "value": secret * 500},
            {**GOAL, "inputs": {"name": {"nested": secret}}},
        ]
        harness = await create_test_harness(browser_use.ext)
        with patch.object(browser_use, "execute_browser_use", new_callable=AsyncMock) as execute:
            for input_dict in cases:
                with self.subTest(action=input_dict.get("action")):
                    with self.assertRaises(ValidationError) as caught:
                        await harness.execute_tool({"name": "browser_use", "input": input_dict})
                    self.assertNotIn(secret, str(caught.exception))
                    self.assertNotIn("input_value=", str(caught.exception))
            execute.assert_not_awaited()
        with self.assertRaises(ValidationError) as caught:
            browser_use.FillInput.model_validate(cases[2])
        self.assertNotIn(secret, str(caught.exception))

    async def test_hanging_cdp_query_or_detach_cannot_delay_cancel_or_deadline(self):
        for stage in ("query", "detach"):
            for interrupt in ("cancel", "deadline"):
                with self.subTest(stage=stage, interrupt=interrupt):
                    h = Harness()
                    entered, allow = asyncio.Event(), asyncio.Event()

                    async def query(method, entered=entered, allow=allow):
                        entered.set()
                        await allow.wait()
                        return {"targetInfo": {"targetId": "unrelated-page"}}

                    async def detach(stage=stage, entered=entered, allow=allow):
                        if stage == "detach":
                            entered.set()
                        await allow.wait()

                    if stage == "query":
                        h.other.cdp.send.side_effect = query
                    h.other.cdp.detach.side_effect = detach
                    task = asyncio.create_task(
                        h.execute(
                            {
                                **GOAL,
                                "timeoutMs": 50 if interrupt == "deadline" else 60000,
                            }
                        )
                    )
                    try:
                        await asyncio.wait_for(entered.wait(), 1)
                        if interrupt == "cancel":
                            task.cancel()
                        # wait_for(task) would cancel again and hide a stuck finally.
                        done, _ = await asyncio.wait({task}, timeout=0.5)
                        self.assertIn(task, done, "CDP teardown blocked disconnect/release")
                        output = task.result()
                        self.assertEqual(
                            output["data"]["status"],
                            "canceled" if interrupt == "cancel" else "timeout",
                            output,
                        )
                        h.browser.close.assert_awaited_once()
                        h.lease.release.assert_awaited_once()
                        self.assertEqual(h.page.dispatched, [])
                        h.factory.assert_not_called()
                    finally:
                        allow.set()
                        await asyncio.wait_for(task, 2)
                        await asyncio.wait_for(
                            asyncio.gather(*list(browser_use._pending_cleanup)), 2
                        )
                        await asyncio.sleep(0)
                    h.other.cdp.detach.assert_awaited_once()
                    self.assertFalse(browser_use._pending_cleanup)

    async def test_shared_page_uses_cdp_identity_and_detaches_all_sessions(self):
        h = Harness()
        self.assertIs(await browser_use.shared_page(h.browser, "shared-page"), h.page)
        for page in (h.other, h.page):
            page.cdp.send.assert_awaited_once_with("Target.getTargetInfo")
            page.cdp.detach.assert_awaited_once()
        with self.assertRaises(browser_dom.BrowserUseError) as caught:
            await browser_use.shared_page(h.browser, "missing")
        self.assertEqual(caught.exception.code, "shared_page_missing")
        h.browser.contexts[0].pages = [h.other] * 33
        with self.assertRaises(browser_dom.BrowserUseError) as caught:
            await browser_use.shared_page(h.browser, "shared-page")
        self.assertEqual(caught.exception.code, "too_many_pages")

    async def test_observe_and_navigate_need_no_client(self):
        for action in ({"action": "observe"}, {"action": "navigate", "url": "about:blank"}):
            with self.subTest(action=action):
                h = Harness(key=None)
                output = await h.execute(action)
                self.assertEqual(
                    output["data"]["status"],
                    "observed" if action["action"] == "observe" else "navigated",
                )
                if action["action"] == "observe":
                    self.assertEqual(len(output["data"]["snapshot"]["elements"]), 2)
                    self.assertNotIn("identity", output["content"])
                else:
                    h.page.goto.assert_awaited_once_with(
                        "about:blank",
                        wait_until="domcontentloaded",
                        timeout=5000,
                    )
                    h.other.goto.assert_not_awaited()
                self.assert_cleaned(h, semantic=False)
                self.assert_private(h, output)

    async def test_invalid_input_credentials_and_urls_fail_before_acquisition(self):
        cases = [({"action": "click", "target": "Settings"}, "missing_key")]
        cases += [
            ({"action": "navigate", "url": url}, "invalid_url")
            for url in (
                "javascript:alert(1)",
                "file:///tmp/secret",
                "https://user:secret@example.com",
                "https://example.com:99999",
                "https://example.com\\secret",
                "https://example.com\nsecret",
            )
        ]
        cases.append(({"action": "click", "target": " "}, "invalid_input"))
        for input_dict, reason in cases:
            with self.subTest(input=input_dict):
                h = Harness(key=None)
                output = await h.execute(input_dict)
                self.assertEqual(output["data"]["reason"], reason, output)
                h.ctx.browser.acquire.assert_not_awaited()
                h.connect.assert_not_awaited()

    async def test_duplicate_label_selection_and_fill_keep_values_local(self):
        for action in ("click", "fill"):
            with self.subTest(action=action):
                h = Harness()
                input_dict = {"action": action, "target": "Project settings"}
                if action == "fill":
                    h.page.controls[1] = Control("Project name", editable=True)
                    input_dict.update(target="Project name", value="browser-only-secret")
                output = await h.execute(input_dict)
                self.assertEqual(output["data"]["status"], "acted", output)
                self.assertFalse(output["data"]["verified"])
                self.assertEqual(output["data"]["target"]["ref"], "e2")
                self.assertEqual(len(h.page.dispatched), 1)
                self.assertEqual(h.page.dispatched[0]["operation"], action)
                if action == "fill":
                    self.assertEqual(h.page.controls[1].value, "browser-only-secret")
                    self.assertEqual(
                        set(h.client.calls[0]["questions"]["target"]["criteria"]), {"none", "e2"}
                    )
                self.assert_private(h, output, "browser-only-secret")
                self.assert_cleaned(h)

    async def test_no_match_and_truncation_do_not_dispatch(self):
        for truncated in (False, True):
            h = Harness(lambda body: target_reply(body, "none"))
            if truncated:
                h.page.controls = [Control() for _ in range(81)]
            output = await h.execute({"action": "click", "target": "Missing"})
            self.assertEqual(output["data"]["status"], "not_executed", output)
            if truncated:
                self.assertEqual(output["data"]["reason"], "truncated_snapshot")
                self.assertEqual(h.client.calls, [])
                self.assertEqual(len(h.page.handles), 80)
            else:
                self.assertEqual(output["data"]["decision"]["reason"], "no_match")
            self.assertEqual(h.page.dispatched, [])
            self.assert_cleaned(h)

    async def test_navigation_identity_and_detachment_invalidate_target(self):
        for mutation in ("navigation", "identity", "detach"):
            with self.subTest(mutation=mutation):
                h = Harness()

                def respond(body, mutation=mutation, h=h):
                    if mutation == "navigation":
                        h.page.navigate()
                    elif mutation == "identity":
                        h.page.controls[1].description["identity"]["id"] = "replacement"
                    else:
                        h.page.controls[1].attached = False
                    return target_reply(body)

                h.client.respond = respond
                output = await h.execute({"action": "click", "target": "Project settings"})
                self.assertEqual(
                    output["data"]["reason"],
                    "stale_snapshot" if mutation == "navigation" else "stale_target",
                )
                self.assertEqual(h.page.dispatched, [])
                self.assert_cleaned(h)

    async def test_mutation_failure_is_unknown_without_retry_or_raw_error(self):
        h = Harness()

        def fail(operation):
            raise RuntimeError("private endpoint ws://localhost/devtools/browser/private")

        h.page.controls[1].after_action = fail
        output = await h.execute({"action": "click", "target": "Project settings"})
        self.assertEqual(output["data"]["status"], "outcome_unknown", output)
        self.assertEqual(len(h.page.dispatched), 1)
        self.assertEqual(len(h.client.calls), 1)
        self.assertIn("do not automatically retry", output["content"])
        self.assert_private(h, output, "private endpoint")
        self.assert_cleaned(h)

    async def test_cancellation_during_inference_releases_without_dispatch(self):
        started = asyncio.Event()

        async def respond(body):
            started.set()
            await asyncio.Event().wait()

        h = Harness(respond)
        task = asyncio.create_task(h.execute({"action": "click", "target": "Settings"}))
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        output = await asyncio.wait_for(task, 2)
        self.assertEqual(output["data"]["status"], "not_executed", output)
        self.assertEqual(h.page.dispatched, [])
        self.assert_cleaned(h)

    async def test_connect_failure_releases_lease_and_sanitizes_details(self):
        h = Harness()
        h.connect.side_effect = RuntimeError("private connection endpoint")
        output = await h.execute({"action": "observe"})
        self.assertEqual(output["data"]["status"], "not_executed", output)
        self.assert_private(h, output, "private connection endpoint")
        h.lease.release.assert_awaited_once()
        h.browser.close.assert_not_awaited()

    async def test_release_failure_preserves_result_with_cleanup_warning(self):
        h = Harness()
        h.lease.release.side_effect = RuntimeError("private host failure")
        output = await h.execute({"action": "observe"})
        self.assertEqual(output["data"]["status"], "observed", output)
        self.assertIn("host invocation cleanup", output["data"]["cleanupWarning"])
        self.assert_private(h, output, "private host failure")
        self.assert_cleaned(h, semantic=False)

    async def test_canceled_acquisition_or_connection_disposes_late_resources_once(self):
        for stage in ("acquire", "connect"):
            with self.subTest(stage=stage):
                h = Harness()
                entered, allow = asyncio.Event(), asyncio.Event()

                async def delayed(*args, entered=entered, allow=allow, h=h, stage=stage, **kwargs):
                    entered.set()
                    await allow.wait()
                    return h.lease if stage == "acquire" else h.browser

                boundary = h.ctx.browser.acquire if stage == "acquire" else h.connect
                boundary.side_effect = delayed
                task = asyncio.create_task(h.execute({"action": "observe"}))
                try:
                    await asyncio.wait_for(entered.wait(), 1)
                    task.cancel()
                    output = await asyncio.wait_for(task, 2)
                    self.assertEqual(output["data"]["status"], "not_executed", output)
                    if stage == "acquire":
                        h.lease.release.assert_not_awaited()
                    else:
                        h.lease.release.assert_awaited_once()
                    h.browser.close.assert_not_awaited()
                finally:
                    allow.set()
                    await asyncio.wait_for(asyncio.gather(*list(browser_use._pending_cleanup)), 2)
                h.lease.release.assert_awaited_once()
                if stage == "connect":
                    h.browser.close.assert_awaited_once()

    async def test_slow_cleanup_continues_after_return_and_reports_uncertainty(self):
        h = Harness()
        allow, released = asyncio.Event(), asyncio.Event()

        async def release():
            await allow.wait()
            released.set()

        h.lease.release.side_effect = release
        try:
            output = await asyncio.wait_for(h.execute({"action": "observe"}), 2)
            self.assertEqual(output["data"]["status"], "observed", output)
            self.assertIn("cleanupWarning", output["data"])
            self.assertFalse(released.is_set())
            self.assertTrue(browser_use._pending_cleanup)
        finally:
            allow.set()
            await asyncio.wait_for(asyncio.gather(*list(browser_use._pending_cleanup)), 2)
        self.assertTrue(released.is_set())
        self.assert_cleaned(h, semantic=False)


class GoalLoopTests(BrowserTestCase):
    def test_goal_schema_and_runner_budgets_are_strict(self):
        self.assertEqual(browser_use.BrowserUseInput.model_validate(GOAL).root.maxSteps, 100)
        invalid = [
            {"action": "run", "goal": "Open settings"},
            {**GOAL, "goal": " "},
            {**GOAL, "successCriteria": " "},
            {**GOAL, "inputs": {"name": 123}},
            {**GOAL, "inputs": {"invalid-name": "value"}},
            *[{**GOAL, "maxSteps": value} for value in (0, -1, 1.5, 101, True)],
            *[{**GOAL, "timeoutMs": value} for value in (0, -1, 1.5, 300001, True)],
        ]
        for input_dict in invalid:
            with self.subTest(input=input_dict), self.assertRaises(ValidationError):
                browser_use.BrowserUseInput.model_validate(input_dict)
        self.assertEqual(browser_use.goal_limits({}, GOAL), {"maxSteps": 12, "timeoutMs": 60000})

    async def test_bad_runner_configuration_fails_before_acquisition(self):
        for name, ceiling in (
            ("KODELET_BROWSER_USE_MAX_TURNS", 100),
            ("KODELET_BROWSER_USE_TIMEOUT_MS", 300000),
        ):
            for value in ("", "0", "-1", "1.5", "NaN", str(ceiling + 1)):
                with self.subTest(name=name, value=value):
                    h = Harness()
                    h.ctx.env[name] = value
                    output = await h.execute()
                    self.assertEqual(output["data"]["reason"], "invalid_config", output)
                    h.ctx.browser.acquire.assert_not_awaited()

    async def test_fill_save_done_on_one_connection_with_private_values(self):
        secret = "browser-only-project-value"
        h = Harness()
        field = Control("Project name", editable=True)
        save = Control("Save changes")

        def open_settings(operation):
            h.page.controls = [field, save]
            h.page.evidence = [Control("Project settings", role="heading")]

        def saved(operation):
            h.page.evidence.append(Control(f"Project saved: {field.value}", role="status"))

        h.page.controls[1].after_action = open_settings
        save.after_action = saved

        def respond(body):
            turn = len(h.client.calls)
            if turn == 1:
                selected = next(
                    key
                    for key, value in body["questions"]["nextAction"]["criteria"].items()
                    if isinstance(value, dict)
                    and value.get("element", {}).get("context") == "Project: kodelet"
                )
            elif turn == 2:
                selected = action_id(body, "fill", "Project name", "projectName")
            elif turn == 3:
                selected = action_id(body, "click", "Save changes")
            else:
                return goal_reply(body, "done")
            return goal_reply(body, action=selected)

        h.client.respond = respond
        output = await h.execute(
            {
                **GOAL,
                "goal": f"Rename the current project to {secret} and save it",
                "inputs": {"projectName": secret},
                "successCriteria": "Project saved is visible and Project name matches projectName",
            }
        )
        data = output["data"]
        self.assertEqual(data["status"], "done", output)
        self.assertEqual(data["verification"], "model")
        self.assertFalse(data["verified"])
        self.assertEqual((data["steps"], data["actionsCompleted"]), (4, 3))
        self.assertEqual(data["limits"], {"maxSteps": 12, "timeoutMs": 60000})
        self.assertEqual(
            data["evidence"]["fieldMatches"],
            [{"name": "Project name", "inputRefs": ["projectName"]}],
        )
        self.assertIn("Project saved: [input:projectName]", data["evidence"]["messages"])
        self.assertEqual(
            h.page.dispatched,
            [
                {"operation": "click", "name": "Settings"},
                {"operation": "fill", "name": "Project name", "value": secret},
                {"operation": "click", "name": "Save changes"},
            ],
        )
        for turn, body in enumerate(h.client.calls, 1):
            self.assertEqual(body["state"]["availableInputs"], ["projectName"])
            for key in body["questions"]["nextAction"]["criteria"]:
                if key != "none":
                    self.assertRegex(key, rf"^t{turn}_a\d+$")
        third = h.client.calls[2]
        self.assertEqual(third["state"]["observation"]["inputMatches"]["e1"], ["projectName"])
        self.assertFalse(
            any(
                isinstance(value, dict) and value.get("operation") == "fill"
                for value in third["questions"]["nextAction"]["criteria"].values()
            )
        )
        h.ctx.browser.acquire.assert_awaited_once()
        h.connect.assert_awaited_once()
        self.assertGreaterEqual(h.ctx.update.await_count, 4)
        self.assertGreater(len(h.page.handles), 10)
        self.assert_private(h, output, secret)
        self.assert_cleaned(h)

    async def test_registry_omits_password_disabled_toggle_and_matching_fill(self):
        h = Harness(lambda body: goal_reply(body, "give_up", blocker="missing_input"))
        h.page.controls = [
            Control("First name", editable=True, value="first-secret"),
            Control("Last name", editable=True),
            Control("Password", editable=True, kind="password", value="password-secret"),
            Control("Delete", enabled=False),
            Control("Read only", editable=True, enabled=False, value="first-secret"),
            *[Control(role, role=role) for role in ("checkbox", "radio", "switch", "tab")],
        ]
        output = await h.execute(
            {**GOAL, "inputs": {"firstName": "first-secret", "lastName": "last-secret"}}
        )
        actions = [
            value
            for value in h.client.calls[0]["questions"]["nextAction"]["criteria"].values()
            if isinstance(value, dict)
        ]
        fills = [
            (value["element"]["name"], value["inputRef"])
            for value in actions
            if value["operation"] == "fill"
        ]
        self.assertEqual(
            fills,
            [("First name", "lastName"), ("Last name", "firstName"), ("Last name", "lastName")],
        )
        self.assertFalse(
            any(value["operation"] in ("click", "fill_from_goal") for value in actions)
        )
        presses = [value["element"]["name"] for value in actions if value["operation"] == "press"]
        self.assertEqual(presses, ["First name"])
        self.assertEqual(output["data"]["status"], "give_up", output)
        self.assertEqual(h.page.dispatched, [])
        self.assert_private(h, output, "first-secret", "last-secret", "password-secret")
        self.assert_cleaned(h)

    async def test_give_up_ignores_absent_or_malformed_unused_answers(self):
        for blocker in browser_goal.GOAL_BLOCKER_CRITERIA:
            with self.subTest(blocker=blocker):

                def respond(body, blocker=blocker):
                    answers = goal_reply(body, "give_up", blocker=blocker)
                    answers["nextAction"] = {"choice": "stale-action"}
                    del answers["evidence"]
                    return answers

                h = Harness(respond)
                output = await h.execute()
                self.assertEqual(output["data"]["status"], "give_up", output)
                self.assertEqual(output["data"]["reason"], blocker)
                self.assertEqual(output["data"]["actionsCompleted"], 0)
                self.assertEqual(len(h.client.calls), 1)
                self.assertEqual(h.page.dispatched, [])
                self.assert_cleaned(h)

    async def test_malformed_action_answers_never_dispatch(self):
        for scenario in (
            "missing_status",
            "unknown_action",
            "bad_distribution",
            "uncertain_action",
        ):
            with self.subTest(scenario=scenario):

                def respond(body, scenario=scenario):
                    answers = goal_reply(body, action=action_id(body, "click"))
                    if scenario == "missing_status":
                        del answers["status"]
                    elif scenario == "unknown_action":
                        answers["nextAction"]["choice"] = "t0_a1"
                    elif scenario == "bad_distribution":
                        answers["nextAction"]["probabilities"]["none"] = 1
                    else:
                        answers["nextAction"]["confidence"] = 0.2
                    return answers

                h = Harness(respond)
                output = await h.execute()
                self.assertEqual(
                    output["data"]["status"],
                    "give_up" if scenario == "uncertain_action" else "failed",
                    output,
                )
                if scenario != "uncertain_action":
                    self.assertEqual(output["data"]["reason"], "jev_invalid_response")
                self.assertEqual(h.page.dispatched, [])
                self.assert_cleaned(h)

    async def test_uncertain_status_allows_only_inspection_and_waiting(self):
        for operation in (
            "inspect",
            "wait",
            "click",
            "fill_from_goal",
            "press",
            "reload",
            "back",
            "done",
            "give_up",
        ):
            with self.subTest(operation=operation):

                def respond(body, operation=operation):
                    status = operation if operation in ("done", "give_up") else "act"
                    selected = action_id(body, operation) if status == "act" else "none"
                    answers = goal_reply(body, status, selected, blocker="needs_confirmation")
                    answers["status"]["confidence"] = 0.2
                    answers["nextAction"]["confidence"] = 0.2
                    return answers

                h = Harness(respond)
                h.page.controls = [
                    Control("Search", editable=True, value="existing"),
                    Control("Save"),
                ]
                if operation in ("inspect", "give_up"):
                    h.page.controls += [Control("Other") for _ in range(80)]
                output = await h.execute({**GOAL, "maxSteps": 1})
                read_only = operation in ("inspect", "wait")
                self.assertEqual(
                    output["data"]["status"], "step_limit" if read_only else "give_up", output
                )
                self.assertEqual(output["data"]["actionsCompleted"], int(read_only))
                if not read_only:
                    self.assertEqual(
                        output["data"]["reason"],
                        "needs_confirmation" if operation == "give_up" else "uncertain",
                    )
                self.assertEqual(
                    len(h.client.calls), 1, "Uncertain mutations must not initiate extraction"
                )
                self.assertEqual(h.page.dispatched, [])
                h.page.reload.assert_not_awaited()
                h.page.go_back.assert_not_awaited()
                self.assert_cleaned(h)

    async def test_done_requires_joint_evidence_valid_noul_and_fresh_observation(self):
        cases = [
            (None, "failed"),
            ({"type": "choice", "choice": "done"}, "failed"),
            *[
                ({"type": "noul", "noul": value}, "failed")
                for value in (-0.1, 1.1, "1", True, float("nan"))
            ],
            ({"type": "noul", "noul": 0.89}, "give_up"),
            ({"type": "noul", "noul": 0.9}, "done"),
        ]
        for evidence, expected in cases:
            with self.subTest(evidence=evidence):

                def respond(body, evidence=evidence):
                    answers = goal_reply(body, "done")
                    answers["evidence"] = evidence
                    answers["nextAction"] = {"unused": "malformed"}
                    return answers

                h = Harness(respond)
                h.page.controls = [
                    Control("Project name", editable=True, enabled=False, value="saved-secret")
                ]
                output = await h.execute({**GOAL, "inputs": {"projectName": "saved-secret"}})
                self.assertEqual(output["data"]["status"], expected, output)
                self.assertEqual(
                    h.client.calls[0]["state"]["completionEvidence"]["fieldMatches"],
                    [
                        {"name": "Project name", "inputRefs": ["projectName"]},
                    ],
                )
                if expected == "done":
                    self.assertFalse(output["data"]["verified"])
                    self.assertEqual(
                        len(h.page.handles), 4, "Done must perform a second observation"
                    )
                elif expected == "failed":
                    self.assertEqual(output["data"]["reason"], "jev_invalid_response")
                else:
                    self.assertEqual(output["data"]["reason"], "completion_unverified")
                self.assertEqual(h.page.dispatched, [])
                self.assert_private(h, output, "saved-secret")
                self.assert_cleaned(h)
        h = Harness(lambda body: goal_reply(body, "done"))
        h.page.evidence = []
        output = await h.execute()
        self.assertEqual(output["data"]["reason"], "completion_unverified", output)

    async def test_changed_completion_evidence_is_reobserved_not_accepted(self):
        h = Harness()

        def respond(body):
            if len(h.client.calls) == 1:
                h.page.evidence[0].description["name"] = "Login required"
                return goal_reply(body, "done")
            return goal_reply(body, "give_up", blocker="missing_input")

        h.client.respond = respond
        output = await h.execute()
        self.assertEqual(output["data"]["reason"], "missing_input", output)
        self.assertEqual(len(h.client.calls), 2)
        self.assertEqual(h.client.calls[1]["state"]["observation"]["evidence"], ["Login required"])
        self.assertEqual(h.page.dispatched, [])
        self.assert_cleaned(h)

    async def test_runner_and_caller_bounds_are_minimums_and_waits_consume_turns(self):
        for runner_limit, caller_limit in ((1, 9), (9, 1), (2, 2)):
            with self.subTest(runner=runner_limit, caller=caller_limit):
                h = Harness(lambda body: goal_reply(body, action=action_id(body, "wait")))
                h.ctx.env["KODELET_BROWSER_USE_MAX_TURNS"] = str(runner_limit)
                output = await h.execute({**GOAL, "maxSteps": caller_limit})
                limit = min(runner_limit, caller_limit)
                self.assertEqual(output["data"]["status"], "step_limit", output)
                self.assertEqual(output["data"]["steps"], limit)
                self.assertEqual(output["data"]["actionsCompleted"], limit)
                self.assertEqual(len(h.client.calls), limit)
                self.assert_cleaned(h)

    async def test_wait_deadline_is_timeout_not_unknown_mutation(self):
        for runner_ms, caller_ms in ((50, 60000), (60000, 50)):
            h = Harness(lambda body: goal_reply(body, action=action_id(body, "wait")))
            h.ctx.env["KODELET_BROWSER_USE_TIMEOUT_MS"] = str(runner_ms)
            output = await h.execute({**GOAL, "timeoutMs": caller_ms})
            self.assertEqual(output["data"]["status"], "timeout", output)
            self.assertEqual(output["data"]["actionsCompleted"], 0)
            self.assertEqual(len(h.client.calls), 1)
            self.assert_cleaned(h)

    async def test_reload_back_and_repeated_action_guard(self):
        for operation in ("reload", "back", "click", "wait"):
            with self.subTest(operation=operation):
                h = Harness(
                    lambda body, operation=operation: goal_reply(
                        body, action=action_id(body, operation)
                    )
                )
                output = await h.execute({**GOAL, "maxSteps": 5})
                self.assertEqual(output["data"]["reason"], "no_progress", output)
                self.assertEqual(
                    output["data"]["actionsCompleted"], 3 if operation == "wait" else 1
                )
                self.assertEqual(len(h.client.calls), 3 if operation == "wait" else 2)
                self.assertEqual(h.page.reload.await_count, int(operation == "reload"))
                self.assertEqual(h.page.go_back.await_count, int(operation == "back"))
                self.assertEqual(len(h.page.dispatched), int(operation == "click"))
                self.assert_cleaned(h)

    async def test_previous_turn_action_ids_are_rejected(self):
        h = Harness()
        previous = None

        def respond(body):
            nonlocal previous
            if previous is None:
                previous = action_id(body, "click")
            return goal_reply(body, action=previous)

        h.client.respond = respond
        output = await h.execute()
        self.assertEqual(output["data"]["reason"], "jev_invalid_response", output)
        self.assertEqual(output["data"]["actionsCompleted"], 1)
        self.assertEqual(len(h.page.dispatched), 1)
        self.assert_cleaned(h)

    async def test_later_inference_failure_retains_completed_history(self):
        h = Harness()

        def respond(body):
            if len(h.client.calls) > 1:
                raise RuntimeError("private provider error")
            return goal_reply(body, action=action_id(body, "click"))

        h.client.respond = respond
        output = await h.execute()
        self.assertEqual(output["data"]["status"], "failed", output)
        self.assertEqual(output["data"]["reason"], "jev_unavailable")
        self.assertEqual(output["data"]["actionsCompleted"], 1)
        self.assertEqual(output["data"]["history"][0]["operation"], "click")
        self.assertEqual(h.client.calls[1]["state"]["recentActions"], output["data"]["history"])
        self.assertIn("not rolled back", output["content"])
        self.assert_private(h, output, "private provider error")
        self.assert_cleaned(h)

    async def test_cancellation_at_progress_inference_and_mid_action(self):
        for stage in ("progress", "inference", "action"):
            with self.subTest(stage=stage):
                started = asyncio.Event()
                h = Harness(lambda body: goal_reply(body, action=action_id(body, "click")))

                async def block(*args, started=started, **kwargs):
                    started.set()
                    await asyncio.Event().wait()

                if stage == "progress":
                    h.ctx.update.side_effect = block
                elif stage == "inference":
                    h.client.respond = block
                else:
                    h.page.controls[0].after_action = block
                task = asyncio.create_task(h.execute())
                await asyncio.wait_for(started.wait(), 1)
                task.cancel()
                output = await asyncio.wait_for(task, 2)
                self.assertEqual(
                    output["data"]["status"],
                    "outcome_unknown" if stage == "action" else "canceled",
                    output,
                )
                self.assertEqual(len(h.page.dispatched), int(stage == "action"))
                self.assertEqual(output["data"]["actionsCompleted"], 0)
                self.assertLessEqual(len(h.client.calls), 1)
                self.assert_cleaned(h)

    async def test_progress_self_cancellation_cannot_reach_dispatch(self):
        h = Harness(lambda body: goal_reply(body, action=action_id(body, "click")))

        async def update(content, data):
            if data.get("operation") == "click":
                asyncio.current_task().cancel()

        h.ctx.update.side_effect = update
        output = await asyncio.create_task(h.execute())
        self.assertEqual(output["data"]["status"], "canceled", output)
        self.assertEqual(h.page.dispatched, [])
        self.assert_cleaned(h)

    async def test_goal_action_failure_is_unknown_without_retry(self):
        h = Harness(lambda body: goal_reply(body, action=action_id(body, "click")))

        def fail(operation):
            raise RuntimeError("private endpoint details")

        h.page.controls[0].after_action = fail
        output = await h.execute()
        self.assertEqual(output["data"]["status"], "outcome_unknown", output)
        self.assertEqual(len(h.page.dispatched), 1)
        self.assertEqual(len(h.client.calls), 1)
        self.assertEqual(output["data"]["actionsCompleted"], 0)
        self.assertIn("do not automatically retry", output["content"])
        self.assert_private(h, output, "private endpoint details")
        self.assert_cleaned(h)

    async def test_sdk_request_is_canceled_by_goal_deadline_without_retry(self):
        requests = []
        canceled = asyncio.Event()

        async def respond(request):
            requests.append(request)
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                canceled.set()
                raise

        h = Harness()
        h.factory.return_value = self.sdk_client(respond)
        output = await h.execute({**GOAL, "timeoutMs": 50})
        self.assertEqual(output["data"]["status"], "timeout", output)
        self.assertTrue(canceled.is_set())
        self.assertEqual(len(requests), 1)
        self.assertEqual(h.page.dispatched, [])
        h.lease.release.assert_awaited_once()
        h.browser.close.assert_awaited_once()

    async def test_off_batch_field_edits_invalidate_save_without_exposing_values(self):
        h = Harness()
        field = Control("Project name", editable=True, value="off-batch-original")
        h.page.controls = [field, *[Control("Other") for _ in range(79)], Control("Save")]

        def respond(body):
            if len(h.client.calls) == 1:
                return goal_reply(body, action=action_id(body, "inspect", section="controls"))
            field.value = "off-batch-human-edit"
            return goal_reply(body, action=action_id(body, "click", "Save"))

        h.client.respond = respond
        output = await h.execute()
        self.assertEqual(output["data"]["reason"], "stale_snapshot", output)
        self.assertEqual(output["data"]["actionsCompleted"], 1)
        self.assertEqual(
            h.client.calls[1]["state"]["observation"]["pagination"]["controls"]["offset"], 80
        )
        self.assertEqual(h.page.dispatched, [])
        self.assert_private(h, output, "off-batch-original", "off-batch-human-edit")
        self.assert_cleaned(h)

    async def test_large_page_inspect_extract_press_and_finish_on_later_evidence(self):
        h = Harness()
        query = Control("Search", editable=True, kind="search")
        h.page.controls = [*[Control("Other") for _ in range(80)], query]
        h.page.evidence = [Control(f"Heading {i}", role="heading") for i in range(20)]

        def submitted(operation):
            if operation == "press":
                h.page.evidence.append(Control("C++ results", role="heading"))

        query.after_action = submitted

        def respond(body):
            if "start" in body["questions"]:
                return span_reply(body, "w2", "w4")
            observation = body["state"]["observation"]
            if observation["pagination"]["controls"]["offset"] == 0:
                selected = action_id(body, "inspect", section="controls", offset=80)
            elif not query.value:
                selected = action_id(body, "fill_from_goal", "Search")
            elif len(h.page.evidence) == 20:
                selected = action_id(body, "press", "Search")
            elif observation["pagination"]["evidence"]["offset"] == 0:
                selected = action_id(body, "inspect", section="evidence", offset=20)
            else:
                return goal_reply(body, "done")
            return goal_reply(body, action=selected)

        h.client.respond = respond
        goal = "Search for C++"
        output = await h.execute(
            {**GOAL, "goal": goal, "successCriteria": "C++ results are visible"}
        )
        self.assertEqual(output["data"]["status"], "done", output)
        self.assertEqual(output["data"]["actionsCompleted"], 4)
        self.assertEqual(
            h.page.dispatched,
            [
                {"operation": "fill", "name": "Search", "value": "C++"},
                {"operation": "press", "name": "Search", "key": "Enter"},
            ],
        )
        self.assertEqual(output["data"]["history"][1]["sourceSpan"], {"start": 11, "end": 14})
        self.assertEqual(output["data"]["evidence"]["messages"], ["C++ results"])
        self.assertEqual(len(h.client.calls), 6)
        for body in h.client.calls:
            if "nextAction" in body["questions"]:
                self.assertLessEqual(len(body["questions"]["nextAction"]["criteria"]), 201)
        self.assert_private(h, output)
        self.assert_cleaned(h)

    async def test_uncertain_no_action_inspects_unseen_batch(self):
        h = Harness()
        h.page.controls = [Control("Other") for _ in range(81)]

        def respond(body):
            if len(h.client.calls) > 1:
                return goal_reply(body, "give_up", blocker="missing_input")
            answers = goal_reply(body)
            answers["status"]["confidence"] = 0.2
            return answers

        h.client.respond = respond
        output = await h.execute()
        self.assertEqual(output["data"]["reason"], "missing_input", output)
        self.assertEqual(output["data"]["history"][0]["operation"], "inspect")
        self.assertEqual(
            h.client.calls[1]["state"]["observation"]["pagination"]["controls"]["offset"], 80
        )
        self.assertEqual(h.page.dispatched, [])
        self.assert_cleaned(h)

    async def test_goal_navigation_during_inference_blocks_dispatch(self):
        h = Harness()

        def respond(body):
            h.page.navigate()
            return goal_reply(body, action=action_id(body, "click"))

        h.client.respond = respond
        output = await h.execute()
        self.assertEqual(output["data"]["reason"], "stale_snapshot", output)
        self.assertEqual(h.page.dispatched, [])
        self.assert_cleaned(h)

    async def test_extraction_revalidates_replaced_edited_or_navigated_target(self):
        for mutation in ("replacement", "value_change", "navigation"):
            with self.subTest(mutation=mutation):
                h = Harness()
                original = Control("Search", editable=True, value="original-value")
                h.page.controls = [original]

                def respond(body, h=h, mutation=mutation, original=original):
                    if "start" not in body["questions"]:
                        return goal_reply(body, action=action_id(body, "fill_from_goal", "Search"))
                    if mutation == "replacement":
                        h.page.controls = [copy.deepcopy(original)]
                        original.attached = False
                    elif mutation == "value_change":
                        original.value = "human-edited-value"
                    else:
                        h.page.navigate()
                    return span_reply(body, "w1", "w2")

                h.client.respond = respond
                output = await h.execute({**GOAL, "goal": "Find Ada Lovelace"})
                self.assertEqual(
                    output["data"]["reason"],
                    "stale_target" if mutation == "replacement" else "stale_snapshot",
                    output,
                )
                self.assertEqual(output["data"]["actionsCompleted"], 0)
                self.assertEqual(len(h.client.calls), 2)
                self.assertEqual(h.page.dispatched, [])
                self.assert_private(h, output, "original-value", "human-edited-value")
                self.assert_cleaned(h)

    async def test_extraction_honors_deadline_and_cancellation_without_retry(self):
        for interrupt in ("cancel", "deadline"):
            with self.subTest(interrupt=interrupt):
                entered, canceled = asyncio.Event(), asyncio.Event()
                h = Harness()
                h.page.controls = [Control("Search", editable=True)]

                async def respond(body, entered=entered, canceled=canceled):
                    if "start" not in body["questions"]:
                        return goal_reply(body, action=action_id(body, "fill_from_goal", "Search"))
                    entered.set()
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        canceled.set()
                        raise

                h.client.respond = respond
                task = asyncio.create_task(
                    h.execute(
                        {
                            **GOAL,
                            "goal": "Find Ada Lovelace",
                            "timeoutMs": 50 if interrupt == "deadline" else 60000,
                        }
                    )
                )
                await asyncio.wait_for(entered.wait(), 1)
                if interrupt == "cancel":
                    task.cancel()
                output = await asyncio.wait_for(task, 2)
                self.assertEqual(
                    output["data"]["status"],
                    "canceled" if interrupt == "cancel" else "timeout",
                    output,
                )
                self.assertTrue(canceled.is_set())
                self.assertEqual(len(h.client.calls), 2)
                self.assertEqual(h.page.dispatched, [])
                self.assert_cleaned(h)

    async def test_navigation_during_initial_observation_is_reobserved_before_inference(self):
        for destroyed in (False, True):
            with self.subTest(destroyed=destroyed):
                h = Harness(lambda body: goal_reply(body, "done"))
                reads = 0

                async def title(h=h, destroyed=destroyed):
                    nonlocal reads
                    reads += 1
                    if reads == 1:
                        h.page.controls = [Control("Destination control")]
                        h.page.navigate()
                        if destroyed:
                            raise RuntimeError("Execution context destroyed during navigation")
                    return "Project settings"

                h.page.title = title
                output = await h.execute({**GOAL, "maxSteps": 2})
                self.assertEqual(output["data"]["status"], "done", output)
                self.assertEqual(output["data"]["steps"], 2)
                self.assertEqual(reads, 3)
                self.assertEqual(len(h.client.calls), 1)
                self.assertEqual(
                    [c["name"] for c in h.client.calls[0]["state"]["observation"]["elements"]],
                    ["Destination control"],
                )
                self.assertEqual(h.page.dispatched, [])
                self.assert_cleaned(h)


class GoalExtractionTests(BrowserTestCase):
    async def test_span_extraction_preserves_punctuation_and_utf16_offsets(self):
        goal = "🔎 Search C++ and .NET then -10"
        for start, end, expected in (
            ("w2", "w4", "C++"),
            ("w6", "w7", ".NET"),
            ("w9", "w10", "-10"),
        ):
            with self.subTest(expected=expected):
                client = FakeClient(lambda body, start=start, end=end: span_reply(body, start, end))
                history = [{"turn": i} for i in range(12)]
                result = await browser_goal.goal_text(client, goal, ACCOUNT, history)
                self.assertEqual(result["value"], expected)
                start_index = goal.index(expected)
                self.assertEqual(
                    result["sourceSpan"],
                    {
                        "start": len(goal[:start_index].encode("utf-16-le")) // 2,
                        "end": len(goal[: start_index + len(expected)].encode("utf-16-le")) // 2,
                    },
                )
                self.assertEqual(client.calls[0]["state"]["recentActions"], history[-8:])

    async def test_uncertain_reversed_absent_and_invalid_spans_cannot_invent_text(self):
        for scenario in (
            "low_confidence",
            "reversed",
            "none",
            "unknown",
            "missing",
            "invalid_distribution",
        ):
            with self.subTest(scenario=scenario):

                def respond(body, scenario=scenario):
                    answers = span_reply(
                        body, "w1", "w2", 0.2 if scenario == "low_confidence" else 0.95
                    )
                    if scenario == "reversed":
                        answers = span_reply(body, "w2", "w1")
                    elif scenario == "none":
                        answers = span_reply(body, "none", "none")
                    elif scenario == "unknown":
                        answers["start"]["choice"] = "page-only-value"
                    elif scenario == "missing":
                        del answers["end"]
                    elif scenario == "invalid_distribution":
                        answers["end"]["probabilities"]["none"] = 1
                    return answers

                client = FakeClient(respond)
                if scenario in ("unknown", "missing", "invalid_distribution"):
                    with self.assertRaises(browser_dom.BrowserUseError) as caught:
                        await browser_goal.goal_text(client, "Search project settings", ACCOUNT, [])
                    self.assertEqual(caught.exception.code, "jev_invalid_response")
                else:
                    self.assertIsNone(
                        await browser_goal.goal_text(client, "Search project settings", ACCOUNT, [])
                    )
                self.assertEqual(len(client.calls), 1)

    async def test_source_token_bound_skips_inference(self):
        client = FakeClient(lambda body: span_reply(body, "w0", "w253"))
        result = await browser_goal.goal_text(client, " ".join(["x"] * 254), ACCOUNT, [])
        self.assertIsNotNone(result)
        self.assertEqual(len(client.calls[0]["questions"]["start"]["criteria"]), 255)
        client.calls.clear()
        for goal in (" ", " ".join(["x"] * 255)):
            self.assertIsNone(await browser_goal.goal_text(client, goal, ACCOUNT, []))
        self.assertEqual(client.calls, [])


@unittest.skipUnless(
    Path("/usr/bin/chromium-browser").is_file(), "Isolated Chromium is unavailable"
)
class IsolatedChromiumTests(BrowserTestCase):
    async def asyncSetUp(self):
        self.profile = tempfile.TemporaryDirectory(prefix="browser-use-test-")
        self.addCleanup(self.profile.cleanup)
        self.driver = await async_playwright().start()
        self.addAsyncCleanup(self.driver.stop)
        self.context = await self.driver.chromium.launch_persistent_context(
            self.profile.name,
            executable_path="/usr/bin/chromium-browser",
            headless=True,
            args=[
                "--remote-debugging-port=0",
                "--remote-debugging-address=127.0.0.1",
                "--disable-background-networking",
                "--no-proxy-server",
                "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost",
            ],
        )
        self.addAsyncCleanup(self.context.close)
        await self.context.route("**/*", lambda route: route.abort())
        await self.context.set_offline(True)
        self.page = self.context.pages[0]
        self.handles = []
        self.addAsyncCleanup(browser_dom.dispose_handles, self.handles)

    async def test_execute_real_cdp_selects_exact_target_and_keeps_browser_alive(self):
        other = self.page
        await other.set_content("<title>Unrelated tab</title><button>Do not touch</button>")
        shared = await self.context.new_page()
        await shared.set_content("<title>Shared tab</title><button>Selected target</button>")
        self.assertIs(self.context.pages[0], other)
        cdp = await self.context.new_cdp_session(shared)
        try:
            target_id = (await cdp.send("Target.getTargetInfo"))["targetInfo"]["targetId"]
        finally:
            await cdp.detach()
        port_file = Path(self.profile.name) / "DevToolsActivePort"
        async with asyncio.timeout(5):
            while not port_file.exists():
                await asyncio.sleep(0.01)
        port, path = port_file.read_text().splitlines()[:2]
        lease = SimpleNamespace(
            session_id="isolated-chromium",
            lease_id="isolated-lease",
            cdp_url=f"ws://127.0.0.1:{port}{path}",
            page_target_id=target_id,
            release=AsyncMock(),
        )
        ctx = SimpleNamespace(
            env={},
            browser=SimpleNamespace(acquire=AsyncMock(return_value=lease)),
            update=AsyncMock(),
        )
        # Deliberately do not inject connect: start/connect/close/stop are real.
        with patch.object(
            browser_use, "jev_client", side_effect=AssertionError("No provider is allowed")
        ):
            observed = await browser_use.execute_browser_use({"action": "observe"}, ctx)
            self.assertEqual(observed["data"]["status"], "observed", observed)
            self.assertEqual(observed["data"]["snapshot"]["title"], "Shared tab")
            self.assertEqual(
                [c["name"] for c in observed["data"]["snapshot"]["elements"]], ["Selected target"]
            )
            self.assertEqual(
                await shared.evaluate("6 * 7"), 42, "CDP cleanup must not close Chromium"
            )
            navigated = await browser_use.execute_browser_use(
                {"action": "navigate", "url": "about:blank"}, ctx
            )
        self.assertEqual(navigated["data"]["status"], "navigated", navigated)
        self.assertEqual(await shared.title(), "")
        self.assertEqual(await other.title(), "Unrelated tab")
        self.assertEqual(await other.locator("button").inner_text(), "Do not touch")
        self.assertFalse(shared.is_closed())
        self.assertEqual(len(self.context.pages), 2)
        self.assertEqual(await shared.evaluate("6 * 7"), 42)
        self.assertEqual(lease.release.await_count, 2)
        self.assertNotIn("cleanupWarning", observed["data"])
        self.assertNotIn("cleanupWarning", navigated["data"])

    async def test_dom_labels_context_hidden_labels_and_values(self):
        await self.page.set_content("""
            <section aria-label="Account"><button>Settings</button></section>
            <form aria-label="Project">
              <button>Settings<span style="display:none">hidden-secret</span></button>
              <label for="name">Project name</label><input id="name" value="input-secret">
              <label>Notes<textarea>textarea-secret</textarea></label>
              <div contenteditable="true" aria-label="Editor">editor-secret</div>
              <span id="hidden-label" hidden>Explicit hidden label</span>
              <input aria-labelledby="hidden-label" value="label-value-secret">
              <div role="combobox" aria-label="Plan"><span role="option">option-secret</span></div>
            </form>
        """)
        snapshot = await browser_dom.observe(self.page, self.handles)
        elements = snapshot["elements"]
        self.assertEqual(
            [(c["name"], c["context"]) for c in elements[:2]],
            [("Settings", "Account"), ("Settings", "Project")],
        )
        self.assertIn("Project name", [c["name"] for c in elements])
        self.assertIn("Explicit hidden label", [c["name"] for c in elements])
        self.assertIn("Plan", [c["name"] for c in elements])
        captured = json.dumps(snapshot)
        for secret in (
            "input-secret",
            "textarea-secret",
            "editor-secret",
            "label-value-secret",
            "hidden-secret",
            "option-secret",
            "identity",
        ):
            self.assertNotIn(secret, captured)
        target = next(item for item in self.handles if item.candidate["name"] == "Project name")
        await self.page.locator("label[for=name]").evaluate(
            "el => el.textContent = 'Changed label'"
        )
        with self.assertRaises(browser_dom.BrowserUseError) as caught:
            await browser_dom.validate_target(self.page, target, "fill")
        self.assertEqual(caught.exception.code, "stale_target")

    async def test_dom_redacts_normalized_long_reflections_before_truncation(self):
        for secret in (
            "private-first\n\tprivate-last",
            "private-prefix-" + "confidential " * 40 + "private-tail",
        ):
            with self.subTest(length=len(secret)):
                await self.page.set_content(
                    "<h1></h1><form><label for='name'></label><textarea id='name'></textarea><button></button><output></output></form>"
                )
                await self.page.evaluate(
                    """secret => {
                    document.title = `Saved ${secret}`;
                    for (const el of document.querySelectorAll('h1,label,button,output')) el.textContent = `Saved ${secret}`;
                    document.querySelector('form').setAttribute('aria-label', `Profile ${secret}`);
                    document.querySelector('textarea').value = secret;
                }""",
                    secret,
                )
                handles = []
                self.addAsyncCleanup(browser_dom.dispose_handles, handles)
                snapshot = await browser_dom.observe_goal(self.page, handles, {"profile": secret})
                self.assertEqual(snapshot["inputMatches"]["e1"], ["profile"])
                self.assertIn("Saved [input:profile]", snapshot["evidence"])
                public = {
                    key: value for key, value in snapshot.items() if key != "localValuesDigest"
                }
                for fragment in (
                    "private-first",
                    "private-last",
                    "private-prefix",
                    "confidential",
                    "private-tail",
                ):
                    self.assertNotIn(fragment, json.dumps(public))

    async def test_dom_freshness_covers_off_batch_fields_and_caps_total_fields(self):
        await self.page.set_content(
            '<label>Project name<input value="off-batch-secret"></label>'
            + "<button>Other</button>" * 79
            + "<button>Save</button>"
        )
        before = await browser_dom.observe_goal(
            self.page, self.handles, {}, {"controls": 80, "evidence": 0}
        )
        self.assertEqual([c["name"] for c in before["elements"]], ["Save"])
        await self.page.locator("input").fill("human-edit-secret")
        fresh_handles = []
        self.addAsyncCleanup(browser_dom.dispose_handles, fresh_handles)
        after = await browser_dom.observe_goal(
            self.page, fresh_handles, {}, {"controls": 80, "evidence": 0}
        )
        self.assertNotEqual(before["localValuesDigest"], after["localValuesDigest"])
        self.assertEqual(
            {k: v for k, v in before.items() if k != "localValuesDigest"},
            {k: v for k, v in after.items() if k != "localValuesDigest"},
        )
        await self.page.set_content('<input aria-label="Field">' * 513)
        capped_handles = []
        self.addAsyncCleanup(browser_dom.dispose_handles, capped_handles)
        with self.assertRaises(browser_dom.BrowserUseError) as caught:
            await browser_dom.observe_goal(self.page, capped_handles, {})
        self.assertEqual(caught.exception.code, "too_many_fields")


if __name__ == "__main__":
    unittest.main()
