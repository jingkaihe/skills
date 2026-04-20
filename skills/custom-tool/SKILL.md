---
name: custom-tool
description: Create Kodelet custom executable tools that implement the custom tool protocol. Use whenever the user wants a Kodelet custom tool, asks to scaffold or generate one, mentions the custom tool protocol, or wants reusable automation exposed as a Kodelet tool.
---

# Kodelet Custom Tool Generator

This skill exists to produce a working custom tool, not just describe one. When it triggers, create the executable, put it in the right tools directory, make it runnable, and verify the protocol.

## Choose the shape first

Make these decisions up front:

1. Decide whether the tool should be **local** or **global**.
   - Default to **local** when the user does not say.
   - Local default directory: `./.kodelet/tools/`
   - Global default directory: `~/.kodelet/tools/`
   - If the user is developing a Kodelet plugin, use the plugin repo's `./tools/` directory instead.
   - If the workspace or user config overrides `custom_tools.local_dir` or `custom_tools.global_dir`, honor that override.
2. Choose a **snake_case** file name and tool name.
   - Example: `analyze log files` -> `analyze_log_files`
   - Do **not** include the `custom_tool_` prefix in the script metadata. Kodelet adds that prefix when registering the tool.
3. Choose the implementation language.
   - Prefer **Python with `uv`** for almost everything.
   - Use **Bash** only for tiny shell-native tools with minimal parsing and logic.

## Directory selection rule

Choose the destination directory in this order:

1. If the user explicitly asks for a plugin-bundled tool, or the current repo is clearly a Kodelet plugin repo, write the executable to `./tools/`.
2. Otherwise, if the user explicitly asks for a global tool, write it to `~/.kodelet/tools/` or the configured global custom tools directory.
3. Otherwise, write it to `./.kodelet/tools/` or the configured local custom tools directory.

Signals that usually mean **plugin development**:

- the repo already has top-level `skills/`, `recipes/`, `tools/`, or `hooks/` directories
- the user says they are building a plugin, plugin repo, or plugin-bundled tool
- the tool is meant to ship via `kodelet plugin add ...`

When writing to `./tools/`, treat it as the final plugin artifact. Do not also create a duplicate copy under `./.kodelet/tools/` unless the user asks for both.

## Protocol requirements

Every custom tool must be an executable that supports exactly these entrypoints:

1. `<tool> description`
   - Prints JSON describing the tool.
   - Must include `name`, `description`, and `input_schema`.
2. `<tool> run`
   - Reads JSON input from stdin.
   - Executes the tool logic.
   - Writes useful output to stdout.

The `description` payload should look like:

```json
{
  "name": "my_tool",
  "description": "What the tool does",
  "input_schema": {
    "type": "object",
    "properties": {
      "input": {
        "type": "string",
        "description": "Example parameter"
      }
    },
    "required": ["input"]
  }
}
```

Design the schema around the actual task. Do not keep a generic `input` field if the tool needs more specific parameters.

## Preferred implementation: Python with uv

Use this as the default starting point:

```python
#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

import json
import sys


def get_description() -> dict:
    return {
        "name": "my_tool",
        "description": "Describe the tool clearly",
        "input_schema": {
            "type": "object",
            "properties": {
                "input": {
                    "type": "string",
                    "description": "Input data"
                }
            },
            "required": ["input"]
        },
    }


def run_tool() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(json.dumps({"error": f"invalid JSON input: {exc}"}))
        return 1

    try:
        result = {
            "status": "success",
            "result": f"processed: {payload['input']}"
        }
        print(json.dumps(result, indent=2))
        return 0
    except KeyError as exc:
        print(json.dumps({"error": f"missing required parameter: {exc.args[0]}"}))
        return 1
    except Exception as exc:
        print(json.dumps({"error": f"tool execution failed: {exc}"}))
        return 1


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: my_tool [description|run]", file=sys.stderr)
        return 1

    command = sys.argv[1]
    if command == "description":
        print(json.dumps(get_description(), indent=2))
        return 0
    if command == "run":
        return run_tool()

    print(f"Unknown command: {command}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
```

## Bash template for very small tools

Use Bash only when the task is mostly shell commands and simple argument handling:

```bash
#!/usr/bin/env bash

set -euo pipefail

case "${1:-}" in
  description)
    cat <<'EOF'
{
  "name": "my_tool",
  "description": "Describe the tool clearly",
  "input_schema": {
    "type": "object",
    "properties": {
      "input": {
        "type": "string",
        "description": "Input data"
      }
    },
    "required": ["input"]
  }
}
EOF
    ;;
  run)
    input_json=$(cat)
    input_value=$(printf '%s' "$input_json" | jq -r '.input // empty')
    if [[ -z "$input_value" ]]; then
      printf '{"error":"missing required parameter: input"}\n'
      exit 1
    fi

    printf '{"status":"success","result":%s}\n' "$(printf '%s' "processed: $input_value" | jq -Rsa .)"
    ;;
  *)
    echo "Usage: $0 {description|run}" >&2
    exit 1
    ;;
esac
```

If the Bash version starts needing real parsing, HTTP calls, multiple data structures, or non-trivial validation, switch to Python.

## Implementation rules

1. Create the parent tools directory if it does not exist.
2. Write the executable directly to the final path.
3. Make it executable with `chmod +x`.
4. Keep the tool focused on one job.
5. Use task-specific parameters instead of a vague catch-all schema.
6. Return structured, useful output.
7. Handle invalid JSON, missing required fields, and execution failures cleanly.
8. Do not create README files or extra documentation unless the user asks.
9. For plugin development, place the executable in `./tools/` so it is discoverable as a plugin-bundled tool.

## Verification checklist

After creating the tool, always run all three checks:

```bash
<tool-path> description
echo '<valid-json>' | <tool-path> run
echo '{}' | <tool-path> run
```

Confirm that:

1. `description` prints valid JSON with the expected schema.
2. A normal `run` invocation succeeds.
3. A bad invocation fails with a clear message.

## Response pattern

When using this skill:

1. Create the tool instead of only suggesting one.
2. Tell the user where the executable was written.
3. Briefly note the chosen interface and why.
4. Report the verification commands you ran and whether they passed.
