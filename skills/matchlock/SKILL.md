---
name: matchlock
description: Run AI agents and arbitrary code in ephemeral micro-VMs with VM-level isolation, network allowlisting, and host-side secret injection. Use for Matchlock CLI and SDK workflows.
---

# Matchlock

CLI tool and SDK for running AI agents in ephemeral micro-VMs with VM-level isolation, network allowlisting, and secret injection via MITM proxy.

Repository: `github.com/jingkaihe/matchlock`

For deeper dives, use the Matchlock repo as the source of truth and reference these locations: Go SDK (`pkg/sdk`), Python SDK (`sdk/python`), TypeScript SDK (`sdk/typescript`), and core source code (`cmd/`, `internal/`, `pkg/`).

**Key principle:** Secrets never enter the VM. The VM only sees placeholder values; a host-side MITM proxy replaces them in-flight when HTTP requests go to explicitly allowed hosts.

When software inside the VM expects a specific token shape, Matchlock also supports caller-defined placeholders via CLI flags (`--secret-placeholder`, `--secret-file`) and SDK builder helpers.

## Architecture

```diagram
╭───────── Host ──────────╮          ╭────── Micro-VM ──────╮
│ SDK → Matchlock CLI     │──vsock──▶│ Guest agent          │
│ Exec and file APIs      │          │ Native guest storage │
│ Policy + TLS MITM proxy │◀─────────│ Guest network        │
╰─────────────────────────╯          ╰──────────────────────╯
```

Platforms: Linux (Firecracker/KVM) and macOS (Apple Silicon via Virtualization.framework).

## CLI Reference

### Run a Command in a New Sandbox

```bash
matchlock run --image <image> [flags] -- <command>
```

Run `matchlock run --help` for the complete, version-matched list of flags and defaults.

`--cpus` accepts finite values greater than 0. Fractional values are implemented as: guest vCPU count = `ceil(cpus)` (for scheduler/topology), and guest CPU usage is additionally constrained with cgroup `cpu.max` to approximately the requested fraction (for example, `0.5` => 1 visible vCPU with ~50% of one CPU time budget).

### Common CLI Examples

```bash
# Simple command
matchlock run --image alpine:latest -- echo "hello from sandbox"

# Interactive shell
matchlock run --image ubuntu:latest -it -- bash

# Keep VM alive after command exits
matchlock run --image python:3.12-alpine --rm=false -- python3 -c "print('ready')"

# Detached mode (Docker-style; same lifecycle as --rm=false, prints VM ID)
matchlock run --image nginx:latest -d

# Detached mode with startup command
matchlock run --image alpine:latest -d -- sh -c "echo started; sleep 300"

# Network allowlisting with secret injection
matchlock run --image python:3.12-alpine \
  --allow-host api.openai.com \
  --secret "OPENAI_API_KEY@api.openai.com" \
  -- python3 script.py

# Custom placeholder for tools that validate token format inside the VM
matchlock run --image ubuntu:24.04 \
  --allow-host github.com \
  --allow-host api.github.com \
  --secret "GH_TOKEN@github.com,api.github.com" \
  --secret-placeholder "GH_TOKEN=gho_sandbox_placeholder" \
  -- sh -lc 'printf "%s\n" "$GH_TOKEN"'

# Attach persistent guest storage
matchlock volume create workdata --size 5120
matchlock run --image alpine:latest \
  --disk @workdata:/workspace \
  -w /workspace \
  -- sh -c 'echo hello > greeting.txt'

# Publish ports
matchlock run --image nginx:latest -p 8080:80 --rm=false -- nginx -g "daemon off;"

# Fully offline sandbox
matchlock run --image alpine:latest --no-network -- echo "no network"

# Resource limits
matchlock run --image python:3.12-alpine --cpus 4 --memory 2048 --disk-size 10240 --timeout 600 -- python3 heavy_task.py

# Fractional CPU request (1 visible vCPU, cgroup-limited to ~50% CPU budget)
matchlock run --image alpine:latest --cpus 0.5 -- echo "half-cpu request"
```

### Custom Secret Placeholders

Use a custom placeholder when the guest-side tool validates the token format before making a request. A common example is `GH_TOKEN`, where tools may expect a `gho_`, `ghp_`, or `github_pat_`-shaped value.

`--secret-file` accepts JSON in this format:

```json
{
  "GH_TOKEN": {
    "value": "gho_xxxxxxxxx_therealone",
    "placeholder": "gho_sandbox_placeholder",
    "hosts": ["github.com", "api.github.com"]
  }
}
```

Placeholder values must not overlap with each other, or with Matchlock's generated placeholder format (`SANDBOX_SECRET_<hex>`), otherwise Matchlock rejects the config.

### Exec into a Running Sandbox

```bash
matchlock exec <vm-id> -- <command>
matchlock exec <vm-id> -it -- bash
matchlock exec <vm-id> -w /tmp -- ls -la
```

### Explicit File Transfer

Use the SDK file APIs for individual files:

| Operation | Go | Python | TypeScript |
|-----------|----|--------|------------|
| Upload bytes | `WriteFile` / `WriteFileMode` | `write_file` | `writeFile` |
| Download bytes | `ReadFile` | `read_file` | `readFile` |
| List guest files | `ListFiles` | `list_files` | `listFiles` |

SDK calls go through host-side `matchlock rpc`; the host communicates with the guest over vsock. The SDK read/write calls buffer file contents and encode them as base64 in JSON-RPC.

For larger or binary transfers, use `ExecPipe` / `exec_pipe` / `execPipe` with stdin/stdout streams. For example, send a tar archive to a guest `tar -xf -` command, or stream a guest file/archive back through stdout. Create guest-local directories before selecting them as a working directory. See the exec-mode examples for pipe usage and the basic examples for file writes.

### Lifecycle Management

```bash
matchlock list                            # List all sandboxes (alias: ls)
matchlock list --running                  # Show only running VMs
matchlock get <vm-id>                     # Get sandbox details (JSON)
matchlock inspect <vm-id>                 # Inspect VM state and lifecycle (JSON)
matchlock kill <vm-id>                    # Kill a running VM
matchlock kill --all                      # Kill all running VMs
matchlock rm <vm-id>                      # Remove stopped VM state
matchlock rm --stopped                    # Remove all stopped VMs
matchlock prune                           # Remove all stopped sandboxes
```

### Runtime Network Policy

```bash
matchlock allow-list add <vm-id> host1,host2
matchlock allow-list delete <vm-id> host1
```

### Port Forwarding

```bash
matchlock port-forward <vm-id> 8080:8080
matchlock port-forward <vm-id> --address 0.0.0.0 8080:8080
```

### Image Management

```bash
matchlock build -f Dockerfile -t myapp:latest .    # Build from Dockerfile
matchlock build alpine:latest                       # Pre-build rootfs
matchlock pull alpine:latest                        # Pull image
matchlock pull --force alpine:latest                # Force re-pull
matchlock image ls                                  # List images
matchlock image rm myapp:latest                     # Remove image
docker save img | matchlock image import img        # Import from tarball
matchlock image gc                                  # Garbage-collect blobs
```

Dockerfile builds run BuildKit in a VM. Matchlock archives the context and selected Dockerfile on the host, streams them into guest-local storage over vsock, then streams the resulting image tarball back into the host image importer. `.dockerignore` and Dockerfile-specific ignore files control the upload; Unix socket entries are skipped rather than transferred.

Build storage has two separate size controls (both default to 10240 MB):

| Flag | Storage |
|------|---------|
| `--build-cache-size` | Persistent ext4 cache attached at `/var/lib/buildkit`; contains BuildKit snapshots and cache |
| `--build-disk` | Build VM root disk; contains the uploaded context and output tarball |

The persistent cache is `~/.cache/matchlock/buildkit/cache.ext4`. A larger `--build-cache-size` grows it in place while preserving the cache. For out-of-space errors under `/var/lib/buildkit` with caching enabled, check cache capacity and host free space; increasing `--build-disk` alone does not enlarge the cache. `--no-cache` skips attaching the persistent cache, so BuildKit storage also consumes the VM root disk in that mode.

### Volume Management

```bash
matchlock volume create mydata                      # Create named ext4 volume
matchlock volume create mydata --size 5120          # Create with size (MB)
matchlock volume ls                                 # List volumes
matchlock volume ls --json                          # Include host image paths
matchlock volume rm mydata                          # Remove volume
matchlock volume cp mydata mydata-backup            # Copy volume
```

Attach a named volume with `run --disk @mydata:/data`, or an existing ext4 image with `run --disk /absolute/path/data.ext4:/data`. Data on these block volumes persists after VM removal. Named volumes live in `~/.cache/matchlock/volumes/`; the separate BuildKit cache is **not** listed or managed by `matchlock volume`.

### Resource Cleanup

```bash
matchlock gc                    # Reconcile leaked resources
matchlock gc <vm-id>            # Reconcile specific VM
matchlock gc --force-running    # Also reconcile running VMs
```

### Host Diagnostics

```bash
matchlock diagnose --json
```

`matchlock diagnose` runs host preflight checks before you try to launch sandboxes.

Examples include host virtualization support, required artifacts, and key runtime dependencies.

### Setup (Linux, requires root)

```bash
sudo matchlock setup linux --user $USER
```

## SDKs

| SDK | Working examples |
|-----|------------------|
| Python | `references/python/basic.py`, `exec_modes.py`, `network_interception.py`, `port_forward.py` |
| Go | `references/go/basic.go`, `exec_modes.go`, `network_interception.go` |
| TypeScript | `references/typescript/basic.ts`, `exec_modes.ts`, `network_interception.ts` |

The Python references use PEP 723 inline dependencies and are run with `uv run`.

The examples cover sandbox construction, lifecycle management, buffered/streaming/interactive execution, explicit file operations, secret injection, network interception, and port forwarding. Network interception callbacks run on the **host side**, so real secrets must not be passed into guest code.

SDK builders mirror the CLI options. For APIs not shown in the examples, inspect the SDK source directories listed above. Custom-placeholder helpers are `add_secret_with_placeholder` (Python), `AddSecretWithPlaceholder` (Go), and `addSecretWithPlaceholder` (TypeScript).

## JSON-RPC Wire Protocol

All SDKs communicate via `matchlock rpc` (JSON-RPC over stdin/stdout):

| Method | Description |
|--------|-------------|
| `create` | Create a sandbox VM |
| `exec` | Buffered command execution |
| `exec_stream` | Streaming stdout/stderr |
| `exec_pipe` | Bidirectional stdin/stdout/stderr (no PTY) |
| `exec_tty` | Interactive PTY |
| `write_file` | Write file into sandbox |
| `read_file` | Read file from sandbox |
| `list_files` | List directory |
| `allow_list_add` | Add hosts to allowlist at runtime |
| `allow_list_delete` | Remove hosts from allowlist |
| `port_forward` | Forward host port → guest port |
| `cancel` | Abort in-flight execution |
| `close` | Shut down the sandbox VM |

## Environment Variables

| Variable | Description |
|----------|-------------|
| `MATCHLOCK_BIN` | Path to matchlock binary (used by SDKs) |
| `MATCHLOCK_RUN_IMAGE` | Default image for `matchlock run` |

## End-to-End Examples

These directories include complete setup instructions and supporting files:

| Directory | Description |
|-----------|-------------|
| `references/claude-code/` | Claude Code in a micro-VM with GitHub repo bootstrap and secret injection |
| `references/claude-code-with-docker/` | Claude Code + Docker daemon in a privileged sandbox (Python SDK) |
| `references/codex/` | OpenAI Codex in a micro-VM with GitHub repo bootstrap and secret injection |
| `references/docker-in-sandbox/` | Full Docker daemon in a privileged micro-VM (systemd + vfs driver) |
| `references/agent-client-protocol/` | Streamlit chatbot running a Kodelet agent over ACP via stdin/stdout |
