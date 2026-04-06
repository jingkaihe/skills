# Sources and doc-backed notes

This skill was based on Cloudflare's tunnel reference bundle in `cloudflare/skills`, then refreshed against current official Cloudflare documentation so the recommendations reflect newer API and operational guidance.

## Primary sources

- Cloudflare Tunnel setup: https://developers.cloudflare.com/tunnel/setup/
- Tunnel tokens: https://developers.cloudflare.com/tunnel/advanced/tunnel-tokens/
- Quick Tunnels / TryCloudflare: https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/
- Deploy replicas: https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/tunnel-availability/deploy-replicas/
- Cloudflare zones API index: https://developers.cloudflare.com/api/resources/zones/methods/list/

## Important refreshes vs older references

- Current official setup docs use the `cfd_tunnel` API path for create/configure/token flows.
- Remotely-managed tunnels only need a tunnel token to run the `cloudflared` connector.
- Quick Tunnels are explicitly for testing only, with a documented `200` concurrent request limit and no SSE support.
- Quick Tunnel docs explicitly warn about `.cloudflared/config.yaml` conflicts.
- Current replica guidance is lower than some older references implied: current docs describe up to `100` connections / `25` replicas per tunnel.

## Operational learnings to keep in the skill

- Standardize repeated API work in a bundled `uv` script instead of repeating multi-line `curl` snippets in markdown.
- The default procedure should be: validate token -> provision remote-managed tunnel -> write token file -> run `cloudflared` with `--token-file`.
- Be fail-fast instead of fallback-heavy. If the scripted workflow cannot run, stop and explain the blocker rather than improvising a second manual path.
- In practice, token scope should usually include:
  - `Account -> Cloudflare Tunnel -> Edit`
  - `Zone -> DNS -> Edit`
  - `Zone -> Zone -> Read` when the workflow resolves `ZONE_ID` from the zone name
- Preflighting the local origin before creating Cloudflare resources catches the most common operator mistake early.
- `cloudflared tunnel --no-autoupdate run --token-file ...` is a good default for local long-running sessions because it avoids exposing the raw tunnel token on the process list.

## How to use this reference

- If a limit, endpoint, or flag matters to the task, re-check the official docs before stating it confidently.
- If the user only needs a public URL fast, use Quick Tunnel.
- If the user needs a stable custom domain, use a remotely-managed named tunnel and keep configuration in Cloudflare, not in a local YAML file.
