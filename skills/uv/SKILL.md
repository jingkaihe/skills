---
name: uv
description: Use `uv` as the default Python tool for package management, environments, and self-contained scripts instead of separate `pip`, `venv`, `poetry`, or raw `python` workflows. Trigger on mentions of uv, Python dependency management, `uv run`, inline script metadata, PEP 723, virtualenv setup, or `uv_build` backend setup.
---

Use this skill when the user wants help with Astral `uv`. Keep the main flow short, then use the reference docs for deeper details.

## Quick ref

Use `uv run` to run scripts, modules, or commands inside a Python environment.

```bash
# Run a script
uv run file.py
uv run -s ./script-without-py-extension

# Run a command in the environment
uv run -- python --version
uv run -- ruff check
uv run -- pytest -q

# Pick a Python version
uv run -p 3.12 -- python
uv run --python 3.11 example.py

# Add one-off dependencies
uv run --with rich example.py
uv run --with 'rich>12,<13' example.py

# Run ad-hoc tools with --with
uv run --with ruff -- ruff check example.py
uv run --with pyright -- pyright example.py
```

Key rule: put all `uv` options before the command, and prefer `--` when you want the split to be explicit.

## Inline script dependencies

Use PEP 723 inline metadata when the user wants a single-file script with declared dependencies.

```bash
# Initialize a script
uv init --script example.py --python 3.12

# Add or remove dependencies
uv add --script example.py requests rich
uv remove --script example.py rich

# Run and lock
uv run example.py
uv lock --script example.py

# Inspect, export, sync, audit
uv tree --script example.py
uv export --script example.py
uv sync --script example.py
uv audit --script example.py

# Syntax / verification patterns
uv run -- python -m ast example.py >/dev/null
uv lock --script example.py
uv audit --script example.py
```

Use `uv run --with` for temporary deps; use `uv add --script` when the dependencies should live in the script.

For full details, read `references/inline-script-dependencies.md`.

## Build backend

Be opinionated here: for a new package, prefer `uv init --package --build-backend uv`.

```bash
# Recommended setup
uv init --package --build-backend uv
uv build
```

```toml
# Manual pyproject.toml setup
[build-system]
requires = ["uv_build>=0.11.2,<0.12"]
build-backend = "uv_build"
```

Important note: `uv_build` currently supports pure Python projects only.

For full details, read `references/build-backend.md`.
