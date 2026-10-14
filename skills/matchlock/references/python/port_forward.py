#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["matchlock"]
# ///
"""Python SDK port-forward example with nginx.

This example:
1. Starts nginx in the guest and forwards host 8080 -> guest 80 at create-time.
2. Adds a runtime forward host 18080 -> guest 80 after launch.

Use --port and --runtime-port if the default host ports are already in use.
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from urllib.request import urlopen

from matchlock import Client, Config, Sandbox

logging.basicConfig(format="%(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)


def wait_for_http(url: str, attempts: int = 30, delay_seconds: float = 0.25) -> str:
    """Poll URL until it serves HTTP 200 or retries are exhausted."""
    last_error: Exception | None = None
    for _ in range(attempts):
        try:
            with urlopen(url, timeout=1.5) as response:
                if response.status == 200:
                    return response.read().decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            last_error = exc
        time.sleep(delay_seconds)
    raise RuntimeError(f"timed out waiting for {url}; last_error={last_error!r}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8080, help="Create-time host port")
    parser.add_argument("--runtime-port", type=int, default=18080, help="Runtime host port")
    args = parser.parse_args()

    sandbox = Sandbox("nginx:alpine").with_port_forward(args.port, 80)
    config = Config(binary_path=os.environ.get("MATCHLOCK_BIN", "matchlock"))

    client = Client(config)
    try:
        with client:
            vm_id = client.launch(sandbox)
            log.info("sandbox ready vm=%s", vm_id)

            client.write_file("/usr/share/nginx/html/index.html", "hello matchlock\n")

            body = wait_for_http(f"http://127.0.0.1:{args.port}")
            log.info("create-time forward works: 127.0.0.1:%d -> guest:80", args.port)
            print(body)

            bindings = client.port_forward(f"{args.runtime_port}:80")
            for binding in bindings:
                log.info(
                    "runtime forward added: %s:%d -> guest:%d",
                    binding.address,
                    binding.local_port,
                    binding.remote_port,
                )

            body = wait_for_http(f"http://127.0.0.1:{args.runtime_port}")
            log.info("runtime forward works: 127.0.0.1:%d -> guest:80", args.runtime_port)
            print(body)
    finally:
        try:
            client.remove()
        except Exception as exc:  # noqa: BLE001
            log.warning("remove failed (ignored): %s", exc)


if __name__ == "__main__":
    main()
