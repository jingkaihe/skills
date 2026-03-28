---
name: uv
description: Use Astral uv for Python command execution, PEP 723 inline script metadata, script locking/export/auditing/compilation, and configuring the `uv_build` backend in `pyproject.toml`. Trigger on mentions of uv, Astral uv, `uv run`, inline script metadata, PEP 723, `uv_build`, or Python build backend setup.
---

# uv -- `uv run`, inline script dependencies, and `uv_build`

Use this skill when the user wants help with Astral `uv`. This guide focuses on three tasks: `uv run`, PEP 723 inline script dependencies, and setting up `uv` as a build backend.

## Key rules

1. Put all `uv run` options before the command. Use `--` to make the split explicit.
2. Use `uv run --with ...` for one-off dependencies and `uv add --script ...` to persist dependencies into a script.
3. For reproducible scripts, lock them with `uv lock --script`. `uv audit --script` requires a lock first.

Examples:

```bash
uv run --python 3.12 -- python
uv run --verbose -- pytest -q
```

## 1) `uv run` quick reference

`uv run` runs a command or script in a Python environment. If the argument is a `.py` file or an HTTP(S) URL, `uv` treats it as a Python script automatically. If you pass `-`, `uv` reads a Python script from stdin.

### Common patterns

Run a script:

```bash
uv run file.py
uv run -s ./script-without-py-extension
```

Run a Python module:

```bash
uv run -m http.server
uv run -m package.module
```

Run an arbitrary command inside the environment:

```bash
uv run -- python --version
uv run --verbose -- pytest -q
uv run -- ruff check
```

Pick a Python version:

```bash
uv run -p 3.12 -- python
uv run --python 3.11 example.py
```

Run from stdin:

```bash
echo 'print("hello world!")' | uv run -
```

Add temporary dependencies without editing metadata:

```bash
uv run --with rich example.py
uv run --with 'rich>12,<13' example.py
```

Force isolation from the current project/workspace:

```bash
uv run --no-project -- python -V
```

### Notes

- In a project, `uv run` uses the project environment.
- If a script has inline metadata, `uv run` installs those dependencies into an isolated ephemeral environment and ignores project dependencies.
- `--script` forces a path to be treated as a Python script even if it does not end in `.py`.

## 2) Inline script dependencies (PEP 723)

Use inline metadata when the user wants a single-file script with declared dependencies.

### Bootstrap a script

Create metadata for a script:

```bash
uv init --script example.py --python 3.12
```

Minimal inline metadata block:

```python
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
```

Example with dependencies:

```python
# /// script
# dependencies = [
#   "requests<3",
#   "rich",
# ]
# ///
```

### Manage script dependencies

Add dependencies:

```bash
uv add --script example.py 'requests<3' rich
```

Remove dependencies:

```bash
uv remove --script example.py rich
```

Run the script:

```bash
uv run example.py
```

Use a specific Python version for the run:

```bash
uv run --python 3.10 example.py
```

Add an alternative index while editing script metadata:

```bash
uv add --index "https://example.com/simple" --script example.py 'requests<3' rich
```

### Lock, inspect, export, sync, and audit

Lock the script:

```bash
uv lock --script example.py
```

Show the dependency tree:

```bash
uv tree --script example.py
```

Export dependencies:

```bash
uv export --script example.py
```

Sync an environment from the script metadata:

```bash
uv sync --script example.py
```

Audit the locked script dependencies:

```bash
uv audit --script example.py
```

Notes:

- `uv run --script`, `uv add --script`, `uv export --script`, and `uv tree --script` reuse or update the adjacent lockfile if one exists.
- If no lockfile exists, `uv export --script` still works, but it does not create one.
- `uv audit --script` requires the script to be locked first with `uv lock --script <script>`.

### Compilation / verification patterns

There are two different "compile" concepts in `uv`:

- `uv pip compile` resolves and writes pinned dependency artifacts.
- `--compile-bytecode` compiles installed Python files to bytecode after installation.

Compile installed files to bytecode after install:

```bash
uv run --compile-bytecode example.py
uv sync --script example.py --compile-bytecode
```

Compile a script's inline metadata into a requirements file:

```bash
uv pip compile example.py --output-file requirements.txt
```

### Decision guide

- Want a one-off dependency for a single run? Use `uv run --with`.
- Want to persist dependencies in the script? Use `uv add --script`.
- Want reproducibility? Use `uv lock --script`.
- Want to inspect resolved deps? Use `uv tree --script`.
- Want a text export of resolved deps? Use `uv export --script`.
- Want vulnerability checking? Use `uv audit --script` after locking.
- Want a compiled requirements artifact from a PEP 723 script? Use `uv pip compile` with the `.py` file as input.

### Extras

Executable shebang for script files:

```python
#!/usr/bin/env -S uv run --script
```

Reproducibility cutoff in inline metadata:

```python
# /// script
# dependencies = ["requests"]
# [tool.uv]
# exclude-newer = "2023-10-16T00:00:00Z"
# ///
```

## 3) Set up `uv` as a build backend

### Fastest setup

Create a package project with `uv` as the backend:

```bash
uv init --package --build-backend uv
```

Then build it:

```bash
uv build
```

Useful build variants:

```bash
uv build --sdist
uv build --wheel
uv build --sdist --wheel
```

### Manual `pyproject.toml` setup

Add this to `pyproject.toml`:

```toml
[build-system]
requires = ["uv_build>=0.11.2,<0.12"]
build-backend = "uv_build"
```

### Important notes

- Backend name: `uv_build`
- The recommended upper bound follows uv's versioning policy.
- The `uv` executable ships with a bundled backend and uses it if it satisfies the declared `uv_build` requirement.
- Other frontends, like `python -m build`, use the installed `uv_build` package.
- The `uv` build backend currently supports pure Python only. If the project has extension modules, choose another backend such as `maturin`, `scikit-build-core`, or another appropriate backend.

### Default layout and customization

By default, `uv_build` expects:

```text
src/<normalized_package_name>/__init__.py
```

You can override the module name or module root:

```toml
[tool.uv.build-backend]
module-name = "FOO"
module-root = ""
```

## Practical workflows

### One-file script with locked deps

```bash
uv init --script demo.py --python 3.12
uv add --script demo.py requests rich
uv lock --script demo.py
uv run demo.py
uv tree --script demo.py
```

### Package project using `uv_build`

```bash
uv init --lib my_pkg --build-backend uv
uv build
```
