# Qwen3.5 on Strix Halo

Use this reference when the user is specifically asking about Qwen3.5 models on Strix Halo.

This is a worked example based on prior experiments with:

- `unsloth/Qwen3.5-35B-A3B-GGUF:BF16`
- `unsloth/Qwen3.5-35B-A3B-GGUF:Q4_K_M`
- `docker.io/kyuz0/amd-strix-halo-toolboxes:vulkan-radv`

## Recommended split

- use **toolbox** to inspect devices, run `gguf-vram-estimator.py`, and benchmark with `llama-bench`
- use **podman** to run a reproducible long-lived `llama-server`

## Toolbox flow

Create the toolbox:

```bash
toolbox create llama-vulkan-radv \
  --image docker.io/kyuz0/amd-strix-halo-toolboxes:vulkan-radv \
  -- --device /dev/dri --group-add video --security-opt seccomp=unconfined
```

Check device visibility:

```bash
toolbox run -c llama-vulkan-radv llama-cli --list-devices
```

Important note:

- on Vulkan RADV, `bf16: 0` in the device listing does **not** mean BF16 model weights cannot run

Estimate memory from the first shard:

```bash
toolbox run -c llama-vulkan-radv \
  gguf-vram-estimator.py /path/to/Qwen3.5-35B-A3B-BF16-00001-of-00002.gguf \
  --contexts 16384 32768 65536 131072 262144
```

## Qwen3.5 serving defaults

Be conservative with memory while testing this model.

- avoid running multiple heavy benchmarks at once
- avoid benchmarking while another large server process is already resident unless the user explicitly wants that comparison
- if a long benchmark is needed, use the `tmux` skill if available so you can monitor and stop it cleanly

For normal interactive usage on Strix Halo, use:

```bash
--jinja \
--chat-template-kwargs '{"enable_thinking":false}' \
--reasoning off
```

Rationale:

- Qwen3.5 thinks by default
- on this hardware it often thinks too much for day-to-day interactive usage
- disabling thinking keeps responses tighter and reduces the feeling of slowness

If the user explicitly wants full reasoning behaviour instead, use:

```bash
--chat-template-kwargs '{"enable_thinking":true}' \
--reasoning on
```

## Reproducible podman example

```bash
podman run -d \
  --restart=always \
  --name=qwen3.5-35b-a3b-bf16 \
  --device /dev/dri \
  --group-add video \
  --security-opt seccomp=unconfined \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  -p 8080:8080 \
  docker.io/kyuz0/amd-strix-halo-toolboxes:vulkan-radv \
  llama-server \
    -hf unsloth/Qwen3.5-35B-A3B-GGUF:BF16 \
    --host 0.0.0.0 \
    --ctx-size 16384 \
    --no-mmproj \
    --no-mmap \
    -ngl 999 \
    -fa on \
    --jinja \
    --chat-template-kwargs '{"enable_thinking":false}' \
    --reasoning off \
    --temp 0.6 \
    --top-p 0.95 \
    --top-k 20 \
    --min-p 0.0 \
    -a qwen3.5-35b-a3b-bf16
```

If the user needs auth, `llama-server` supports `--api-key KEY` and `--api-key-file FNAME`. The built-in Web UI uses `Authorization: Bearer <key>` and stores the key in browser localStorage.

## Performance reference

These numbers are useful as orientation, not as a universal guarantee.

On the tested Vulkan RADV setup:

- `Qwen3.5-35B-A3B BF16` was about **10 to 11 tok/s**
- `Qwen3.5-35B-A3B Q4_K_M` was about **4x faster** in short generation and much lighter on memory

Use this reference to explain the trade-off:

- BF16 for maximum local quality
- Q4_K_M for responsiveness

## Operational advice

- start with **16K** or **32K** context
- use `--no-mmproj` for text-only serving
- prefer an explicit cached `-m /path/to/model.gguf` when the user wants deterministic offline runs after the first download
