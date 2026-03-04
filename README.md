# Skills

A collection of CLI tool skill definitions for AI assistants.

## Available Skills

| Skill | Description |
|-------|-------------|
| [google-workspace](./skills/google-workspace/SKILL.md) | Google Workspace MCP integration - Gmail search, email management, attachments, and contacts |
| [librarian](./skills/librarian/SKILL.md) | Maintain a local cache of remote Git repositories for code research and exploration |
| [icloud-cli](./skills/icloud-cli/SKILL.md) | Manage iCloud calendars, events, and email via the CLI |
| [matchlock](./skills/matchlock/SKILL.md) | Run AI agents in ephemeral micro-VMs with VM-level isolation, network allowlisting, and secret injection - CLI, Go SDK, and Python SDK |
| [tmux](./skills/tmux/SKILL.md) | Run interactive CLIs and long-running tasks in isolated tmux sessions - manage session lifecycle, scrape pane output, and poll for patterns |
| [waitrose-cli](./skills/waitrose-cli/SKILL.md) | Manage Waitrose grocery shopping - trolley, product search, delivery slots, and orders |

## Available Recipes

| Recipe | Description |
|--------|-------------|
| [code/architect](./recipes/code/architect.md) | Analyzes codebase patterns and designs architectural solutions with implementation blueprints |
| [code/explorer](./recipes/code/explorer.md) | Explores and explains a codebase to help understand how it works |
| [code/reviewer](./recipes/code/reviewer.md) | Performs a comprehensive code review of changes with focus on quality, security, and best practices |
| [ralph/init](./recipes/ralph/init.md) | Generate a PRD (Product Requirements Document) based on discussion and design docs for autonomous development |
| [ralph/iterate](./recipes/ralph/iterate.md) | Iteratively work through a feature list, implementing one feature at a time with progress tracking |

## Structure

Each skill is contained in its own directory with a `SKILL.md` file that provides:
- Metadata (name, description, trigger conditions)
- Prerequisites and setup instructions
- Command reference and usage examples
- Common workflows and troubleshooting tips

## Installation

```bash
# Install all skills to the global kodelet skills directory
kodelet skill add jingkaihe/skills -g

# Install a specific skill to the global kodelet skills directory
kodelet skill add jingkaihe/skills -d waitrose-cli -g
```

## Usage

These skill definitions are designed to be loaded by AI assistants to enable interaction with external CLI tools. Each `SKILL.md` follows a standard format with YAML frontmatter for metadata.
