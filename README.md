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
| [goal](./extensions/goal/kodelet-extension-goal) | `/goal`, `get_goal`, `update_goal` | Persistent conversation objectives with completion audits and automatic follow-up turns |
| [last-word](./extensions/last-word/kodelet-extension-last-word) | `/last-word`, `ctrl+alt+w` | Save the most recent completed agent response to a Markdown file |
| [look-at](./extensions/look-at/kodelet-extension-look-at) | `look_at` | Targeted analysis of local files, including PDFs, images, audio, video, and documents |
| [nano-banana](./extensions/nano-banana/kodelet-extension-nano-banana) | `nano_banana` | Generate images with Gemini Nano Banana and save them under `~/.cache/nano-banana` |
| [read-conversation](./extensions/read-conversation/kodelet-extension-read-conversation) | `read_conversation` | Read-only agentic research over saved conversation snapshots, with transcript-line evidence |
| [todo](./extensions/todo/kodelet-extension-todo) | `todo_read`, `todo_write` | Track conversation tasks with progress summaries, status checklists, and a live composer widget |
| [web-search](./extensions/web-search/kodelet-extension-web-search) | `web_search` | Web research with concise, source-linked answers |

`browser_use` requires a host with the shared-browser capability. Set `TYPESAFE_API_KEY` in the runner environment for semantic click/fill and goal execution; observe and navigate do not need it.

`/last-word` and `ctrl+alt+w` prompt for a workspace-relative path, defaulting to `last-word.md`. Use `/last-word path=notes/final.md` to skip the dialog; headless hosts also use the default when no path is supplied.

The runner needs `kodelet` on `PATH`, CLI daemon configuration (`KODELET_SERVER` or saved config), and client/API authentication (`KODELET_AUTH_TOKEN` or CLI auth config) for both history export and SDK session creation. A runner-only token is insufficient.

The durable subagent extension now lives in the standalone [`jingkaihe/kodelet-subagent`](https://github.com/jingkaihe/kodelet-subagent) repository. Install it separately with `uvx kodelet-subagent install`.

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
