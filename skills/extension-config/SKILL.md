---
name: extension-config
description: Configure jingkaihe/skills Kodelet extensions through extensions.config. Use when changing the model, provider, reasoning effort, or token budget for the code-search, conversation, or web-search extensions.
---

# Extension configuration

Configure the model profiles used by `code-search`, `conversation`, and `web-search` from the `jingkaihe/skills` plugin. These settings affect the extensions, not the parent conversation's model.

## Configuration location

- Project: `kodelet-config.yaml` in the runner's execution workspace.
- Global: the runner's trusted configuration, normally `~/.kodelet/config.yaml`.

Edit only the requested scope. Merge entries into the existing `extensions.config` map, preserving unrelated settings. Keys must match the full installed extension IDs shown below.

## Configure inline profiles

Add only the entries you want to configure:

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
        max_tokens: 64000
    jingkaihe@skills/web-search:
      profile:
        provider: anthropic
        model: claude-haiku-5-5
        anthropic_api_access: subscription
        reasoning_effort: none
        max_tokens: 64000
```

- `profile` is an inline object using Kodelet's native profile keys. It requires `provider` (`openai` or `anthropic`) and a nonempty `model`. No separate named model profile is needed.
- A supplied profile **replaces** the built-in model settings. Omitted settings use Kodelet's defaults, except for the restrictions below. Configuration layers can still merge nested maps; remove stale provider settings from the relevant layers when switching providers.
- For Anthropic subscription, include `anthropic_api_access: subscription`. Prefer `claude-haiku-5-5` unless the user requests another model.
- For OpenAI via Codex, explicitly include `openai.platform: codex`.
- To restore the built-in OpenAI/Codex `gpt-6-luna` profile, remove `profile` from the effective configuration, including inherited settings. Do not set it to `null`, an empty object, or a named-profile string.

## Configuration limits

- Conversation's profile affects only `read_conversation`; keyword-based `search_conversation` has no model settings.
- Code-search and conversation enforce their read-only `allowed_tools` and `enable_fs_search_tools: true`.
- Web-search enforces an `allowed_tools` list containing only `anthropic_web_search` or `openai_web_search`, according to provider. OpenAI profiles also force `openai.api_mode: responses` and `openai.enable_search: true`; other supplied OpenAI settings are preserved.
- All three enforce disabled skills and code mode off.

## Apply changes

After changing runner configuration, use `reload_resources` or `/reload` to apply it to subsequent extension calls.
