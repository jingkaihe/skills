# Skills

A collection of CLI tool skill definitions for AI assistants.

## Available Skills

| Skill | Description |
|-------|-------------|
| [icloud-cli](./icloud-cli/SKILL.md) | Interact with iCloud calendar and email services - manage calendars, events (with recurrence), mailboxes, emails, and drafts |
| [waitrose-cli](./waitrose-cli/SKILL.md) | Interact with Waitrose & Partners grocery services - manage trolley, search products, book delivery slots, and view orders |

## Structure

Each skill is contained in its own directory with a `SKILL.md` file that provides:
- Metadata (name, description, trigger conditions)
- Prerequisites and setup instructions
- Command reference and usage examples
- Common workflows and troubleshooting tips

## Installation

```bash
kodelet skill add jingkaihe/skills -g
```

## Usage

These skill definitions are designed to be loaded by AI assistants to enable interaction with external CLI tools. Each `SKILL.md` follows a standard format with YAML frontmatter for metadata.
