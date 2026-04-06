---
name: cloudflare-tunnel
description: Use whenever the user wants to expose localhost or a private service through Cloudflare Tunnel, publish a stable custom-domain HTTPS endpoint with cloudflared, run a named tunnel using a Cloudflare API token or tunnel token, or generate a temporary trycloudflare.com URL. Trigger on mentions of cloudflared, Cloudflare Tunnel, trycloudflare, Argo Tunnel, public hostname, expose localhost, preview URL, webhook testing, or custom-domain HTTPS over Cloudflare.
---

# Cloudflare Tunnel

This skill focuses on the two best public-serving flows:

1. a **named, remotely-managed tunnel** for stable custom-domain HTTPS
2. a **Quick Tunnel** for temporary `trycloudflare.com` sharing

Prefer current Cloudflare docs and API behavior over stale memory. The upstream Cloudflare skill references are a strong starting point, but Tunnel details do move; use `references/sources.md` as the current baseline.

## First principles

- If `CLOUDFLARE_API_TOKEN` and/or `CLOUDFLARE_TUNNEL_TOKEN` are already available, avoid interactive `cloudflared tunnel login`.
- For anything stable, repeatable, or user-facing, prefer a **remotely-managed named tunnel** over a locally-managed `config.yml` workflow.
- For public HTTPS, usually point Cloudflare Tunnel at `http://127.0.0.1:<PORT>` and let Cloudflare terminate public TLS at the edge. Only use an HTTPS origin if the local service actually requires TLS.
- For servers, containers, and production-ish setups, prefer `cloudflared tunnel --no-autoupdate run --token-file ...` and manage upgrades deliberately.
- For admin panels or internal tools, recommend Cloudflare Access in front of the public hostname.
- If uptime matters, run the same named tunnel on 2+ replicas rather than relying on a single `cloudflared` process.
- For repeated API work, prefer the bundled `uv` script in `scripts/remote_managed_tunnel.py` over ad-hoc `curl` blocks. It standardizes token validation, zone resolution, tunnel creation, ingress config, and DNS upsert.
- Be opinionated and fail fast: if the scripted path cannot run, stop and surface the blocker instead of drifting into a hand-built `curl` procedure.

## Decision rule

Choose the serving mode first:

```text
Need a stable hostname, custom domain, reusable deployment, or anything beyond ad-hoc testing?
-> Use a named remotely-managed tunnel.

Need a temporary share link, local demo URL, or there is no usable token / no Cloudflare-managed zone?
-> Use a Quick Tunnel (trycloudflare.com).
```

## What to load next

- Read `references/custom-domain.md` for **custom-domain HTTPS serving**.
- Read `references/quick-tunnel.md` for **trycloudflare.com serving**.
- Read `references/sources.md` when you need to sanity-check a limit, endpoint, or doc-backed behavior.
- Use `scripts/remote_managed_tunnel.py` when you need a repeatable custom-domain setup flow.

## Strong default stance

Do **not** default to locally-managed tunnels unless the user explicitly wants YAML-managed config on disk, version-controlled ingress rules, or an offline-ish workflow. For most CLI and ops tasks, the best path is:

1. use `CLOUDFLARE_API_TOKEN` to create or update a remotely-managed tunnel and DNS
2. use `CLOUDFLARE_TUNNEL_TOKEN` to run `cloudflared`

That split is cleaner because the API token manages configuration while the tunnel token only authorizes the connector process.

The standardized path in this skill is:

1. `uv run scripts/remote_managed_tunnel.py validate-token ...`
2. `uv run scripts/remote_managed_tunnel.py provision-custom-domain ... --write-token-file ...`
3. `cloudflared tunnel --no-autoupdate run --token-file ...`

## CLI quick reference

Script: `scripts/remote_managed_tunnel.py`

```bash
uv run scripts/remote_managed_tunnel.py [--api-token-env ENV] [--output text|json] <subcommand> ...
```

Global flags go **before** the subcommand.

```bash
# good
uv run scripts/remote_managed_tunnel.py --output json validate-token --show-zones

# bad
uv run scripts/remote_managed_tunnel.py validate-token --show-zones --output json
```

### `validate-token`

```bash
uv run scripts/remote_managed_tunnel.py validate-token [--zone-name ZONE] [--show-zones]
```

Checks that the API token works, optionally lists visible zones, and resolves `zone_id`/`account_id` for a zone.

```bash
uv run scripts/remote_managed_tunnel.py validate-token --show-zones
uv run scripts/remote_managed_tunnel.py --output json validate-token --zone-name example.com --show-zones
```

### `provision-custom-domain`

```bash
uv run scripts/remote_managed_tunnel.py provision-custom-domain --zone-name ZONE --hostname HOST --origin-url URL [--tunnel-name NAME] [--write-token-file PATH] [--fallback-service http_status:404] [--dry-run] [--no-check-origin] [--origin-timeout SEC] [--unproxied] [--include-token]
```

Required: `--zone-name`, `--hostname`, `--origin-url`.

Useful options: `--tunnel-name`, `--write-token-file`, `--dry-run`, `--no-check-origin`, `--origin-timeout`, `--unproxied`, `--include-token`.

Normal run behavior: validate hostname and origin, resolve zone/account IDs, create the tunnel, push ingress for `hostname -> origin-url`, upsert the DNS CNAME, then print the tunnel id, public target, and `cloudflared` run command.

```bash
# preflight only
uv run scripts/remote_managed_tunnel.py --output json provision-custom-domain --zone-name example.com --hostname app.example.com --origin-url http://127.0.0.1:8000 --dry-run

# provision and save token securely
uv run scripts/remote_managed_tunnel.py provision-custom-domain --zone-name example.com --hostname app.example.com --origin-url http://127.0.0.1:8000 --tunnel-name app-example-com --write-token-file /tmp/app-example-com.token

# run connector
cloudflared tunnel --no-autoupdate run --token-file /tmp/app-example-com.token
```

Common output keys: `hostname`, `zone_name`, `origin_url`, `origin_check`, `zone_id`, `account_id`, `tunnel_name`, `tunnel_id`, `public_target`, `dns_action`, `dns_record_id`, `token_file`, `run_command`.

Notes: prefer `http://127.0.0.1:<PORT>` as the origin; prefer `--write-token-file` over `--include-token`; if parsing fails, check whether global flags were placed after the subcommand; there is no cleanup subcommand yet, so cleanup means deleting the DNS record and the tunnel via the API.

## Response pattern

When helping the user, keep the answer in this order:

1. say which mode you are choosing and why
2. ask only for missing inputs (`hostname`, `zone`, `local port`, `origin URL`)
3. give exact commands that use the bundled Python script for repeatable custom-domain setup; do not switch to raw API `curl` fallback in normal use
4. mention the one or two most relevant gotchas
5. end with a concrete validation step using the final public URL
