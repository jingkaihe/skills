#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# ///
"""Provision remotely-managed Cloudflare Tunnel resources.

Examples:
  uv run scripts/remote_managed_tunnel.py validate-token --show-zones

  uv run scripts/remote_managed_tunnel.py provision-custom-domain \
    --zone-name example.com \
    --hostname app.example.com \
    --origin-url http://127.0.0.1:8000 \
    --write-token-file /tmp/app-example-com.token
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


API_BASE = "https://api.cloudflare.com/client/v4"
DEFAULT_FALLBACK_SERVICE = "http_status:404"
USER_AGENT = "cloudflare-tunnel-skill/1.0"


class UsageError(RuntimeError):
    """Raised when the caller supplies incomplete or inconsistent inputs."""


class CloudflareError(RuntimeError):
    """Raised when the Cloudflare API returns an error."""


@dataclass(frozen=True)
class ZoneInfo:
    zone_id: str
    account_id: str
    zone_name: str


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "tunnel"


def default_tunnel_name(hostname: str) -> str:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
    base = slugify(hostname)[:80]
    return f"{base}-{stamp}"


def ensure_hostname_in_zone(hostname: str, zone_name: str) -> None:
    normalized_host = hostname.rstrip(".").lower()
    normalized_zone = zone_name.rstrip(".").lower()
    if normalized_host == normalized_zone or normalized_host.endswith(f".{normalized_zone}"):
        return
    raise UsageError(
        f"Hostname {hostname!r} is not inside zone {zone_name!r}. "
        "Use a hostname on the target zone or pick the correct zone."
    )


def check_origin(url: str, timeout: float) -> dict[str, Any]:
    last_error: Exception | None = None
    for method in ("HEAD", "GET"):
        request = urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return {"method": method, "status": response.status, "url": url}
        except urllib.error.HTTPError as exc:
            return {"method": method, "status": exc.code, "url": url}
        except Exception as exc:  # pragma: no cover - exercised by real network failures
            last_error = exc
    raise UsageError(f"Origin check failed for {url}: {last_error}")


def write_secret_file(path: pathlib.Path, value: str) -> pathlib.Path:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(value)
    os.chmod(path, 0o600)
    return path


def format_api_errors(payload: dict[str, Any]) -> str:
    errors = payload.get("errors") or []
    messages: list[str] = []
    for item in errors:
        if isinstance(item, dict):
            code = item.get("code")
            message = item.get("message")
            if code and message:
                messages.append(f"{code}: {message}")
            elif message:
                messages.append(str(message))
            else:
                messages.append(json.dumps(item, sort_keys=True))
        else:
            messages.append(str(item))
    return "; ".join(messages) if messages else json.dumps(payload, sort_keys=True)


class CloudflareClient:
    def __init__(self, api_token: str):
        self.api_token = api_token

    def request_json(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{API_BASE}{path}"
        if query:
            filtered = {key: value for key, value in query.items() if value is not None}
            url = f"{url}?{urllib.parse.urlencode(filtered, doseq=True)}"

        payload = None if body is None else json.dumps(body).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(url, data=payload, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = json.load(response)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                payload_json = json.loads(raw)
            except json.JSONDecodeError:
                payload_json = {"errors": [{"message": raw or exc.reason}]}
            raise CloudflareError(
                f"Cloudflare API {method} {path} failed with HTTP {exc.code}: "
                f"{format_api_errors(payload_json)}"
            ) from exc

        if not data.get("success", False):
            raise CloudflareError(
                f"Cloudflare API {method} {path} returned success=false: {format_api_errors(data)}"
            )
        return data

    def list_zones(self) -> list[dict[str, Any]]:
        return self.request_json("GET", "/zones").get("result") or []

    def resolve_zone(self, zone_name: str) -> ZoneInfo:
        results = self.request_json("GET", "/zones", query={"name": zone_name}).get("result") or []
        exact_matches = [
            zone for zone in results if (zone.get("name") or "").rstrip(".").lower() == zone_name.rstrip(".").lower()
        ]
        if not exact_matches:
            raise UsageError(
                f"Zone {zone_name!r} is not visible to the API token. "
                "Check Zone -> Zone -> Read scope and token resource restrictions."
            )
        if len(exact_matches) > 1:
            raise UsageError(f"Zone lookup for {zone_name!r} returned multiple exact matches; narrow token scope.")

        zone = exact_matches[0]
        account = zone.get("account") or {}
        zone_id = zone.get("id")
        account_id = account.get("id")
        if not zone_id or not account_id:
            raise UsageError(f"Zone lookup for {zone_name!r} did not include zone/account IDs: {zone}")

        return ZoneInfo(zone_id=zone_id, account_id=account_id, zone_name=zone.get("name") or zone_name)

    def create_remote_managed_tunnel(self, account_id: str, tunnel_name: str) -> dict[str, Any]:
        payload = {"name": tunnel_name, "config_src": "cloudflare"}
        return self.request_json("POST", f"/accounts/{account_id}/cfd_tunnel", body=payload)["result"]

    def configure_tunnel_ingress(
        self,
        account_id: str,
        tunnel_id: str,
        *,
        hostname: str,
        origin_url: str,
        fallback_service: str,
    ) -> dict[str, Any]:
        ingress = [
            {"hostname": hostname, "service": origin_url, "originRequest": {}},
            {"service": fallback_service},
        ]
        body = {"config": {"ingress": ingress}}
        return self.request_json(
            "PUT",
            f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations",
            body=body,
        ).get("result") or {}

    def upsert_dns_cname(self, zone_id: str, *, name: str, content: str, proxied: bool = True) -> dict[str, Any]:
        existing = self.request_json("GET", f"/zones/{zone_id}/dns_records", query={"name": name}).get("result") or []
        exact_matches = [
            record
            for record in existing
            if (record.get("name") or "").rstrip(".").lower() == name.rstrip(".").lower()
        ]
        if len(exact_matches) > 1:
            raise UsageError(
                f"DNS lookup for {name!r} returned multiple records. Clean them up manually before retrying."
            )

        body = {"type": "CNAME", "name": name, "content": content, "proxied": proxied}
        if exact_matches:
            record_id = exact_matches[0].get("id")
            if not record_id:
                raise UsageError(f"Existing DNS record for {name!r} has no id: {exact_matches[0]}")
            result = self.request_json("PUT", f"/zones/{zone_id}/dns_records/{record_id}", body=body)["result"]
            return {"action": "updated", "record": result}

        result = self.request_json("POST", f"/zones/{zone_id}/dns_records", body=body)["result"]
        return {"action": "created", "record": result}


def emit_output(data: dict[str, Any], output_format: str) -> None:
    if output_format == "json":
        json.dump(data, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return

    for key, value in data.items():
        if isinstance(value, (dict, list)):
            rendered = json.dumps(value, sort_keys=True)
        else:
            rendered = str(value)
        print(f"{key}={rendered}")


def cmd_validate_token(args: argparse.Namespace) -> int:
    client = CloudflareClient(api_token=get_api_token(args.api_token_env))
    zones = client.list_zones()
    result: dict[str, Any] = {
        "success": True,
        "zone_count": len(zones),
    }
    if args.show_zones:
        result["zones"] = sorted(zone.get("name") for zone in zones if zone.get("name"))
    if args.zone_name:
        zone = client.resolve_zone(args.zone_name)
        result["zone"] = {
            "zone_name": zone.zone_name,
            "zone_id": zone.zone_id,
            "account_id": zone.account_id,
        }
    emit_output(result, args.output)
    return 0


def cmd_provision_custom_domain(args: argparse.Namespace) -> int:
    ensure_hostname_in_zone(args.hostname, args.zone_name)
    client = CloudflareClient(api_token=get_api_token(args.api_token_env))

    result: dict[str, Any] = {
        "hostname": args.hostname,
        "zone_name": args.zone_name,
        "origin_url": args.origin_url,
        "tunnel_name": args.tunnel_name or default_tunnel_name(args.hostname),
    }
    if not args.no_check_origin:
        result["origin_check"] = check_origin(args.origin_url, timeout=args.origin_timeout)

    zone = client.resolve_zone(args.zone_name)
    result["zone_id"] = zone.zone_id
    result["account_id"] = zone.account_id

    if args.dry_run:
        result["dry_run"] = True
        result["actions"] = [
            "create remote-managed tunnel",
            "configure ingress for hostname -> origin",
            "upsert proxied DNS CNAME to <tunnel_id>.cfargotunnel.com",
        ]
        emit_output(result, args.output)
        return 0

    tunnel = client.create_remote_managed_tunnel(zone.account_id, result["tunnel_name"])
    tunnel_id = tunnel.get("id")
    token = tunnel.get("token")
    if not tunnel_id or not token:
        raise UsageError(f"Tunnel creation did not return id/token: {tunnel}")

    client.configure_tunnel_ingress(
        zone.account_id,
        tunnel_id,
        hostname=args.hostname,
        origin_url=args.origin_url,
        fallback_service=args.fallback_service,
    )
    dns = client.upsert_dns_cname(
        zone.zone_id,
        name=args.hostname,
        content=f"{tunnel_id}.cfargotunnel.com",
        proxied=not args.unproxied,
    )

    result["tunnel_id"] = tunnel_id
    result["public_target"] = f"{tunnel_id}.cfargotunnel.com"
    result["dns_action"] = dns["action"]
    result["dns_record_id"] = (dns.get("record") or {}).get("id")
    result["cloudflared_connector_mode"] = "token-file" if args.write_token_file else "token"

    if args.write_token_file:
        token_path = write_secret_file(pathlib.Path(args.write_token_file), token)
        result["token_file"] = str(token_path)
        result["run_command"] = f"cloudflared tunnel --no-autoupdate run --token-file {token_path}"
    else:
        result["run_command"] = "cloudflared tunnel --no-autoupdate run --token-file <path>"

    if args.include_token:
        result["token"] = token
    else:
        result["token_included"] = False

    emit_output(result, args.output)
    return 0


def get_api_token(env_var: str) -> str:
    value = os.environ.get(env_var, "").strip()
    if value:
        return value
    raise UsageError(f"Environment variable {env_var} is not set.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--api-token-env",
        default="CLOUDFLARE_API_TOKEN",
        help="Environment variable that contains the Cloudflare API token.",
    )
    parser.add_argument(
        "--output",
        choices=("text", "json"),
        default="text",
        help="Output format for command results.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser(
        "validate-token",
        help="Verify the API token and optionally resolve a specific zone.",
    )
    validate.add_argument("--zone-name", help="Optional zone name to resolve.")
    validate.add_argument(
        "--show-zones",
        action="store_true",
        help="List zones visible to the token. Useful for a quick scope sanity-check.",
    )
    validate.set_defaults(func=cmd_validate_token)

    provision = subparsers.add_parser(
        "provision-custom-domain",
        help="Create a remote-managed tunnel, configure ingress, and upsert the DNS record.",
    )
    provision.add_argument("--zone-name", required=True, help="Cloudflare zone, for example example.com.")
    provision.add_argument("--hostname", required=True, help="Public hostname, for example app.example.com.")
    provision.add_argument("--origin-url", required=True, help="Local origin URL, for example http://127.0.0.1:8000.")
    provision.add_argument(
        "--tunnel-name",
        help="Friendly tunnel name. Defaults to a hostname-based UTC timestamp.",
    )
    provision.add_argument(
        "--fallback-service",
        default=DEFAULT_FALLBACK_SERVICE,
        help="Catch-all ingress service appended after the hostname rule.",
    )
    provision.add_argument(
        "--write-token-file",
        help="Write the returned tunnel token to this file with 0600 permissions.",
    )
    provision.add_argument(
        "--include-token",
        action="store_true",
        help="Include the raw tunnel token in stdout. Prefer --write-token-file when possible.",
    )
    provision.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve the zone and validate the origin without creating Cloudflare resources.",
    )
    provision.add_argument(
        "--no-check-origin",
        action="store_true",
        help="Skip the local origin reachability check.",
    )
    provision.add_argument(
        "--origin-timeout",
        type=float,
        default=5.0,
        help="Timeout in seconds for the origin reachability probe.",
    )
    provision.add_argument(
        "--unproxied",
        action="store_true",
        help="Create an unproxied DNS record instead of a proxied one.",
    )
    provision.set_defaults(func=cmd_provision_custom_domain)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (UsageError, CloudflareError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
