# Custom-domain HTTPS serving

Use this path when the user wants a stable hostname such as `https://app.example.com`.

## Opinionated default

Prefer a **remotely-managed named tunnel**.

Why this is usually optimal:

- no interactive browser login is required when `CLOUDFLARE_API_TOKEN` is already available
- no local credentials JSON or local config file is required just to run the connector
- routing changes live in Cloudflare and are easier to update centrally
- the returned tunnel token can be handed to the runtime host without also granting broad API access

## Inputs you need

- `ZONE_NAME` — apex zone on Cloudflare, for example `example.com`
- `HOSTNAME` — public hostname, for example `app.example.com`
- `ORIGIN_URL` — local service address, usually `http://127.0.0.1:3000`
- `TUNNEL_NAME` — friendly name, for example `app-tunnel`

## Standardized workflow

This skill should use the bundled script in `scripts/remote_managed_tunnel.py`.

Do **not** fall back to hand-written `curl` and `jq` snippets in normal use. If the script cannot run, fail fast and surface the blocker.

### 1) Validate the token and zone

```bash
export ZONE_NAME=example.com

uv run scripts/remote_managed_tunnel.py validate-token \
  --zone-name "$ZONE_NAME" \
  --show-zones
```

This confirms that the token can list zones and resolve the target zone.

### 2) Provision the tunnel, ingress, and DNS

```bash
export ZONE_NAME=example.com
export HOSTNAME=app.example.com
export ORIGIN_URL=http://127.0.0.1:3000
export TUNNEL_NAME=app-tunnel
export TOKEN_FILE=/tmp/app-example-com.token

uv run scripts/remote_managed_tunnel.py provision-custom-domain \
  --zone-name "$ZONE_NAME" \
  --hostname "$HOSTNAME" \
  --origin-url "$ORIGIN_URL" \
  --tunnel-name "$TUNNEL_NAME" \
  --write-token-file "$TOKEN_FILE"
```

The script intentionally fails early if:

- `CLOUDFLARE_API_TOKEN` is missing
- the token cannot see the requested zone
- the hostname is outside the zone
- the local origin is unreachable
- the API returns an error during tunnel or DNS provisioning

For a preflight with no side effects:

```bash
uv run scripts/remote_managed_tunnel.py provision-custom-domain \
  --zone-name "$ZONE_NAME" \
  --hostname "$HOSTNAME" \
  --origin-url "$ORIGIN_URL" \
  --dry-run \
  --output json
```

### 3) Run the connector

Foreground:

```bash
cloudflared tunnel --no-autoupdate run --token-file "$TOKEN_FILE"
```

Long-running local CLI session in tmux:

```bash
tmux -L llm-agent new-session -d -s app-tunnel
tmux -L llm-agent set-option -t app-tunnel remain-on-exit on
tmux -L llm-agent send-keys -t app-tunnel \
  "cloudflared tunnel --no-autoupdate run --token-file $TOKEN_FILE 2>&1" Enter
```

Persistent system service on Linux or macOS:

```bash
sudo cloudflared service install "$(cat "$TOKEN_FILE")"
```

## Validation

Use both an external and an origin-side check:

```bash
curl -I "https://$HOSTNAME"
```

Also confirm that `cloudflared` logs show a healthy connection and that the app responds through the new public hostname.

## Important guidance

- For public HTTPS, your origin usually does **not** need its own public certificate. A local HTTP origin is often the simplest and best option.
- The API token usually needs:
  - `Account -> Cloudflare Tunnel -> Edit`
  - `Zone -> DNS -> Edit`
  - `Zone -> Zone -> Read` when the script resolves the zone automatically
- If the local origin uses HTTPS with a self-signed certificate, prefer either:
  - switching the tunnel service URL to local HTTP, or
  - adding the correct `originRequest` TLS settings instead of hoping it works
- If the tunnel cannot connect, verify outbound access to `*.argotunnel.com` on UDP/TCP `7844` and TCP `443`.
- For anything sensitive, recommend adding Cloudflare Access after the tunnel is working.
- If the user wants higher availability, run the same named tunnel on multiple hosts or containers.
