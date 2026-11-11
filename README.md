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
| [extension-config](./skills/extension-config/SKILL.md) | Configure search extension models and providers through `extensions.config` |
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
| [caffeinate](./extensions/caffeinate) | Lifecycle hooks only | Automatically prevent macOS idle sleep during agent runs, with independent assertions for concurrent sessions |
| [code-search](./extensions/code-search) | `code_search` | Find code by behaviour or concept |
| [conversation](./extensions/conversation) | `search_conversation`, `read_conversation` | Find past conversations and extract what's relevant |
| [last-word](./extensions/last-word) | `/last-word`, `ctrl+alt+w` | Save the last response to a Markdown file |
| [look-at](./extensions/look-at) | `look_at` | Analyse PDFs, images, audio, and video |
| [nano-banana](./extensions/nano-banana) | `nano_banana` | Generate images with Gemini |
| [profiles](./extensions/profiles) | `openai`, `anthropic` model profiles | OpenAI (`patch` tool mode) and Anthropic (`full` tool mode) profiles, without daemon profile configuration |
| [rush](./extensions/rush) | `rush` model profile | GLM-5.3 via Fireworks Responses API |
| [web-search](./extensions/web-search) | `web_search` | Web research with cited answers |

Extensions run with `uv` (Python 3.11+). Some need extra setup:

- `look-at`, `nano-banana`: Gemini credentials (`GEMINI_API_KEY`, or `GOOGLE_CLOUD_PROJECT` for Vertex AI).
- `profiles`: uses subscriptions; run `kodelet codex login` and `kodelet anthropic login` against the daemon.
- `rush`: set `FIREWORK_API_KEY` in the daemon's environment, then use `kodelet chat --profile rush` or `kodelet run --profile rush "query"`.
- `web-search`: OpenAI via Codex by default (`kodelet codex login`); use the [extension-config skill](./skills/extension-config/SKILL.md) to switch providers or models.
- `code-search`, `conversation`, `web-search`: `kodelet` on `PATH`, configured and authenticated against your daemon.

Looking for subagents? See [jingkaihe/kodelet-subagent](https://github.com/jingkaihe/kodelet-subagent).

### Search model profiles

`code-search`, `conversation`, and `web-search` accept inline model profiles under `extensions.config`. See the [extension-config skill](./skills/extension-config/SKILL.md) for configuration locations, examples, supported settings, and how to apply changes.

### Boneyard

The [boneyard](./boneyard/) contains archived skills and extensions, kept for reference and excluded from plugin installation.

## Development

```bash
uv run --script tests/test_extensions.py
uv run --script tests/test_browser_use.py
```

## License

[MIT](./LICENSE)
