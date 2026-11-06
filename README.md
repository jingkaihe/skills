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
| [caffeinate](./extensions/caffeinate) | Lifecycle hooks only | Automatically prevent macOS idle sleep during agent runs, with independent assertions for concurrent sessions |
| [code-search](./extensions/code-search) | `code_search` | Find code by behaviour or concept |
| [conversation](./extensions/conversation) | `search_conversation`, `read_conversation` | Find past conversations and extract what's relevant |
| [last-word](./extensions/last-word) | `/last-word`, `ctrl+alt+w` | Save the last response to a Markdown file |
| [look-at](./extensions/look-at) | `look_at` | Analyse PDFs, images, audio, and video |
| [nano-banana](./extensions/nano-banana) | `nano_banana` | Generate images with Gemini |
| [web-search](./extensions/web-search) | `web_search` | Web research with cited answers |

Extensions run with `uv` (Python 3.11+). Some need extra setup:

- `look-at`, `nano-banana`: Gemini credentials (`GEMINI_API_KEY`, or `GOOGLE_CLOUD_PROJECT` for Vertex AI).
- `web-search`: OpenAI via Codex by default (`kodelet codex login`); set `KODELET_WEB_SEARCH_PROVIDER=anthropic` to switch.
- `code-search`, `conversation`, `web-search`: `kodelet` on `PATH`, configured and authenticated against your daemon.

Looking for subagents? See [jingkaihe/kodelet-subagent](https://github.com/jingkaihe/kodelet-subagent).

### Search model profiles

`code-search` and `conversation` accept an inline model profile under `extensions.config`, keyed by their full installed extension IDs:

```yaml
extensions:
  config:
    jingkaihe@skills/code-search:
      profile:
        provider: openai
        model: gpt-6-luna
        reasoning_effort: none
        openai:
          api_mode: responses
          platform: codex
          service_tier: fast
    jingkaihe@skills/conversation:
      profile:
        provider: anthropic
        model: claude-haiku-5-5
        anthropic_api_access: subscription
        max_tokens: 8192
```

The optional `profile` object uses Kodelet's native profile keys and must include `provider` (`openai` or `anthropic`) and a nonempty `model`. It replaces the built-in model settings rather than merging with them; omitted settings use Kodelet's defaults. Omit `profile` entirely to keep the current OpenAI/Codex `gpt-6-luna` defaults. Invalid profiles are reported as errors, not silently replaced with defaults. Provider-specific settings are validated by the daemon when it resolves the profile; credentials remain daemon-managed.

The extensions retain their hidden profile names (`code-search` and `read-conversation`), read-only tool allowlists, enabled filesystem search, disabled skills, and code mode off. Configured `allowed_tools`, `enable_fs_search_tools`, and `skills` values cannot override those restrictions. For `conversation`, this only affects `read_conversation`; keyword-based `search_conversation` does not use an LLM.

Apply these settings in the runner's extension configuration and reload resources to initialize the extensions with the new profiles. No separate named model profile is required.

### Boneyard

The [boneyard](./boneyard/) contains archived skills and extensions, kept for reference and excluded from plugin installation.

## Development

```bash
uv run --script tests/test_extensions.py
uv run --script tests/test_browser_use.py
```

## License

[MIT](./LICENSE)
