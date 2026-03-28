# Inline script dependencies with `uv`

Use this when the user wants a single-file Python script with PEP 723 inline metadata.

## 1) Core workflow

```bash
# Initialize a script
uv init --script example.py --python 3.12

# Run a script
uv run example.py
uv run --python 3.10 example.py
uv run -s ./script-without-py-extension
echo 'print("hello world!")' | uv run -

# Use temporary dependencies
uv run --with rich example.py
uv run --with 'rich>12,<13' example.py

# Run ad-hoc tools with --with
uv run --with ruff -- ruff check example.py
uv run --with pyright -- pyright example.py

# Persist dependencies in the script
uv add --script example.py 'requests<3' rich
uv remove --script example.py rich
uv add --index "https://example.com/simple" --script example.py 'requests<3' rich

# Lock, inspect, export, sync, audit
uv lock --script example.py
uv tree --script example.py
uv export --script example.py
uv sync --script example.py
uv audit --script example.py

# Syntax / verification patterns
uv run -- python -m ast example.py >/dev/null
uv lock --script example.py
uv audit --script example.py
```

## 2) Metadata patterns

These examples are intentionally explicit. The `# /// script` block is comment-prefixed TOML-style metadata inside the Python file. The shebang, when used, is separate and goes above the metadata block.

### Minimal inline metadata

```python
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
```

### Inline metadata with dependencies

```python
# /// script
# dependencies = [
#   "requests<3",
#   "rich",
# ]
# ///
```

### Inline metadata with reproducibility cutoff

```python
# /// script
# dependencies = ["requests"]
# [tool.uv]
# exclude-newer = "2023-10-16T00:00:00Z"
# ///
```

### Inline metadata with an alternative package index

Keep the index config inside the same `# /// script` block:

```python
# /// script
# dependencies = ["requests<3"]
# [[tool.uv.index]]
# url = "https://example.com/simple"
# ///
```

### Executable shebang

The shebang is separate from the metadata block and belongs at the top of the file:

```python
#!/usr/bin/env -S uv run --script

# /// script
# dependencies = ["rich"]
# ///
```

## 3) Opinionated notes

- Use `uv run --with ...` for one-off deps.
- Use `uv add --script ...` when deps should live in the script.
- Use `uv run -- python -m ast <script> >/dev/null` for a cheap syntax-only check that avoids creating `__pycache__`.
- Use `uv lock --script ...` for reproducibility.
- `uv audit --script ...` requires `uv lock --script <script>` first.
- `uv export --script ...` works without a lockfile, but does not create one.
- `uv run --script`, `uv add --script`, `uv export --script`, and `uv tree --script` reuse or update the adjacent lockfile if one exists.
