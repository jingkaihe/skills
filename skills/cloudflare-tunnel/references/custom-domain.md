# Custom-domain HTTPS serving

Use this path when the user wants a stable hostname such as `https://app.example.com`.

## Opinionated default

Prefer a **remotely-managed named tunnel**.

Why this is usually optimal:

- no interactive browser login is required when `CLOUDFLARE_API_TOKEN` is already available
- no local credentials JSON or local config file is required just to run the connector
- routing changes live in Cloudflare and are easier to update centrally
- the tunnel token can be handed to the runtime host without also granting broad API access

## Inputs you need

- `ZONE_NAME` — apex zone on Cloudflare, for example `example.com`
- `HOSTNAME` — public hostname, for example `app.example.com`
- `ORIGIN_URL` — local service address, usually `http://127.0.0.1:3000`
- `TUNNEL_NAME` — friendly name, for example `app-tunnel`

If the user already has `CLOUDFLARE_TUNNEL_TOKEN` for the intended tunnel, you can often skip tunnel creation and just run the connector. Use the API token when you need to create or change routes.

## Best workflow

### 1) Resolve zone and account IDs from the zone name

```bash
export ZONE_NAME=example.com
export HOSTNAME=app.example.com
export ORIGIN_URL=http://127.0.0.1:3000
export TUNNEL_NAME=app-tunnel

ZONE_JSON=$(curl -fsS --get https://api.cloudflare.com/client/v4/zones \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  --data-urlencode "name=$ZONE_NAME")

ZONE_ID=$(jq -r '.result[0].id' <<<"$ZONE_JSON")
ACCOUNT_ID=$(jq -r '.result[0].account.id' <<<"$ZONE_JSON")
```

If `jq` is not available, parse the JSON with Python instead of changing the overall flow.

### 2) Create a remotely-managed tunnel if you do not already have a tunnel token

```bash
TUNNEL_JSON=$(curl -fsS https://api.cloudflare.com/client/v4/accounts/$ACCOUNT_ID/cfd_tunnel \
  --request POST \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  -H "Content-Type: application/json" \
  --data "{\"name\":\"$TUNNEL_NAME\",\"config_src\":\"cloudflare\"}")

TUNNEL_ID=$(jq -r '.result.id' <<<"$TUNNEL_JSON")
export CLOUDFLARE_TUNNEL_TOKEN=$(jq -r '.result.token' <<<"$TUNNEL_JSON")
```

If the environment already provides `CLOUDFLARE_TUNNEL_TOKEN` and the tunnel is known-good, do not recreate it just to be fancy.

### 3) Configure the public hostname -> local origin mapping

Always include a catch-all rule at the end.

```bash
curl -fsS https://api.cloudflare.com/client/v4/accounts/$ACCOUNT_ID/cfd_tunnel/$TUNNEL_ID/configurations \
  --request PUT \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  -H "Content-Type: application/json" \
  --data "{\"config\":{\"ingress\":[{\"hostname\":\"$HOSTNAME\",\"service\":\"$ORIGIN_URL\",\"originRequest\":{}},{\"service\":\"http_status:404\"}]}}"
```

### 4) Create the proxied DNS record

The public hostname should point to `<TUNNEL_ID>.cfargotunnel.com` as a proxied CNAME.

```bash
curl -fsS https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records \
  --request POST \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  -H "Content-Type: application/json" \
  --data "{\"type\":\"CNAME\",\"proxied\":true,\"name\":\"$HOSTNAME\",\"content\":\"$TUNNEL_ID.cfargotunnel.com\"}"
```

If the DNS record already exists, update it instead of creating a duplicate.

### 5) Run the connector

For a foreground run:

```bash
cloudflared tunnel --no-autoupdate run --token "$CLOUDFLARE_TUNNEL_TOKEN"
```

For a persistent service on Linux or macOS:

```bash
sudo cloudflared service install "$CLOUDFLARE_TUNNEL_TOKEN"
```

## Validation

Use both an external and an origin-side check:

```bash
curl -I "https://$HOSTNAME"
```

Also confirm that `cloudflared` logs show a healthy connection and that the app responds through the new public hostname.

## Important guidance

- For public HTTPS, your origin usually does **not** need its own public certificate. A local HTTP origin is often the simplest and best option.
- If the local origin uses HTTPS with a self-signed certificate, prefer either:
  - switching the tunnel service URL to local HTTP, or
  - adding the correct `originRequest` TLS settings instead of hoping it works
- If the tunnel cannot connect, verify outbound access to `*.argotunnel.com` on UDP/TCP `7844` and TCP `443`.
- For anything sensitive, recommend adding Cloudflare Access after the tunnel is working.
- If the user wants higher availability, run the same named tunnel on multiple hosts or containers.
