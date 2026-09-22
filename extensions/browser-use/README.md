# Browser use

A Python port of Kodelet's TypeScript browser-use extension. It operates the conversation's existing shared browser through the published `kodelet-sdk` browser capability, async Playwright, and the official `typesafe-sdk`. The control plane authorizes and returns a runner-local CDP lease; observations, Jev decisions, and browser actions stay inside the extension. No browser-action RPC or Node project build is required.

## Setup

Install this plugin repository with `kodelet plugin add jingkaihe/skills`. The executable `kodelet-extension-browser-use` uses `uv run --script` to resolve its Python dependencies. Python 3.11 or later and `uv` must be available on the runner. All extensions in this repository now require `kodelet-sdk>=0.5.5,<0.6`; this extension also pins `typesafe-sdk==0.7.1` and `playwright==1.63.0`.

Use a Kodelet host advertising browser capability version 1. Configure Chrome on the runner and enable browser access on the control-plane host, rather than in repository model-profile overrides:

```yaml
browser:
  executable: /usr/bin/chromium-browser
serve:
  browser_enabled: true
```

The caller needs the host's browser permission (`terminal` or `admin`). Export `TYPESAFE_API_KEY` in the runner environment before starting or restarting the runner. `observe` and `navigate` work without it; `click`, `fill`, and `run` reject missing credentials before browser acquisition. Do not put keys into tool arguments or files. Do not run `playwright install`: the extension connects to the host-owned Chrome and never launches its own browser during tool execution.

Runner-local installed tools are required; inline SDK callbacks on another machine cannot use this endpoint. Disable the workspace TypeScript prototype if it is also installed, so two extensions do not compete to register `browser_use`.

## Tool interface

The tool retains the TypeScript version's action names and camelCase argument/result fields:

```json
{"action":"navigate","url":"http://localhost:3000"}
{"action":"observe"}
{"action":"click","target":"Settings for the current project, not my account"}
{"action":"fill","target":"Project name","value":"Example project"}
```

`navigate` accepts HTTP/HTTPS without URL credentials, or `about:blank`, and waits for DOMContentLoaded—not application readiness. `observe` returns a main-frame inventory with title, labels, roles, context, editability, and enabled state, without form values. Single-step snapshots contain at most 80 controls. Semantic click/fill abstain on truncated snapshots, ambiguous matches, or stale targets; use `run` to work through large pages. Element references describe one observation, not persistent selectors.

Prefer a goal for multi-step work:

```json
{
  "action": "run",
  "goal": "Rename the current project using projectName, save changes, and verify the saved value. Do not modify the account.",
  "successCriteria": "The confirmation page reports Project saved and its read-back Project name field matches projectName.",
  "inputs": {"projectName": "Website refresh"},
  "maxSteps": 8,
  "timeoutMs": 45000
}
```

Navigate first if needed. `goal` and `successCriteria` are required. Up to eight named inputs are supported; names start with a letter and contain only letters, digits, or underscores, up to 64 characters. Names and descriptions must not contain secrets. Literal input values stay local for filling and equality comparisons.

Without named inputs, the loop can copy a value already stated in the goal:

```json
{
  "action": "run",
  "goal": "Find and open the English Wikipedia article titled Russian Civil War using Wikipedia search.",
  "successCriteria": "The Russian Civil War article is open and its heading is visible."
}
```

Jev selects start/end tokens; code copies the exact original substring, including meaningful punctuation such as `C++`, `.NET`, or `-10`. It never generates arbitrary text or copies instructions from page content. This fallback supports at most 254 goal tokens; use named inputs for longer goals. `sourceSpan` uses UTF-16 character offsets, matching the TypeScript contract.

## Goal loop and bounds

Each turn observes the page, constructs concrete actions, and submits one batched SDK request with status (`act`, `done`, `give_up`), next-action, blocker, and completion-evidence questions. Action IDs belong only to that turn. Code executes the chosen action after revalidating the observation and retained handle, then observes again. Jev does not generate JavaScript, selectors, URLs, or a free-form plan.

Supported candidates are click, named-input fill, exact-goal-text fill, Enter on nonempty native inputs, bounded wait, reload, back, and inspection of another control/evidence batch. Goal mode omits password entry and toggle/select operations whose state it cannot observe. A partial inventory is not a failure: unseen batches remain inspectable, including when the model cannot confidently select a mutation. Explicit `give_up` always stops.

Each batch contains at most 80 controls, 20 heading/status/evidence nodes, and 200 actions. Control batches shrink as named inputs increase the action combinations. Local freshness checks include fields outside the current batch, capped at 512 fields. Navigation during the initial DOM read can trigger a fresh observation; navigation during a decision, changed values, or invalidated handles prevent dispatch. Previously dispatched actions are not automatically retried.

The model remains pinned to `jev-1.13.0`. Mutation and completion decisions require Choice confidence at least 0.7 and selected probability at least 0.75; mutations require the same action-selection thresholds. Read-only inspection and waiting can gather evidence despite an uncertain preference. Completion additionally requires observed evidence, a Noul probability at least 0.9, and a fresh unchanged observation. Small rounded probability-sum differences are tolerated without normalizing away the action thresholds. These prototype thresholds are not calibrated correctness or authorization guarantees.

Runner environment variables impose hard limits:

| Variable | Default | Allowed range |
| --- | --- | --- |
| `KODELET_BROWSER_USE_MAX_TURNS` | 12 | 1–100 |
| `KODELET_BROWSER_USE_TIMEOUT_MS` | 60000 | 1–300000 milliseconds |

Invalid settings fail before acquisition. Per-call `maxSteps` and `timeoutMs` only lower these limits. Observation/decision cycles, waiting, inspection, and the final completion decision all consume turns. The deadline includes acquisition, connection, progress updates, and inference; cleanup gets up to one additional second. Single-step calls have a 30-second deadline. Browser operations have 5-second limits, SDK requests have an 8-second timeout, and SDK retries are disabled. Python task cancellation stops the loop and cleans up its clients and lease.

## Outcomes and browser lifetime

Single-step results distinguish `observed`, `navigated`, `acted`, `not_executed`, and `outcome_unknown`. A successful click/fill is not proof of application success. An unknown outcome must be inspected, not automatically retried.

Goal results include `data.status`, `steps`, `actionsCompleted`, `history`, effective `limits`, the last `decision`, and the last `observation` when available:

| Status | Meaning |
| --- | --- |
| `done` | Fresh evidence supports completion according to the model; includes `verification: "model"` and `verified: false`. |
| `give_up` | A model decision or code guard stopped progress, with a reason such as missing input, uncertainty, or no progress. |
| `step_limit` | The turn budget was exhausted. |
| `timeout` / `canceled` | The deadline or caller stopped the workflow outside an in-flight mutation. |
| `failed` | A browser, inference, or validation error stopped execution. |
| `outcome_unknown` | An in-flight operation may have taken effect without finishing cleanly. |

Setup failures can return `not_executed`. Earlier actions are never rolled back. Completion is a model judgment, not independent application verification, and can conservatively fail even after the intended application effect succeeded. Do not repeat consequential actions just to obtain a positive model assessment.

The extension acquires one lease per invocation and resolves the exact host-supplied `page_target_id`, never the first tab. It disconnects Playwright and releases the lease without closing Chrome or the shared page. Late resources are disposed after cancellation. A failed release acknowledgement is reported as `cleanupWarning`; the host also releases invocation-scoped leases. Concurrent calls within this extension process cannot operate on the same session, but humans and other clients can still change the page—there is no exclusive ownership or atomic transaction.

## Data handling and limitations

Semantic actions send the requested target and bounded page metadata to TypeSafe. Goal mode additionally sends the goal, success criteria, input names, heading/status evidence, local equality/nonempty/validity flags, action candidates, and recent history. Raw form values, local freshness digests, cookies, storage, CDP endpoints, and local identity fields do not enter inference requests or tool results. Reflected named values are redacted on a best-effort basis before truncation; transformed or encoded reflections can still escape. This is data minimization, not a confidentiality guarantee against hostile pages.

Raw CDP is broad browser control for trusted extensions, not a sandbox or page-specific permission boundary. The goal must authorize consequential actions. Jev is not an authorization or prompt-injection boundary. The tool does not accept arbitrary code, CSS selectors, CDP endpoints, or other conversation IDs.

Supported observations are main-frame semantic HTML, not a full accessibility tree. Iframes, uploads/downloads, dialogs, canvas-only controls, closed shadow roots, tab switching, and comprehensive form workflows require handing control back to the caller or human.

## Development and verification

The Python implementation is separated into `browser_use.py` (registration, SDK integration, lifetime, single actions), `browser_dom.py` (bounded observations and retained-target validation), and `browser_goal.py` (action candidates and goal loop). Browser-side JavaScript is embedded locally; runtime use requires no TypeScript compilation.

```sh
# From this repository's root; no provider calls.
uv run --script tests/test_extensions.py
uv run --script tests/test_browser_use.py
extensions/browser-use/kodelet-extension-browser-use </dev/null

# Static checks for the port.
uv run --with ruff -- ruff check extensions/browser-use tests/test_browser_use.py
uv run --with ruff -- ruff format --line-length 100 --check extensions/browser-use tests/test_browser_use.py
```

Tests use mocked model responses and browser fixtures, never the conversation's existing browser. Browser-backed fixtures use a separate Chromium process when available; they make no paid model requests. Live Jev quality and production-host authorization remain separate integration concerns.
