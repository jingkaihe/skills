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

## How to use this reference

- If a limit, endpoint, or flag matters to the task, re-check the official docs before stating it confidently.
- If the user only needs a public URL fast, use Quick Tunnel.
- If the user needs a stable custom domain, use a remotely-managed named tunnel and keep configuration in Cloudflare, not in a local YAML file.
