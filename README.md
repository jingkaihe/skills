# Skills

A collection of CLI tool skill definitions for AI assistants.

## Available Skills

| Skill | Description |
|-------|-------------|
| [ast-grep](./skills/ast-grep/SKILL.md) | Structural code search with ast-grep using AST-aware patterns |
| [google-workspace](./skills/google-workspace/SKILL.md) | Google Workspace MCP integration through maco - Gmail, Calendar, Contacts, and Drive |
| [librarian](./skills/librarian/SKILL.md) | Maintain a local cache of remote Git repositories for code research and exploration |
| [icloud-cli](./skills/icloud-cli/SKILL.md) | Manage iCloud calendars, events, and email via the CLI |
| [matchlock](./skills/matchlock/SKILL.md) | Run AI agents in ephemeral micro-VMs with VM-level isolation, network allowlisting, and secret injection - CLI, Go SDK, and Python SDK |
| [schedule](./skills/schedule/SKILL.md) | Manage scheduled, background agentic tasks |
| [screen-recording](./skills/screen-recording/SKILL.md) | Record terminal/browser demos with tmux, asciinema, Playwright or maco, lossless capture, FFmpeg, and visual playback checks |
| [strix-halo-llm](./skills/strix-halo-llm/SKILL.md) | Run, benchmark, and serve local GGUF models on AMD Strix Halo using kyuz0 toolboxes, memory estimation, and reproducible podman commands |
| [tmux](./skills/tmux/SKILL.md) | Run interactive CLIs and long-running tasks in isolated tmux sessions - manage session lifecycle, scrape pane output, and poll for patterns |
| [uv](./skills/uv/SKILL.md) | Astral uv usage for `uv run`, PEP 723 inline script dependencies, dependency compilation/locking, and `uv_build` backend setup |
| [waitrose-cli](./skills/waitrose-cli/SKILL.md) | Manage Waitrose grocery shopping - trolley, product search, delivery slots, and orders |

## Available Extensions

| Extension | Tools / Commands | Description |
|-----------|------------------|-------------|
| [browser-use](./extensions/browser-use/README.md) | `browser_use` | Goal-driven automation in the browser shared with the user |
| [code-search](./extensions/code-search/kodelet-extension-code-search) | `code_search` | Agentic codebase search for complex, multi-step code discovery tasks |
| [conversation](./extensions/conversation/kodelet-extension-conversation) | `read_conversation`, `search_conversation` | Search saved conversations for ranked IDs and highlighted excerpts, then extract relevant evidence with read-only agentic research |
| [goal](./extensions/goal/kodelet-extension-goal) | `/goal`, `get_goal`, `update_goal` | Persistent conversation objectives with completion audits and automatic follow-up turns |
| [last-word](./extensions/last-word/kodelet-extension-last-word) | `/last-word`, `ctrl+alt+w` | Save the most recent completed agent response to a Markdown file |
| [look-at](./extensions/look-at/kodelet-extension-look-at) | `look_at` | Targeted analysis of local files, including PDFs, images, audio, video, and documents |
| [nano-banana](./extensions/nano-banana/kodelet-extension-nano-banana) | `nano_banana` | Generate images with Gemini Nano Banana and save them under `~/.cache/nano-banana` |
| [todo](./extensions/todo/kodelet-extension-todo) | `todo_read`, `todo_write` | Track conversation tasks with progress summaries, status checklists, and a live composer widget |
| [web-search](./extensions/web-search/kodelet-extension-web-search) | `web_search` | Web research with concise, source-linked answers |

`browser_use` requires a host with the shared-browser capability. Set `TYPESAFE_API_KEY` in the runner environment for semantic click/fill and goal execution; observe and navigate do not need it.

`/last-word` and `ctrl+alt+w` prompt for a workspace-relative path, defaulting to `last-word.md`. Use `/last-word path=notes/final.md` to skip the dialog; headless hosts also use the default when no path is supplied.

The runner needs `kodelet` on `PATH`, CLI daemon configuration (`KODELET_SERVER` or saved config), and client/API authentication (`KODELET_AUTH_TOKEN` or CLI auth config) for history export, conversation search, and SDK session creation. A runner-only token is insufficient. `search_conversation` needs a kodelet release that includes `kodelet conversation search`; it runs one search without a model call and excludes the current conversation.

The `conversation` extension replaces the separate `read-conversation` and `search-conversation` extensions. Update any extension allow/deny entries to the new executable/directory path (or plugin ID `jingkaihe@skills/conversation`) and process configuration to `conversation`. Tool names and per-tool configuration are unchanged, as is the hidden `read-conversation` profile.

The durable subagent extension now lives in the standalone [`jingkaihe/kodelet-subagent`](https://github.com/jingkaihe/kodelet-subagent) repository. Install it separately with `uvx kodelet-subagent install`.

### Web search provider

Set `KODELET_WEB_SEARCH_PROVIDER` in the **runner environment** before the `web-search` extension starts:

| Value | Backend |
|-------|---------|
| Unset or `anthropic` | Existing Anthropic subscription backend, `claude-haiku-4-5-20251001` |
| `openai` | OpenAI `gpt-6-luna` via Codex subscription authentication and the Responses API |

Only the exact values `anthropic` and `openai` are accepted. An explicitly empty value or any other value causes a clear startup error; there is no silent provider fallback. Restart the extension (or its runner) after changing the variable.

The OpenAI backend matches code-search's `reasoning_effort: none` and `openai.service_tier: fast`; Kodelet maps `fast` to upstream `priority`. It sets `openai.platform: codex`, `api_mode: responses`, and `enable_search: true`. Both backends register only the selected hidden `web-search` profile, use `max_tokens: 4096`, disable skills, and allow only their provider's native search tool (`anthropic_web_search` or `openai_web_search`), not filesystem or delegation tools. Child sessions run with code mode off.

Authenticate your Codex subscription on the selected **Kodelet daemon**, not in the extension process:

```bash
kodelet codex login
kodelet codex status
# Set in the environment that starts the runner:
export KODELET_WEB_SEARCH_PROVIDER=openai
```

These auth commands require administrator access to the configured daemon (`KODELET_SERVER` or saved CLI config); provider credentials remain on the daemon. OpenAI search requires an updated daemon that emits structured OpenAI citation data in ACP `kodelet/textData`. Update/restart the daemon and restart the extension when upgrading; older daemons that return only display text fail with `web_search returned no answer data`, rather than parsing prose or fabricating citations. The tool keeps the same query/context inputs and content/blocks output: OpenAI citations carry URL/title with `cited_text: null` when no excerpt is supplied, while Anthropic excerpts are preserved.

The extension's existing five-minute tool cap is unchanged (the child-agent timeout is 290 seconds). When called through `code_execute`, the **entire code-mode invocation has a 120-second deadline**, including all awaited tools; the earlier parent deadline can cancel web search before its own cap. Progress reporting and cancellation cleanup are unchanged.

## Structure

Each skill is contained in its own directory with a `SKILL.md` file that provides:
- Metadata (name, description, trigger conditions)
- Prerequisites and setup instructions
- Command reference and usage examples
- Common workflows and troubleshooting tips

Test extensions without provider calls: `uv run --script tests/test_extensions.py`.

## Installation

```bash
# Install this plugin repository globally
kodelet plugin add jingkaihe/skills -g

# Install locally for the current repo
kodelet plugin add jingkaihe/skills
```

## Usage

These skill definitions are designed to be loaded by AI assistants to enable interaction with external CLI tools. Each `SKILL.md` follows a standard format with YAML frontmatter for metadata.
