# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "kodelet-sdk==0.5.5",
#   "typesafe-sdk==0.7.1",
#   "playwright==1.63.0",
# ]
# ///

"""Run with `uv run --script tests/test_browser_use.py`; no provider calls.

Goal policy tests inject observations rather than emulate the DOM. SDK requests
use httpx2.MockTransport. Chromium tests launch a temporary,
headless profile, block external requests, and never use the host's browser.
Only that optional class skips when /usr/bin/chromium-browser is unavailable.
"""

from __future__ import annotations

import asyncio
import copy
import inspect
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


def element(ref, name, *, editable=False):
    candidate = {
        "ref": ref,
        "name": name,
        "role": "textbox" if editable else "button",
        "context": "Project",
        "editable": editable,
        "enabled": True,
    }
    return browser_dom.ObservedElement(
        candidate=candidate,
        description={
            **candidate,
            "identity": {"tag": "input" if editable else "button", "type": "text"},
        },
        handle=SimpleNamespace(
            click=AsyncMock(), fill=AsyncMock(), press=AsyncMock(), dispose=AsyncMock()
        ),
    )


class Harness:
    """Fixture at the observation boundary; Chromium tests own DOM semantics."""

    def __init__(self, respond=target_reply, key="test-key"):
        self.elements = [element("e1", "Settings"), element("e2", "Save")]
        self.snapshot = {
            "title": "Fixture",
            "frame": "main",
            "truncated": False,
            "elements": [item.candidate for item in self.elements],
            "pagination": {
                "controls": {"offset": 0, "total": 2, "limit": 80},
                "evidence": {"offset": 0, "total": 1, "limit": 20},
            },
            "evidence": ["Project settings"],
            "inputMatches": {},
            "invalidFields": [],
            "nonEmptyFields": [],
            "localValuesDigest": "local-digest",
        }
        self.page = SimpleNamespace(
            url="http://localhost/private-fixture",
            is_closed=Mock(return_value=False),
            on=Mock(),
            remove_listener=Mock(),
            set_default_timeout=Mock(),
            set_default_navigation_timeout=Mock(),
            wait_for_load_state=AsyncMock(),
        )
        self.cdp = SimpleNamespace(
            send=AsyncMock(return_value={"targetInfo": {"targetId": "shared-page"}}),
            detach=AsyncMock(),
        )
        self.page.context = SimpleNamespace(new_cdp_session=AsyncMock(return_value=self.cdp))
        self.browser = SimpleNamespace(
            contexts=[SimpleNamespace(pages=[self.page])],
            close=AsyncMock(),
        )
        self.lease = SimpleNamespace(
            session_id="test-session",
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

        self.connect = AsyncMock(return_value=self.browser)
        self.validate = AsyncMock()

    async def observe(self, page, handles, *args):
        handles.extend(self.elements)
        return copy.deepcopy(self.snapshot)

    async def observe_single(self, page, handles):
        snapshot = await self.observe(page, handles)
        return {key: snapshot[key] for key in ("title", "frame", "truncated", "elements")}

    async def execute(self, input_dict=None):
        with (
            patch.object(browser_use, "observe", self.observe_single),
            patch.object(browser_goal, "observe_goal", self.observe),
            patch.object(browser_use, "validate_target", self.validate),
            patch.object(browser_goal, "validate_target", self.validate),
        ):
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
        self.assertEqual(h.page.on.call_count, h.page.remove_listener.call_count)
        self.assertNotIn(h.lease.session_id, browser_use._active_sessions)

    def assert_no_dispatch(self, h):
        for observed in h.elements:
            for operation in ("click", "fill", "press"):
                getattr(observed.handle, operation).assert_not_awaited()

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
                answer = choice(selected, probabilities, confidence, probabilities)
                client = FakeClient(lambda body, answer=answer: {"target": answer})
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

    def test_rounded_distributions_preserve_original_action_thresholds(self):
        for selected, other, accepted in (
            (0.75, 0.24, True),
            (0.75, 0.26, True),
            (0.74, 0.25, False),
        ):
            probabilities = {"target": selected, "none": other}
            with self.subTest(probabilities=probabilities):
                answer = browser_use.validate_choice(
                    choice("target", probabilities, probabilities=probabilities),
                    probabilities,
                )
                self.assertEqual(answer["probabilities"], probabilities)
                self.assertEqual(browser_use.confident(answer), accepted)

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
        self.assert_no_dispatch(h)
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
                        return {"targetInfo": {"targetId": "shared-page"}}

                    async def detach(stage=stage, entered=entered, allow=allow):
                        if stage == "detach":
                            entered.set()
                        await allow.wait()

                    if stage == "query":
                        h.cdp.send.side_effect = query
                    h.cdp.detach.side_effect = detach
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
                        self.assert_no_dispatch(h)
                        h.factory.assert_not_called()
                    finally:
                        allow.set()
                        await asyncio.wait_for(task, 2)
                        await asyncio.wait_for(
                            asyncio.gather(*list(browser_use._pending_cleanup)), 2
                        )
                        await asyncio.sleep(0)
                    h.cdp.detach.assert_awaited_once()
                    self.assertFalse(browser_use._pending_cleanup)

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

    async def test_selected_handle_receives_action_without_exposing_values(self):
        for action in ("click", "fill"):
            with self.subTest(action=action):
                h = Harness()
                h.elements[1].candidate["editable"] = True
                input_dict = {"action": action, "target": "Project settings"}
                if action == "fill":
                    input_dict.update(target="Project name", value="browser-only-secret")
                output = await h.execute(input_dict)
                self.assertEqual(output["data"]["status"], "acted", output)
                self.assertFalse(output["data"]["verified"])
                self.assertEqual(output["data"]["target"]["ref"], "e2")
                h.elements[0].handle.click.assert_not_awaited()
                h.elements[0].handle.fill.assert_not_awaited()
                if action == "fill":
                    h.elements[1].handle.fill.assert_awaited_once_with(
                        "browser-only-secret", timeout=5000
                    )
                else:
                    h.elements[1].handle.click.assert_awaited_once_with(
                        timeout=5000, no_wait_after=True
                    )
                h.validate.assert_awaited_once_with(h.page, h.elements[1], action)
                self.assert_private(h, output, "browser-only-secret")
                self.assert_cleaned(h)

    async def test_no_match_and_truncation_do_not_dispatch(self):
        for truncated in (False, True):
            h = Harness(lambda body: target_reply(body, "none"))
            h.snapshot["truncated"] = truncated
            output = await h.execute({"action": "click", "target": "Missing"})
            self.assertEqual(output["data"]["status"], "not_executed", output)
            if truncated:
                self.assertEqual(output["data"]["reason"], "truncated_snapshot")
                self.assertEqual(h.client.calls, [])
            else:
                self.assertEqual(output["data"]["decision"]["reason"], "no_match")
            self.assert_no_dispatch(h)
            self.assert_cleaned(h)

    async def test_stale_target_or_navigation_prevents_dispatch(self):
        for mutation in ("navigation", "target"):
            with self.subTest(mutation=mutation):
                h = Harness()

                def respond(body, mutation=mutation, h=h):
                    if mutation == "navigation":
                        h.page.url = "http://localhost/changed"
                    else:
                        h.validate.side_effect = browser_dom.BrowserUseError(
                            "stale_target", "Target changed"
                        )
                    return target_reply(body)

                h.client.respond = respond
                output = await h.execute({"action": "click", "target": "Project settings"})
                self.assertEqual(
                    output["data"]["reason"],
                    "stale_snapshot" if mutation == "navigation" else "stale_target",
                )
                self.assert_no_dispatch(h)
                self.assert_cleaned(h)

    async def test_failures_keep_outcome_and_cleanup_distinct(self):
        for stage, status in (
            ("connect", "not_executed"),
            ("click", "outcome_unknown"),
            ("release", "acted"),
        ):
            with self.subTest(stage=stage):
                h = Harness()
                boundary = {
                    "connect": h.connect,
                    "click": h.elements[1].handle.click,
                    "release": h.lease.release,
                }[stage]
                boundary.side_effect = RuntimeError("private endpoint details")
                output = await h.execute({"action": "click", "target": "Save"})
                self.assertEqual(output["data"]["status"], status, output)
                self.assert_private(h, output, "private endpoint details")
                h.lease.release.assert_awaited_once()
                if stage == "connect":
                    self.assert_no_dispatch(h)
                else:
                    h.elements[1].handle.click.assert_awaited_once()
                    self.assertEqual(len(h.client.calls), 1)
                    self.assert_cleaned(h)
                self.assertEqual("cleanupWarning" in output["data"], stage == "release")

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


class GoalLoopTests(BrowserTestCase):
    async def test_fill_save_done_on_one_connection_with_private_values(self):
        secret = "browser-only-project-value"
        h = Harness()
        field, save = element("e1", "Project name", editable=True), element("e2", "Save")
        h.elements = [field, save]
        h.snapshot["elements"] = [item.candidate for item in h.elements]
        h.observe = AsyncMock(wraps=h.observe)

        def filled(value, **kwargs):
            h.snapshot.update(
                inputMatches={"e1": ["projectName"]},
                nonEmptyFields=["e1"],
                localValuesDigest="filled-digest",
            )

        def saved(**kwargs):
            # Equality and page-text redaction belong to the observation boundary.
            h.snapshot["evidence"] = ["Project saved: [input:projectName]"]

        field.handle.fill.side_effect = filled
        save.handle.click.side_effect = saved

        def respond(body):
            turn = len(h.client.calls)
            if turn == 1:
                selected = action_id(body, "fill", "Project name", "projectName")
            elif turn == 2:
                selected = action_id(body, "click", "Save")
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
        self.assertEqual((data["steps"], data["actionsCompleted"]), (3, 2))
        self.assertEqual(data["limits"], {"maxSteps": 12, "timeoutMs": 60000})
        self.assertEqual(
            data["evidence"]["fieldMatches"],
            [{"name": "Project name", "inputRefs": ["projectName"]}],
        )
        self.assertIn("Project saved: [input:projectName]", data["evidence"]["messages"])
        field.handle.fill.assert_awaited_once_with(secret, timeout=browser_dom.ACTION_TIMEOUT)
        save.handle.click.assert_awaited_once_with(
            timeout=browser_dom.ACTION_TIMEOUT, no_wait_after=True
        )
        self.assertEqual([item["operation"] for item in data["history"]], ["fill", "click"])
        self.assertEqual(h.client.calls[2]["state"]["recentActions"], data["history"])
        self.assertEqual(
            h.client.calls[1]["state"]["observation"]["inputMatches"], {"e1": ["projectName"]}
        )
        self.assertEqual(h.observe.await_count, 6, "Each decision needs a fresh observation")
        h.ctx.browser.acquire.assert_awaited_once()
        h.connect.assert_awaited_once()
        self.assert_private(h, output, secret)
        self.assert_cleaned(h)

    async def test_invalid_or_uncertain_mutations_abstain_but_inspection_is_allowed(self):
        for scenario, status, reason in (
            ("invalid", "failed", "jev_invalid_response"),
            ("status", "give_up", "uncertain"),
            ("action", "give_up", "no_suitable_action"),
            ("inspect", "step_limit", None),
        ):
            with self.subTest(scenario=scenario):

                def respond(body, scenario=scenario):
                    answers = goal_reply(body, action=action_id(body, "click"))
                    if scenario == "invalid":
                        answers["nextAction"]["choice"] = "t0_a1"
                    if scenario in ("status", "inspect"):
                        answers["status"]["confidence"] = 0.2
                    if scenario in ("action", "inspect"):
                        answers["nextAction"]["confidence"] = 0.2
                    return answers

                h = Harness(respond)
                if scenario == "inspect":
                    h.snapshot["pagination"]["controls"]["total"] = 81
                output = await h.execute({**GOAL, "maxSteps": 1})
                self.assertEqual(output["data"]["status"], status, output)
                self.assertEqual(output["data"].get("reason"), reason)
                operations = [item["operation"] for item in output["data"]["history"]]
                self.assertEqual(operations, ["inspect"] if scenario == "inspect" else [])
                self.assertEqual(len(h.client.calls), 1)
                self.assert_no_dispatch(h)
                self.assert_cleaned(h)

    async def test_done_requires_valid_confident_completion_evidence(self):
        for probability, messages, reason in (
            (None, ["Saved"], "jev_invalid_response"),
            (0.89, ["Saved"], "completion_unverified"),
            (1, [], "completion_unverified"),
        ):
            with self.subTest(probability=probability, messages=messages):
                h = Harness(
                    lambda body, probability=probability: goal_reply(
                        body, "done", evidence=probability
                    )
                )
                h.snapshot["evidence"] = messages
                output = await h.execute()
                self.assertEqual(output["data"]["reason"], reason, output)
                self.assertEqual(output["data"]["actionsCompleted"], 0)
                self.assertFalse(output["data"]["verified"])
                self.assert_no_dispatch(h)
                self.assert_cleaned(h)

    async def test_changed_completion_evidence_is_reobserved_not_accepted(self):
        h = Harness()

        def respond(body):
            if len(h.client.calls) == 1:
                h.snapshot["evidence"] = ["Login required"]
                return goal_reply(body, "done")
            return goal_reply(body, "give_up", blocker="missing_input")

        h.client.respond = respond
        output = await h.execute()
        self.assertEqual(output["data"]["reason"], "missing_input", output)
        self.assertEqual(len(h.client.calls), 2)
        self.assertEqual(h.client.calls[1]["state"]["observation"]["evidence"], ["Login required"])
        self.assert_no_dispatch(h)
        self.assert_cleaned(h)

    async def test_runner_and_caller_bounds_are_minimums_and_waits_consume_turns(self):
        for runner_limit, caller_limit in ((1, 9), (9, 1)):
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

    async def test_repeated_mutation_stops_without_progress(self):
        h = Harness(lambda body: goal_reply(body, action=action_id(body, "click")))
        output = await h.execute({**GOAL, "maxSteps": 5})
        self.assertEqual(output["data"]["reason"], "no_progress", output)
        self.assertEqual(output["data"]["actionsCompleted"], 1)
        self.assertEqual(len(h.client.calls), 2)
        h.elements[0].handle.click.assert_awaited_once()
        self.assert_cleaned(h)

    async def test_later_stale_action_or_api_failure_retains_completed_history(self):
        for failure, reason in (
            ("stale_action", "jev_invalid_response"),
            ("api", "jev_unavailable"),
        ):
            with self.subTest(failure=failure):
                h = Harness()

                def respond(body, h=h, failure=failure):
                    if len(h.client.calls) > 1 and failure == "api":
                        raise RuntimeError("private provider error")
                    return goal_reply(body, action=action_id(h.client.calls[0], "click"))

                h.client.respond = respond
                output = await h.execute()
                data = output["data"]
                self.assertEqual(data["status"], "failed", output)
                self.assertEqual(data["reason"], reason)
                self.assertEqual(data["actionsCompleted"], 1)
                self.assertEqual(data["history"][0]["operation"], "click")
                self.assertEqual(h.client.calls[1]["state"]["recentActions"], data["history"])
                h.elements[0].handle.click.assert_awaited_once()
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
                    h.elements[0].handle.click.side_effect = block
                task = asyncio.create_task(h.execute())
                try:
                    await asyncio.wait_for(started.wait(), 1)
                    task.cancel()
                    output = await asyncio.wait_for(task, 2)
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                self.assertEqual(
                    output["data"]["status"],
                    "outcome_unknown" if stage == "action" else "canceled",
                    output,
                )
                self.assertEqual(h.elements[0].handle.click.await_count, int(stage == "action"))
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
        self.assert_no_dispatch(h)
        self.assert_cleaned(h)

    async def test_extraction_revalidates_after_model_completion(self):
        for mutation, reason in (("target", "stale_target"), ("values", "stale_snapshot")):
            with self.subTest(mutation=mutation):
                h = Harness()
                field = element("e1", "Search", editable=True)
                h.elements = [field]
                h.snapshot["elements"] = [field.candidate]

                def respond(body, h=h, mutation=mutation):
                    if "start" not in body["questions"]:
                        return goal_reply(body, action=action_id(body, "fill_from_goal", "Search"))
                    h.validate.assert_not_awaited()
                    if mutation == "target":
                        h.validate.side_effect = browser_dom.BrowserUseError(
                            "stale_target", "Replaced"
                        )
                    else:
                        h.snapshot["localValuesDigest"] = "human-edited-value-digest"
                    return span_reply(body, "w1", "w2")

                h.client.respond = respond
                output = await h.execute({**GOAL, "goal": "Find Ada Lovelace"})
                self.assertEqual(output["data"]["reason"], reason, output)
                self.assertEqual(output["data"]["actionsCompleted"], 0)
                self.assertEqual(len(h.client.calls), 2)
                self.assertEqual(h.validate.await_count, int(mutation == "target"))
                self.assert_no_dispatch(h)
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

    async def test_goal_inspects_large_page_copies_search_and_submits(self):
        await self.page.set_content(
            "<button>Other</button>" * 80
            + '<form><input aria-label="Search" type="search"></form>'
            + "<h2>Other heading</h2>" * 20
            + "<output></output>"
        )
        await self.page.evaluate("""() => {
            document.querySelector('form').addEventListener('submit', event => {
                event.preventDefault();
                document.querySelector('output').textContent = 'C++ results';
            });
        }""")

        def respond(body):
            if "start" in body["questions"]:
                return span_reply(body, "w2", "w4")
            observation = body["state"]["observation"]
            if observation["pagination"]["controls"]["offset"] == 0:
                selected = action_id(body, "inspect", section="controls", offset=80)
            elif not observation["nonEmptyFields"]:
                selected = action_id(body, "fill_from_goal", "Search")
            elif not any(
                action["operation"] == "press" for action in body["state"]["recentActions"]
            ):
                selected = action_id(body, "press", "Search")
            elif observation["pagination"]["evidence"]["offset"] == 0:
                selected = action_id(body, "inspect", section="evidence", offset=20)
            else:
                return goal_reply(body, "done")
            return goal_reply(body, action=selected)

        client = FakeClient(respond)
        async with asyncio.timeout(10) as deadline:
            output = await browser_goal.run_goal(
                self.page,
                {**GOAL, "goal": "Search for C++", "successCriteria": "C++ results are visible"},
                SimpleNamespace(update=AsyncMock()),
                client,
                {"maxSteps": 8, "timeoutMs": 10000},
                deadline,
            )
        self.assertEqual(output["data"]["status"], "done", output)
        self.assertEqual(await self.page.locator("input").input_value(), "C++")
        self.assertEqual(await self.page.locator("output").inner_text(), "C++ results")
        self.assertEqual(
            [entry["operation"] for entry in output["data"]["history"]],
            ["inspect", "fill_from_goal", "press", "inspect"],
        )
        self.assertEqual(output["data"]["history"][1]["sourceSpan"], {"start": 11, "end": 14})

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
