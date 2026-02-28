---
name: matchlock
description: Run AI agents and arbitrary code in ephemeral micro-VMs with VM-level isolation, network allowlisting, and secret injection. Use when users want to create sandboxes, run code in isolated VMs, manage sandbox lifecycles, use the matchlock CLI, or integrate with the matchlock Go/Python SDK. Triggers on mentions of matchlock, sandbox, micro-VM, VM isolation, network interception, secret injection, or ephemeral environments.
---

# Matchlock

CLI tool and SDK for running AI agents in ephemeral micro-VMs with VM-level isolation, network allowlisting, and secret injection via MITM proxy.

**Key principle:** Secrets never enter the VM. The VM only sees placeholder values; a host-side MITM proxy replaces them in-flight when HTTP requests go to explicitly allowed hosts.

## Architecture

```
┌──────────── Host ────────────┐      ┌──── Micro-VM ────┐
│ Matchlock CLI / SDK          │      │ Guest Agent       │
│ Policy Engine                │──────│ (vsock :5000)     │
│ Transparent Proxy + TLS MITM │      │                   │
│ VFS Server                   │──────│ /workspace (FUSE) │
└──────────────────────────────┘      └───────────────────┘
```

Platforms: Linux (Firecracker/KVM) and macOS (Apple Silicon via Virtualization.framework).

## CLI Reference

### Run a Command in a New Sandbox

```bash
matchlock run --image <image> [flags] -- <command>
```

| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `--image` | | *(required)* | Container image |
| `--allow-host` | | | Allowed hosts (repeatable; supports `*.example.com` wildcards) |
| `--secret` | | | Secret injection: `NAME=VALUE@host1,host2` or `NAME@host1,host2` |
| `--no-network` | | `false` | Fully offline sandbox |
| `--network-intercept` | | `false` | Force interception proxy even with empty allow-list |
| `-it` | | | Interactive TTY mode |
| `--rm` | | `true` | Remove sandbox after exit (`--rm=false` to keep alive) |
| `-p` | | | Publish port `[LOCAL:]REMOTE` |
| `--cpus` | | 2 | Number of CPUs |
| `--memory` | | 512 | Memory in MB |
| `--disk-size` | | 2048 | Disk size in MB |
| `--timeout` | | 300 | Timeout in seconds |
| `-e` | | | Environment variable `KEY=VALUE` |
| `--env-file` | | | Env file path |
| `-v` | | | Volume mount `host:guest[:overlay\|host_fs\|ro]` |
| `--disk` | | | Attach raw ext4 disk `host_path:guest_mount[:ro]` |
| `--add-host` | | | Custom host-to-IP mapping `host:ip` |
| `--dns-servers` | | `8.8.8.8,8.8.4.4` | DNS servers |
| `--privileged` | | `false` | Skip in-guest seccomp/cap-drop |
| `-w` | | image WORKDIR | Working directory |
| `-u` | | image USER | Run as user |
| `--entrypoint` | | image ENTRYPOINT | Override entrypoint |
| `--graceful-shutdown` | | 5s | Graceful shutdown duration |
| `--pull` | | `false` | Always pull image (ignore cache) |
| `--workspace` | | | Guest mount point for VFS |
| `--hostname` | | sandbox ID | Guest hostname |
| `--mtu` | | 1500 | Network MTU |
| `--address` | | `127.0.0.1` | Bind address for published ports |

### Common CLI Examples

```bash
# Simple command
matchlock run --image alpine:latest -- echo "hello from sandbox"

# Interactive shell
matchlock run --image ubuntu:latest -it -- bash

# Keep VM alive after command exits
matchlock run --image python:3.12-alpine --rm=false -- python3 -c "print('ready')"

# Network allowlisting with secret injection
matchlock run --image python:3.12-alpine \
  --allow-host api.openai.com \
  --secret "OPENAI_API_KEY@api.openai.com" \
  -- python3 script.py

# Mount a host directory
matchlock run --image node:22-alpine \
  -v /home/user/project:/workspace:host_fs \
  -w /workspace \
  -- npm test

# Publish ports
matchlock run --image nginx:latest -p 8080:80 --rm=false -- nginx -g "daemon off;"

# Fully offline sandbox
matchlock run --image alpine:latest --no-network -- echo "no network"

# Resource limits
matchlock run --image python:3.12-alpine --cpus 4 --memory 2048 --disk-size 10240 --timeout 600 -- python3 heavy_task.py
```

### Exec into a Running Sandbox

```bash
matchlock exec <vm-id> -- <command>
matchlock exec <vm-id> -it -- bash
matchlock exec <vm-id> -w /workspace -- ls -la
```

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

### Volume Management

```bash
matchlock volume create mydata                      # Create named ext4 volume
matchlock volume create mydata --size 5120          # Create with size (MB)
matchlock volume ls                                 # List volumes
matchlock volume rm mydata                          # Remove volume
matchlock volume cp mydata mydata-backup            # Copy volume
```

### Resource Cleanup

```bash
matchlock gc                    # Reconcile leaked resources
matchlock gc <vm-id>            # Reconcile specific VM
matchlock gc --force-running    # Also reconcile running VMs
```

### Setup (Linux, requires root)

```bash
sudo matchlock setup linux --user $USER
```

## Python SDK

Install: `pip install matchlock`

Reference examples are in the `references/python/` directory.

### Sandbox Builder

```python
from matchlock import Client, Sandbox

sandbox = (
    Sandbox("python:3.12-alpine")
    .with_cpus(2)
    .with_memory(1024)
    .with_disk_size(5120)
    .with_timeout(300)
    .with_workspace("/workspace")
    .with_privileged()
    .allow_host("api.openai.com", "*.npmjs.org")
    .add_host("api.internal", "10.0.0.10")
    .add_secret("API_KEY", os.environ["API_KEY"], "api.openai.com")
    .block_private_ips()
    .with_dns_servers("1.1.1.1")
    .with_network_mtu(1200)
    .with_no_network()
    .with_env("FOO", "bar")
    .mount_host_dir("/workspace/src", "/home/user/src")
    .mount_host_dir_readonly("/workspace/cfg", "/etc/cfg")
    .mount_memory("/workspace/tmp")
    .mount_overlay("/workspace/data", "/home/user/data")
    .with_user("1000:1000")
    .with_entrypoint("python3")
    .with_port_forward(8080, 8080)
)
```

### Client Lifecycle

```python
from matchlock import Client, Config, Sandbox

config = Config(binary_path="matchlock")

with Client(config) as client:
    vm_id = client.launch(sandbox)

    # Buffered exec
    result = client.exec("echo hello")
    # result.exit_code, result.stdout, result.stderr, result.duration_ms

    # Streaming exec
    result = client.exec_stream("long-running-cmd", stdout=sys.stdout, stderr=sys.stderr)

    # Pipe exec (bidirectional stdin/stdout/stderr)
    result = client.exec_pipe("cat", stdin=io.BytesIO(b"input\n"), stdout=out_buf, stderr=err_buf)

    # Interactive TTY exec
    result = client.exec_interactive("sh", stdin=stdin_reader, stdout=sys.stdout, rows=24, cols=80)

    # File operations
    client.write_file("/workspace/hello.txt", "hello")
    client.write_file("/workspace/script.sh", "#!/bin/sh\necho hi", mode=0o755)
    data = client.read_file("/workspace/hello.txt")   # returns bytes
    files = client.list_files("/workspace")             # list[FileInfo]

    # Runtime allowlist mutation
    client.allow_list_add("api.openai.com", "api.anthropic.com")
    client.allow_list_delete("api.openai.com")

    # Port forwarding
    client.port_forward(8080, 8080)

client.remove()
```

### Network Interception (Python)

Callback hooks run on the **host side** — secrets never enter the VM:

```python
from matchlock import (
    Client, Sandbox,
    NetworkHookRule, NetworkInterceptionConfig,
    NetworkHookRequest, NetworkHookResult, NetworkHookRequestMutation,
)

api_key = os.environ["ANTHROPIC_API_KEY"]

def before_hook(req: NetworkHookRequest) -> NetworkHookResult:
    headers = {k: list(v) for k, v in (req.request_headers or {}).items()}
    headers["X-Api-Key"] = [api_key]
    return NetworkHookResult(
        action="mutate",
        request=NetworkHookRequestMutation(headers=headers),
    )

sandbox = (
    Sandbox("python:3.12-alpine")
    .allow_host("api.anthropic.com")
    .with_network_interception(
        NetworkInterceptionConfig(rules=[
            NetworkHookRule(
                name="inject-api-key",
                phase="before",
                hosts=["api.anthropic.com"],
                hook=before_hook,
            )
        ])
    )
)
```

### VFS Interception (Python)

Block, mutate, or audit file operations inside the sandbox:

```python
from matchlock import (
    VFS_HOOK_ACTION_BLOCK, VFS_HOOK_OP_CREATE, VFS_HOOK_OP_WRITE,
    VFS_HOOK_PHASE_BEFORE, VFS_HOOK_PHASE_AFTER,
    Sandbox, VFSHookRule, VFSInterceptionConfig,
    VFSHookEvent, VFSMutateRequest, VFSActionRequest,
)

def mutate_write(req: VFSMutateRequest) -> bytes:
    return b"mutated-by-hook"

def after_write(event: VFSHookEvent) -> None:
    print(f"wrote {event.path} ({event.size} bytes)")

sandbox = (
    Sandbox("alpine:latest")
    .with_workspace("/workspace")
    .mount_memory("/workspace")
    .with_vfs_interception(VFSInterceptionConfig(rules=[
        VFSHookRule(
            phase=VFS_HOOK_PHASE_BEFORE, ops=[VFS_HOOK_OP_CREATE],
            path="/workspace/blocked.txt", action=VFS_HOOK_ACTION_BLOCK,
        ),
        VFSHookRule(
            phase=VFS_HOOK_PHASE_BEFORE, ops=[VFS_HOOK_OP_WRITE],
            path="/workspace/mutated.txt", mutate_hook=mutate_write,
        ),
        VFSHookRule(
            phase=VFS_HOOK_PHASE_AFTER, ops=[VFS_HOOK_OP_WRITE],
            path="/workspace/*", hook=after_write, timeout_ms=2000,
        ),
    ]))
)
```

## Go SDK

Import: `go get github.com/jingkaihe/matchlock/pkg/sdk`

Reference examples are in the `references/go/` directory.

### Sandbox Builder

```go
sandbox := sdk.New("python:3.12-alpine").
    WithCPUs(2).
    WithMemory(1024).
    WithDiskSize(5120).
    WithTimeout(300).
    WithWorkspace("/workspace").
    WithPrivileged().
    AllowHost("api.openai.com", "*.npmjs.org").
    AddHost("api.internal", "10.0.0.10").
    AddSecret("API_KEY", os.Getenv("API_KEY"), "api.openai.com").
    BlockPrivateIPs().
    WithDNSServers("1.1.1.1").
    WithNetworkMTU(1200).
    WithNoNetwork().
    WithEnv("FOO", "bar").
    MountHostDir("/workspace/src", "/home/user/src").
    MountHostDirReadonly("/workspace/cfg", "/etc/cfg").
    MountMemory("/workspace/tmp").
    MountOverlay("/workspace/data", "/home/user/data").
    WithUser("1000:1000").
    WithEntrypoint("python3").
    WithPortForward(8080, 8080)
```

### Client Lifecycle

```go
cfg := sdk.DefaultConfig()
client, err := sdk.NewClient(cfg)
defer client.Remove()
defer client.Close(0)

vmID, err := client.Launch(sandbox)

// Buffered exec
result, err := client.Exec(ctx, "echo hello")
// result.ExitCode, result.Stdout, result.Stderr, result.DurationMS

// Streaming exec
streamResult, err := client.ExecStream(ctx, "long-running-cmd", os.Stdout, os.Stderr)

// Pipe exec (bidirectional stdin/stdout/stderr, no PTY)
pipeResult, err := client.ExecPipe(ctx, "cat", stdinReader, stdoutWriter, stderrWriter)

// Interactive TTY exec
ttyResult, err := client.ExecInteractive(ctx, "sh", &sdk.ExecInteractiveOptions{
    WorkingDir: "/workspace", Rows: 24, Cols: 80,
    Stdin: os.Stdin, Stdout: os.Stdout, Resize: resizeCh,
})

// File operations
client.WriteFile(ctx, "/workspace/hello.txt", []byte("hello"))
client.WriteFileMode(ctx, "/workspace/script.sh", []byte("#!/bin/sh\necho hi"), 0755)
data, err := client.ReadFile(ctx, "/workspace/hello.txt")
files, err := client.ListFiles(ctx, "/workspace")

// Runtime allowlist mutation
client.AllowListAdd(ctx, "api.openai.com", "api.anthropic.com")
client.AllowListDelete(ctx, "api.openai.com")

// Port forwarding
client.PortForward(ctx, 8080, 8080)
```

### Network Interception (Go)

```go
sandbox := sdk.New("python:3.12-alpine").
    AllowHost("api.anthropic.com").
    WithNetworkInterception(&sdk.NetworkInterceptionConfig{
        Rules: []sdk.NetworkHookRule{
            {
                Name:  "inject-api-key",
                Phase: sdk.NetworkHookPhaseBefore,
                Hosts: []string{"api.anthropic.com"},
                Hook: func(_ context.Context, req sdk.NetworkHookRequest) (*sdk.NetworkHookResult, error) {
                    headers := maps.Clone(req.RequestHeaders)
                    headers["X-Api-Key"] = []string{apiKey}
                    return &sdk.NetworkHookResult{
                        Action:  sdk.NetworkHookActionMutate,
                        Request: &sdk.NetworkHookRequestMutation{Headers: headers},
                    }, nil
                },
            },
        },
    })
```

### VFS Interception (Go)

```go
sandbox := sdk.New("alpine:latest").
    WithWorkspace("/workspace").
    MountMemory("/workspace").
    WithVFSInterception(&sdk.VFSInterceptionConfig{
        Rules: []sdk.VFSHookRule{
            {
                Phase:  sdk.VFSHookPhaseBefore,
                Ops:    []sdk.VFSHookOp{sdk.VFSHookOpCreate},
                Path:   "/workspace/blocked.txt",
                Action: sdk.VFSHookActionBlock,
            },
            {
                Phase: sdk.VFSHookPhaseBefore,
                Ops:   []sdk.VFSHookOp{sdk.VFSHookOpWrite},
                Path:  "/workspace/mutated.txt",
                MutateHook: func(ctx context.Context, req sdk.VFSMutateRequest) ([]byte, error) {
                    return []byte("mutated-by-hook"), nil
                },
            },
            {
                Phase:     sdk.VFSHookPhaseAfter,
                Ops:       []sdk.VFSHookOp{sdk.VFSHookOpWrite},
                Path:      "/workspace/*",
                TimeoutMS: 2000,
                Hook: func(ctx context.Context, event sdk.VFSHookEvent) error {
                    fmt.Printf("wrote %s (%d bytes)\n", event.Path, event.Size)
                    return nil
                },
            },
        },
    })
```

## TypeScript SDK

Install: `npm install matchlock-sdk`

Reference examples are in the `references/typescript/` directory.

### Sandbox Builder

```typescript
import { Client, Sandbox } from "matchlock-sdk";

const sandbox = new Sandbox("node:22-alpine")
  .withCPUs(2)
  .withMemory(1024)
  .withDiskSize(5120)
  .withTimeout(300)
  .withWorkspace("/workspace")
  .withPrivileged()
  .allowHost("registry.npmjs.org", "*.npmjs.org", "api.anthropic.com")
  .addHost("api.internal", "10.0.0.10")
  .addSecret("API_KEY", process.env.API_KEY ?? "", "api.anthropic.com")
  .blockPrivateIPs()
  .withDNSServers("1.1.1.1")
  .withNetworkMTU(1200)
  .withNoNetwork()
  .withEnv("FOO", "bar")
  .mountHostDir("/workspace/src", "/home/user/src")
  .mountHostDirReadonly("/workspace/cfg", "/etc/cfg")
  .mountMemory("/workspace/tmp")
  .mountOverlay("/workspace/data", "/home/user/data")
  .withUser("1000:1000")
  .withEntrypoint("node")
  .withPortForward(8080, 8080);
```

### Client Lifecycle

```typescript
const client = new Client();

try {
  const vmId = await client.launch(sandbox);

  // Buffered exec
  const result = await client.exec("echo hello");
  // result.exitCode, result.stdout, result.stderr, result.durationMs

  // Streaming exec
  const stream = await client.execStream("long-running-cmd", {
    stdout: process.stdout,
    stderr: process.stderr,
  });

  // Pipe exec (bidirectional stdin/stdout/stderr)
  const pipe = await client.execPipe("cat", {
    stdin: [Buffer.from("input\n")],
    stdout: (chunk) => process.stdout.write(chunk),
    stderr: (chunk) => process.stderr.write(chunk),
  });

  // Interactive TTY exec
  const tty = await client.execInteractive("sh", {
    stdin: process.stdin,
    stdout: process.stdout,
    rows: 24,
    cols: 80,
  });

  // File operations
  await client.writeFile("/workspace/hello.txt", "hello");
  const data = await client.readFile("/workspace/hello.txt");
  const files = await client.listFiles("/workspace");

  // Runtime allowlist mutation
  await client.allowListAdd("api.openai.com", "api.anthropic.com");
  await client.allowListDelete("api.openai.com");

  // Port forwarding
  await client.portForward(8080, 8080);
} finally {
  await client.close();
  await client.remove();
}
```

### Network Interception (TypeScript)

```typescript
import { Client, type NetworkHookRequest, type NetworkHookResult, Sandbox } from "matchlock-sdk";

const apiKey = process.env.ANTHROPIC_API_KEY ?? "";

const sandbox = new Sandbox("python:3.12-alpine")
  .allowHost("api.anthropic.com")
  .withNetworkInterception({
    rules: [
      {
        name: "inject-api-key",
        phase: "before",
        hosts: ["api.anthropic.com"],
        hook: async (req: NetworkHookRequest): Promise<NetworkHookResult> => {
          const headers = { ...(req.requestHeaders ?? {}) };
          headers["X-Api-Key"] = [apiKey];
          return { action: "mutate", request: { headers } };
        },
      },
    ],
  });
```

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

## References

Complete working examples are in the `references/` directory. Each subdirectory has a README with full setup instructions.

### SDK Examples

| Directory | Language | Covers |
|-----------|----------|--------|
| `references/python/` | Python | basic, exec_modes, network_interception, vfs_hooks |
| `references/go/` | Go | basic, exec_modes, network_interception, vfs_hooks |
| `references/typescript/` | TypeScript | basic, exec_modes, network_interception |

### AI Agent Examples

| Directory | Description |
|-----------|-------------|
| `references/claude-code/` | Claude Code in a micro-VM with GitHub repo bootstrap and secret injection |
| `references/claude-code-with-docker/` | Claude Code + Docker daemon in a privileged sandbox (Python SDK) |
| `references/codex/` | OpenAI Codex in a micro-VM with GitHub repo bootstrap and secret injection |

### Infrastructure Examples

| Directory | Description |
|-----------|-------------|
| `references/docker-in-sandbox/` | Full Docker daemon in a privileged micro-VM (systemd + vfs driver) |
| `references/agent-client-protocol/` | Streamlit chatbot running a Kodelet agent over ACP via stdin/stdout |
