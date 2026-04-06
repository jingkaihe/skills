---
name: strix-halo-llm
description: Run, benchmark, and serve local GGUF / llama.cpp models on AMD Strix Halo systems such as Framework Desktop using kyuz0 toolboxes, unified-memory sizing, and reproducible podman commands. Use this skill for Strix Halo local LLM work, especially llama.cpp setup, memory-fit or performance tuning, toolbox benchmarking, and podman-based serving.
---

# Strix Halo LLMs with llama.cpp

Use this skill to help users run, benchmark, and serve GGUF models on AMD Strix Halo hardware.

The core pattern is:

- use **toolbox** for discovery, benchmarking, and memory estimation
- use **podman** for reproducible long-running serving

This keeps experimentation and operations separate.

## Backend choice

Start by choosing the right backend.

| Backend | When to prefer it | Notes |
| --- | --- | --- |
| `vulkan-radv` | Default recommendation | Best balance of compatibility and simplicity. Use this unless the user specifically needs max BF16 throughput. |
| `rocm-7.2` or similar ROCm toolbox | User wants the fastest BF16 path | More moving parts than Vulkan, but generally better BF16 throughput on Strix Halo. |
| `vulkan-amdvlk` | Only if the user explicitly wants to try it | Can be fast, but large models may fail because of the single-buffer allocation limit. |

If the user is unsure, recommend `vulkan-radv` first.

## Workflow

Follow this sequence.

### 1. Confirm the user goal

Figure out whether the user wants:

- quick interactive experimentation
- memory-fit estimation
- benchmarking
- a stable background server
- maximum quality or maximum responsiveness

If the user says "optimal", clarify whether they mean **quality**, **latency**, or **operational simplicity**.

### 2. Use toolbox for discovery and experiments

Recommend the toolbox workflow for one-off checks and tuning:

```bash
toolbox create llama-vulkan-radv \
  --image docker.io/kyuz0/amd-strix-halo-toolboxes:vulkan-radv \
  -- --device /dev/dri --group-add video --security-opt seccomp=unconfined

toolbox enter llama-vulkan-radv
```

If `toolbox enter` has terminal issues, use non-interactive commands instead:

```bash
toolbox run -c llama-vulkan-radv llama-cli --list-devices
```

Use toolbox for:

- `llama-cli --list-devices`
- `gguf-vram-estimator.py`
- `llama-bench`
- quick one-shot `llama-cli` tests

Do **not** position toolbox as the final serving story if the user wants something reproducible. For that, prefer `podman run`.

### 3. Check device visibility

Run:

```bash
toolbox run -c llama-vulkan-radv llama-cli --list-devices
```

Important interpretation notes:

- `uma: 1` is expected and good on Strix Halo
- `bf16: 0` on Vulkan RADV does **not** mean BF16 weights cannot run
- it **does** mean you should not casually assume BF16 KV cache is a good default on Vulkan

### 4. Estimate memory before committing to context size

Always use the estimator before recommending large contexts or BF16 on big models:

```bash
toolbox run -c llama-vulkan-radv \
  gguf-vram-estimator.py /path/to/model-or-first-shard.gguf --contexts 16384 32768 65536 131072
```

Rules:

- for multipart GGUFs, pass the **first shard**
- leave margin for the OS and background processes
- avoid running multiple heavy benchmarks or servers at the same time
- long context costs memory even on unified memory
- if the machine is already carrying substantial memory usage, reduce context size before benchmarking or serving

Safe default guidance:

- start with **16K** or **32K** context
- only push higher after estimating memory usage

### 5. Benchmark before calling something "optimal"

Use `llama-bench` to measure both short generation and prefilled-context behaviour.

If the benchmark is likely to run for a while, or if you want to poll output safely without blocking the main session, invoke the `tmux` skill if available and run the benchmark there.

Short benchmark:

```bash
toolbox run -c llama-vulkan-radv \
  llama-bench -m /path/to/model.gguf -p 512 -n 128 -ngl 999 -fa 1 -mmp 0 -r 1 -o md
```

Long-context benchmark:

```bash
toolbox run -c llama-vulkan-radv \
  llama-bench -m /path/to/model.gguf -p 2048 -n 32 -d 16384 -ngl 999 -fa 1 -mmp 0 -r 1 -o md
```

Use these numbers to explain trade-offs clearly.

Benchmarking rules:

- prefer **one heavy run at a time** on Strix Halo
- do not leave an existing large `llama-server` process running while starting another heavy benchmark unless the user explicitly wants that
- clean up background benchmark sessions after collecting the results
- treat long-context BF16 benchmarks as memory-sensitive operations

### 6. Use podman for the final serving recommendation

For reproducible serving, prefer a container like this:

```bash
podman run -d \
  --restart=always \
  --name=my-model \
  --device /dev/dri \
  --group-add video \
  --security-opt seccomp=unconfined \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  -v ~/.cache/llama.cpp:/root/.cache/llama.cpp \
  -p 8080:8080 \
  docker.io/kyuz0/amd-strix-halo-toolboxes:vulkan-radv \
  llama-server \
    -hf user/model:QUANT \
    --host 0.0.0.0 \
    --ctx-size 16384 \
    --no-mmap \
    -ngl 999 \
    -fa on \
    --jinja \
    -a my-model
```

Add model-specific flags as needed.

## Strix Halo llama.cpp defaults

These are the baseline defaults to reach for on Strix Halo.

### Core flags

- `--no-mmap`
- `-ngl 999`
- `-fa on`
- `--ctx-size 16384` or `32768` unless the estimator supports more comfortably

Treat these as the default baseline for large GGUFs on this hardware.

### Text-only multimodal repos

If the repo exposes an `mmproj` file but the user only wants text generation, add:

```bash
--no-mmproj
```

This avoids unnecessary memory use and makes the deployment intent explicit.

### Model-specific references

Keep the main skill general. For concrete model recipes and worked examples, read the relevant reference file when needed.

- Qwen3.5 reference: `references/qwen3-5.md`

Use the reference file when the user explicitly asks about Qwen3.5, Qwen3.5 GGUFs, thinking toggles, or wants a concrete Strix Halo command for that model family.

### Deterministic offline runs

After the first download, prefer an explicit cached model path over `-hf` when the user wants deterministic local runs or offline usage:

```bash
-m ~/.cache/huggingface/hub/.../model.gguf
```

This avoids surprises from repo preset lookups and makes it obvious which exact shard set is being loaded.

## Performance heuristics

Use these heuristics when explaining trade-offs.

### BF16 on Vulkan RADV

Treat BF16 on `vulkan-radv` as:

- **quality-first**
- **memory-heavy**
- **usable, but not fast**

From the tested setup behind this skill, `unsloth/Qwen3.5-35B-A3B-GGUF:BF16` on Vulkan RADV was about **10 to 11 tok/s**, while the exact same model family in `Q4_K_M` was roughly **4x faster** and much smaller in memory footprint. Treat that as a useful reference example for explaining BF16 vs quantized trade-offs on Strix Halo. See `references/qwen3-5.md` for the full worked example.

Do not overgeneralize those exact numbers. Use the pattern:

- BF16 for maximum local quality
- quantized variants for responsiveness

### ROCm guidance

If the user wants maximum BF16 throughput, tell them ROCm is worth considering. Phrase it as a trade-off:

- Vulkan RADV is easier and more compatible
- ROCm is usually the better answer for BF16 speed

## System-level caveats

For very large models, success depends not just on model size but also on host unified-memory tuning.

Do **not** hardcode kernel or firmware advice unless you have current confirmation.

Instead:

- tell the user to check the current `kyuz0/amd-strix-halo-toolboxes` README for the latest host configuration guidance
- mention that large-model viability depends on GTT and pinned-memory settings
- remind them that unified memory is shared with the OS and all other processes, so real available headroom is lower than total installed RAM

## Response pattern

Keep answers practical and command-first:

1. state the recommended backend and workflow: toolbox for exploration, podman for serving
2. give one copy-paste command block, usually a final `podman run` command
3. explain only the important flags: `--no-mmap`, `-fa on`, `-ngl 999`, `--no-mmproj` if applicable, and any model-specific toggles
4. call out memory-fit risk and suggest the estimator or a smaller context if needed
5. if the user asks for "optimal", make the quality vs latency trade-off explicit
6. if benchmarking will be long-running, use the `tmux` skill if available and avoid concurrent heavy runs
