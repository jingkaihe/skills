"""DOM observation and retained-target validation for the browser-use runner.

Descriptions never read control values. Goal observations read values only for
local freshness checks; callers must remove ``localValuesDigest`` before sending
a snapshot to inference, history, or tool output. Navigation freshness belongs to
the parent, which must check it before and after ``validate_target``.
"""

from __future__ import annotations

import hashlib
import re
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import ElementHandle, Page


ACTION_TIMEOUT = 5000
MAX_CANDIDATES = 80
MAX_ACTIONS = 200


class BrowserUseError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class ObservedElement:
    candidate: dict[str, Any]
    description: dict[str, Any]
    handle: ElementHandle
    # Local freshness only: never include in inference, history, or tool output.
    value_digest: str | None = None


# Each expression is self-contained: Playwright passes the element and the
# optional argument directly, without page globals or a JavaScript build step.
_DESCRIBE_ELEMENT = r"""
(element, inputs = {}) => {
  if (!element.isConnected) {
    return null;
  }

  const redactions = Object.entries(inputs)
    .map(([name, value]) => [name, value.replace(/\s+/g, " ").trim()])
    .filter(([, value]) => value)
    .sort(([, firstValue], [, secondValue]) => secondValue.length - firstValue.length);

  const clean = (text, limit) => {
    let normalized = (text ?? "").replace(/\s+/g, " ").trim();
    for (const [name, value] of redactions) {
      normalized = normalized.split(value).join(`[input:${name}]`);
    }
    return normalized.slice(0, limit);
  };

  const valueContents = [
    "input",
    "textarea",
    "select",
    "[contenteditable]",
    "[role~=combobox]",
    "[role~=listbox]",
    "[role~=option]",
    "[role~=textbox]",
    "[role~=searchbox]",
    "[role~=spinbutton]",
    "[role~=slider]",
    "[role~=scrollbar]",
    "[role~=progressbar]",
    "[role~=meter]",
  ].join(",");

  const textOf = (root, limit, explicitLabel = false) => {
    if (!root) {
      return "";
    }

    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        const parent = node.parentElement;
        if (!parent || parent.closest(`${valueContents},script,style`)) {
          return NodeFilter.FILTER_REJECT;
        }

        // Referenced accessible labels can intentionally be hidden. Incidental
        // body/context text must be rendered and must never include widget values.
        if (!explicitLabel) {
          if (parent.closest('[hidden],[aria-hidden="true"]')) {
            return NodeFilter.FILTER_REJECT;
          }
          const visibility = getComputedStyle(parent).visibility;
          if (visibility === "hidden" || visibility === "collapse") {
            return NodeFilter.FILTER_REJECT;
          }
          for (let ancestor = parent; ancestor; ancestor = ancestor.parentElement) {
            const style = getComputedStyle(ancestor);
            if (style.display === "none" || style.contentVisibility === "hidden") {
              return NodeFilter.FILTER_REJECT;
            }
          }
        }
        return NodeFilter.FILTER_ACCEPT;
      },
    });

    let text = "";
    let node;
    // Include enough lookahead to redact a reflected value before truncation.
    const readLimit = limit + (redactions[0]?.[1].length ?? 0);
    let nodeCount = 0;
    while (nodeCount < 100 && text.length < readLimit && (node = walker.nextNode())) {
      text = `${text} ${node.textContent ?? ""}`.replace(/\s+/g, " ").trim();
      nodeCount++;
    }

    // If the node budget prevents lookahead, omit text instead of returning a
    // potentially partial reflected value that full-value redaction cannot match.
    if (redactions.length && nodeCount === 100 && text.length < readLimit && walker.nextNode()) {
      return "";
    }
    return clean(text, limit);
  };

  const labelledBy = (node) => {
    const labelText = (node.getAttribute("aria-labelledby") ?? "")
      .split(/\s+/)
      .slice(0, 8)
      .map((id) => textOf(document.getElementById(id), 160, true))
      .join(" ");
    return clean(labelText, 160);
  };

  const tag = element.tagName.toLowerCase();
  const input = element instanceof HTMLInputElement ? element : undefined;
  const type = input?.type ?? "";
  const editable =
    element instanceof HTMLTextAreaElement ||
    (!!input && ["text", "email", "password", "search", "tel", "url", "number"].includes(type)) ||
    (element instanceof HTMLElement && element.isContentEditable);
  const control = element;
  const label = Array.from(control.labels ?? [])
    .slice(0, 4)
    .map((node) => textOf(node, 160))
    .join(" ");
  const name = clean(
    element.getAttribute("aria-label") ||
      labelledBy(element) ||
      label ||
      (!editable && textOf(element, 160)) ||
      element.getAttribute("placeholder") ||
      element.getAttribute("title") ||
      "",
    160,
  );

  const regionSelector = [
    "fieldset",
    "form",
    "section",
    "article",
    "nav",
    "li",
    "tr",
    "[role=dialog]",
    "[role=row]",
    "[role=group]",
    "[role=region]",
  ].join(",");
  const region = element.parentElement?.closest(regionSelector) ?? null;
  let context = "";
  if (region) {
    const regionName =
      region.getAttribute("aria-label") || labelledBy(region) || textOf(region, 240);
    context = clean(regionName, 240);
  }

  let nativeRole = tag;
  if (editable) {
    nativeRole = "textbox";
  } else if (tag === "a") {
    nativeRole = "link";
  } else if (tag === "select") {
    nativeRole = "combobox";
  } else if (["checkbox", "radio"].includes(type)) {
    nativeRole = type;
  } else if (["button", "submit", "reset"].includes(type)) {
    nativeRole = "button";
  }
  const role = clean(element.getAttribute("role") || nativeRole, 40);

  return {
    role,
    name,
    context,
    editable,
    enabled:
      !element.matches(":disabled") &&
      !element.closest('[aria-disabled="true"], [inert]') &&
      !(editable && control.readOnly),
    identity: {
      tag,
      id: element.id,
      type,
      href: element.getAttribute("href"),
      fieldName: element.getAttribute("name"),
    },
  };
}
"""

_CANDIDATE_SELECTOR = (
    ":is(button,a[href],input:not([type=hidden]),textarea,select,"
    "[role=button],[role=link],[role=checkbox],[role=radio],[role=tab],"
    "[role=menuitem],[role=combobox],[contenteditable=true]):visible"
)

_FIELD_VALUES = r"""
(elements) => {
  if (elements.length > 512) {
    return null;
  }
  // Serialize here to preserve JSON.stringify's exact Unicode representation
  // for the local digest. Neither values nor this string leave the runner.
  return JSON.stringify(elements.map((element) => [
    element.id,
    element.getAttribute("name"),
    "value" in element ? element.value : element.textContent,
  ]));
}
"""

_FIELD_FACTS = r"""
(element, values) => {
  const value = "value" in element ? element.value : (element.textContent ?? "");
  const matchingInputs = Object.entries(values)
    .filter(([, expected]) => value === expected)
    .map(([name]) => name);
  return {
    value,
    matches: matchingInputs,
    invalid: !!element.validity && !element.validity.valid,
  };
}
"""

# JavaScript /\s/ differs from Python's Unicode whitespace (notably BOM and NEL).
_JS_WHITESPACE = re.compile(
    r"[\u0009-\u000d\u0020\u00a0\u1680\u2000-\u200a"
    r"\u2028\u2029\u202f\u205f\u3000\ufeff]+"
)


def redact_input_values(text: str, inputs: dict[str, str]) -> str:
    """Normalize and redact longest reflected input values before truncation."""
    text = _JS_WHITESPACE.sub(" ", text).strip(" ")
    redactions = [
        (name, _JS_WHITESPACE.sub(" ", value).strip(" ")) for name, value in inputs.items()
    ]
    redactions.sort(
        key=lambda entry: len(entry[1].encode("utf-16-le", "surrogatepass")),
        reverse=True,
    )
    for name, value in redactions:
        if value:
            text = text.replace(value, f"[input:{name}]")
    return text


async def observe(
    page: Page,
    handles: list[ObservedElement],
    inputs: dict[str, str] | None = None,
    offset: int = 0,
    limit: int = MAX_CANDIDATES,
) -> dict[str, Any]:
    inputs = inputs if inputs is not None else {}
    locator = page.locator(_CANDIDATE_SELECTOR)
    count = await locator.count()
    last_batch_offset = max(0, ((count - 1) // limit) * limit)
    offset = min(offset, last_batch_offset)
    batch_end = min(count, offset + limit)

    for index in range(offset, batch_end):
        handle = await locator.nth(index).element_handle(timeout=1000)
        if handle is None:
            continue

        retained = False
        try:
            description = await handle.evaluate(_DESCRIBE_ELEMENT, inputs)
            if description is None:
                continue
            fields = {key: value for key, value in description.items() if key != "identity"}
            handles.append(
                ObservedElement(
                    candidate={"ref": f"e{index + 1}", **fields},
                    description=description,
                    handle=handle,
                )
            )
            retained = True
        finally:
            if not retained:
                with suppress(Exception):
                    await handle.dispose()

    title = redact_input_values(await page.title(), inputs)
    # JavaScript slice bounds count UTF-16 code units, not Python code points.
    title = title.encode("utf-16-le", "surrogatepass")[:320].decode("utf-16-le", "surrogatepass")
    return {
        "title": title,
        "frame": "main",
        "truncated": count > limit,
        "inventory": {"offset": offset, "total": count, "limit": limit},
        "elements": [dict(observed.candidate) for observed in handles],
    }


async def observe_goal(
    page: Page,
    handles: list[ObservedElement],
    inputs: dict[str, str],
    offsets: dict[str, int] | None = None,
) -> dict[str, Any]:
    offsets = offsets if offsets is not None else {"controls": 0, "evidence": 0}
    actions_per_control = len(inputs) + 2
    control_limit = min(MAX_CANDIDATES, (MAX_ACTIONS - 8) // actions_per_control)
    snapshot = await observe(page, handles, inputs, offsets["controls"], control_limit)
    inventory = snapshot.pop("inventory")

    # Freshness spans the whole form, not just the current control batch.
    field_values = await page.locator(
        "input:not([type=hidden]),textarea,[contenteditable=true]"
    ).evaluate_all(_FIELD_VALUES)
    if field_values is None:
        raise BrowserUseError(
            "too_many_fields",
            "The form exceeds the local freshness-check limit.",
        )
    local_values_digest = hashlib.sha256(field_values.encode("utf-8")).hexdigest()

    evidence: list[str] = []
    evidence_locator = page.locator("h1,h2,h3,[role=heading],[role=alert],[role=status],output")
    evidence_count = await evidence_locator.count()
    last_evidence_offset = max(0, ((evidence_count - 1) // 20) * 20)
    evidence_offset = min(offsets["evidence"], last_evidence_offset)
    evidence_end = min(evidence_count, evidence_offset + 20)

    for index in range(evidence_offset, evidence_end):
        handle = await evidence_locator.nth(index).element_handle(timeout=1000)
        if handle is None:
            continue
        try:
            if not await handle.is_visible():
                continue
            description = await handle.evaluate(_DESCRIBE_ELEMENT, inputs)
            if description and description["name"]:
                evidence.append(description["name"])
        finally:
            with suppress(Exception):
                await handle.dispose()

    input_matches: dict[str, list[str]] = {}
    invalid_fields: list[str] = []
    non_empty_fields: list[str] = []
    for observed in handles:
        candidate = observed.candidate
        if not candidate["editable"]:
            continue
        facts = await observed.handle.evaluate(_FIELD_FACTS, inputs)
        observed.value_digest = hashlib.sha256(
            facts["value"].encode("utf-16-le", "surrogatepass")
        ).hexdigest()
        if facts["value"]:
            non_empty_fields.append(candidate["ref"])
        input_matches[candidate["ref"]] = facts["matches"]
        if facts["invalid"]:
            invalid_fields.append(candidate["ref"])

    return {
        **snapshot,
        "truncated": snapshot["truncated"] or evidence_count > 20,
        "pagination": {
            "controls": inventory,
            "evidence": {"offset": evidence_offset, "total": evidence_count, "limit": 20},
        },
        "evidence": evidence,
        "inputMatches": input_matches,
        "invalidFields": invalid_fields,
        "nonEmptyFields": non_empty_fields,
        "localValuesDigest": local_values_digest,
    }


async def validate_target(
    page: Page,
    observed: ObservedElement,
    operation: str,
    inputs: dict[str, str] | None = None,
) -> None:
    """Validate the exact retained handle; the parent guards navigation freshness."""
    if page.is_closed():
        raise BrowserUseError(
            "stale_snapshot",
            "Navigation invalidated the observed target; no action was taken.",
        )
    current = await observed.handle.evaluate(
        _DESCRIBE_ELEMENT, inputs if inputs is not None else {}
    )
    editable = operation in ("fill", "fill_from_goal", "press")
    if (
        current is None
        or current != observed.description
        or not await observed.handle.is_visible()
        or not await observed.handle.is_enabled()
        or (editable and not await observed.handle.is_editable())
    ):
        raise BrowserUseError(
            "stale_target",
            "The selected element changed or is no longer actionable; no action was taken.",
        )


async def dispose_handles(handles: list[ObservedElement]) -> None:
    """Best-effort release of retained handles without swallowing cancellation."""
    for observed in handles:
        with suppress(Exception):
            await observed.handle.dispose()
