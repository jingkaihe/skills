# Skills

A [Kodelet](https://github.com/jingkaihe/kodelet) plugin with agent skills and extensions.

## Install

```bash
kodelet plugin add jingkaihe/skills -g   # global
kodelet plugin add jingkaihe/skills      # current repo only
```

## Skills

| Skill | Description |
|-------|-------------|
| [cloudflare-tunnel](./skills/cloudflare-tunnel/SKILL.md) | Expose local services with Cloudflare Tunnel |
| [google-workspace](./skills/google-workspace/SKILL.md) | Gmail, Calendar, Contacts, and Drive via the google-workspace MCP server |
| [icloud-cli](./skills/icloud-cli/SKILL.md) | iCloud calendars, events, and email |
| [librarian](./skills/librarian/SKILL.md) | Local cache of remote Git repositories for code research |
| [matchlock](./skills/matchlock/SKILL.md) | Run agents and code in isolated micro-VMs |
| [screen-recording](./skills/screen-recording/SKILL.md) | Record terminal and browser demos, including on headless Linux |
| [strix-halo-llm](./skills/strix-halo-llm/SKILL.md) | Run and benchmark local llama.cpp models on AMD Strix Halo |
| [tmux](./skills/tmux/SKILL.md) | Run interactive CLIs and long-running tasks in tmux sessions |
| [uv](./skills/uv/SKILL.md) | Python packages, environments, and scripts with uv |
| [waitrose-cli](./skills/waitrose-cli/SKILL.md) | Waitrose grocery shopping |

## Extensions

| Extension | Provides | Description |
|-----------|----------|-------------|
| [browser-use](./extensions/browser-use/README.md) | `browser_use` | Goal-driven automation in the shared browser |
| [code-search](./extensions/code-search) | `code_search` | Find code by behaviour or concept |
| [conversation](./extensions/conversation) | `search_conversation`, `read_conversation` | Find past conversations and extract what's relevant |
| [goal](./extensions/goal) | `/goal`, `get_goal`, `update_goal` | Keep working toward a goal until it's done |
| [last-word](./extensions/last-word) | `/last-word`, `ctrl+alt+w` | Save the last response to a Markdown file |
| [look-at](./extensions/look-at) | `look_at` | Analyse PDFs, images, audio, and video |
| [nano-banana](./extensions/nano-banana) | `nano_banana` | Generate images with Gemini |
| [todo](./extensions/todo) | `todo_read`, `todo_write` | Track tasks with a live progress widget |
| [web-search](./extensions/web-search) | `web_search` | Web research with cited answers |

Extensions run with `uv` (Python 3.11+). Some need extra setup:

- **browser-use**: a Kodelet host with browser access enabled; `TYPESAFE_API_KEY` for `click`, `fill`, and `run`.
- **look-at**, **nano-banana**: Gemini credentials (`GEMINI_API_KEY`, or `GOOGLE_CLOUD_PROJECT` for Vertex AI).
- **web-search**: OpenAI via Codex by default (`kodelet codex login`); set `KODELET_WEB_SEARCH_PROVIDER=anthropic` to switch.
- **code-search**, **conversation**, **web-search**: `kodelet` on `PATH`, configured and authenticated against your daemon.

Looking for subagents? See [jingkaihe/kodelet-subagent](https://github.com/jingkaihe/kodelet-subagent).

## Development

```bash
uv run --script tests/test_extensions.py
uv run --script tests/test_browser_use.py
```

## License

[MIT](./LICENSE)
