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
| [strix-halo-llm](./skills/strix-halo-llm/SKILL.md) | Run, benchmark, and serve local GGUF models on AMD Strix Halo using kyuz0 toolboxes, memory estimation, and reproducible podman commands |
| [tmux](./skills/tmux/SKILL.md) | Run interactive CLIs and long-running tasks in isolated tmux sessions - manage session lifecycle, scrape pane output, and poll for patterns |
| [uv](./skills/uv/SKILL.md) | Astral uv usage for `uv run`, PEP 723 inline script dependencies, dependency compilation/locking, and `uv_build` backend setup |
| [waitrose-cli](./skills/waitrose-cli/SKILL.md) | Manage Waitrose grocery shopping - trolley, product search, delivery slots, and orders |

## Available Recipes

| Recipe | Description |
|--------|-------------|
| [code/architect](./recipes/code/architect.md) | Analyzes codebase patterns and designs architectural solutions with implementation blueprints |
| [code/reviewer](./recipes/code/reviewer.md) | Performs a comprehensive code review of changes with focus on quality, security, and best practices |
| [ralph/init](./recipes/ralph/init.md) | Generate a PRD (Product Requirements Document) based on discussion and design docs for autonomous development |
| [ralph/iterate](./recipes/ralph/iterate.md) | Iteratively work through a feature list, implementing one feature at a time with progress tracking |

## Available Extensions

| Extension | Tools / Commands | Description |
|-----------|------------------|-------------|
| [code-search](./extensions/code-search/kodelet-extension-code-search) | `code_search` | Agentic codebase search for complex, multi-step code discovery tasks |
| [last-word](./extensions/last-word/kodelet-extension-last-word) | `/last-word`, `ctrl+alt+w` | Save the most recent completed agent response to a Markdown file |
| [look-at](./extensions/look-at/kodelet-extension-look-at) | `look_at` | Targeted analysis of local files, including PDFs, images, audio, video, and documents |
| [nano-banana](./extensions/nano-banana/kodelet-extension-nano-banana) | `nano_banana` | Generate images with Gemini Nano Banana and save them under `~/.cache/nano-banana` |
| [subagent](./extensions/subagent/kodelet-extension-subagent) | `spawn_agent`, `wait_agent`, `list_agents`, `followup_agent`, `steer_agent`, `cancel_agent` | Run durable background Kodelet agents |

`/last-word` and `ctrl+alt+w` prompt for a workspace-relative path, defaulting to `last-word.md`. Use `/last-word path=notes/final.md` to skip the dialog; headless hosts also use the default when no path is supplied.

The subagent extension lets the main agent start up to three independent agents and continue working while they run, with an extension-wide limit of eight active agents. `spawn_agent` returns both an agent ID and run ID immediately; use `wait_agent` with that run ID to collect the exact run result, `list_agents` to inspect durable state, `steer_agent` to inject guidance into a running child, `followup_agent` to wake an idle, failed, or interrupted child, and `cancel_agent` to permanently stop one. Child agents cannot spawn or manage other agents.

On hosts that advertise persistent widgets, the extension publishes a conversation-scoped background-agent status panel above the composer. It remains visible after the parent turn completes, updates as child runs transition, and is reconstructed from the extension's SQLite state on a later agent lifecycle event.

`spawn_agent` defaults to `context_mode: "fork"`, which copies the main agent's live conversation into an isolated child. Use `context_mode: "fresh"` when the child should start with no parent conversation memory. A requested fork fails rather than silently falling back to fresh context.

Agent identity, child conversation IDs, run history, results, errors, pending steering messages, and lease state are persisted in the extension-owned `<dataDir>/subagents.sqlite` database and scoped to the parent conversation. Kodelet supplies only the extension data directory, a generic background-runtime capability, and the ACP `_session/steering` extension; it does not own the subagent schema or lifecycle. Once ACP reports a steering message as `injected`, unconsumed guidance remains on the child conversation and may be applied during a later follow-up. If the active turn closes before injection, the extension keeps the message and moves it to the next `followup_agent` run. The extension process keeps only live tasks and clients in memory. A clean extension shutdown marks active runs interrupted immediately; an unclean loss is detected when the 60-second worker lease expires. `followup_agent` starts a new fenced run that resumes the persisted child conversation. If interruption happened before a child was attached, fresh mode starts another blank session and fork mode creates a new fork of the owning parent rather than silently falling back to blank memory.

This MVP is enabled only for local extension runtimes. Runner-backed Kodelet executions currently hide these tools because their extension process and workspace are torn down with the parent run; durable runner workers require a separate runner-level supervisor.

The previous synchronous `subagent` tool remains removed; its behavior is replaced by calling `spawn_agent` followed by `wait_agent`.

## Structure

Each skill is contained in its own directory with a `SKILL.md` file that provides:
- Metadata (name, description, trigger conditions)
- Prerequisites and setup instructions
- Command reference and usage examples
- Common workflows and troubleshooting tips

Extensions live under `extensions/` as executable `kodelet-extension-*` Python SDK scripts with inline `uv` dependency metadata.

## Installation

```bash
# Install this plugin repository globally
kodelet plugin add jingkaihe/skills -g

# Install locally for the current repo
kodelet plugin add jingkaihe/skills
```

## Usage

These skill definitions are designed to be loaded by AI assistants to enable interaction with external CLI tools. Each `SKILL.md` follows a standard format with YAML frontmatter for metadata.
