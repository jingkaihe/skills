# Quick Tunnel / trycloudflare.com

Use this path for temporary sharing, demos, local previews, webhook testing, and the "I just need a URL right now" case.

## When this is the right choice

Choose Quick Tunnel when at least one of these is true:

- there is no custom domain requirement
- there is no usable Cloudflare-managed DNS zone
- there is no tunnel token available
- the user wants a temporary preview URL, not a stable deployment

## Command

Point the tunnel at the local service directly:

```bash
cloudflared tunnel --url http://127.0.0.1:3000
```

`cloudflared` will print a random `https://<random>.trycloudflare.com` URL.

## Good default behavior

- Prefer `127.0.0.1` and the exact local port the app is listening on.
- Treat the generated URL as ephemeral.
- Use this for testing and demos, then graduate to a named tunnel if the URL needs to stay stable.

## Important limitations

According to current Cloudflare docs, Quick Tunnels are for testing only:

- random hostname on `trycloudflare.com`
- not a stable production endpoint
- `200` concurrent request limit
- no Server-Sent Events (SSE)

## Gotcha: local config file conflict

Cloudflare's Quick Tunnel docs explicitly say the feature is not supported if `.cloudflared/config.yaml` is present.

Practical inference: if Quick Tunnel refuses to start and the machine already uses named tunnels, temporarily move aside any existing `.cloudflared/config.yaml` or `.cloudflared/config.yml` and retry.

## Validation

Once the URL is printed, validate it immediately:

```bash
curl -I https://<random>.trycloudflare.com
```

If the app works and the user later asks for a stable hostname or real HTTPS on their own domain, switch them to the custom-domain flow instead of trying to stretch Quick Tunnel beyond its intended use.
