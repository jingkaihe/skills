# `uv_build` build backend

Use this when the user wants `uv` to be the project's PEP 517 build backend.

## 1) Recommended path

```bash
# Recommended setup
uv init --package --build-backend uv
uv build
```

Use the manual `pyproject.toml` path only when the project already exists or needs to be updated in place.

## 2) Manual `pyproject.toml`

```toml
# Manual setup
[build-system]
requires = ["uv_build>=0.11.2,<0.12"]
build-backend = "uv_build"

# Optional layout customization
[tool.uv.build-backend]
module-name = "FOO"
module-root = ""
```

## 3) Notes

- Backend package and entrypoint: `uv_build`
- `uv build` builds source distributions and wheels.
- `uv` can use its bundled backend if it satisfies the declared `uv_build` requirement.
- Other frontends like `python -m build` use the installed `uv_build` package.
- `uv_build` currently supports pure Python projects only.

## 4) Expected layout

```text
src/<normalized_package_name>/__init__.py
```

The package name is normalized to lowercase and `.` / `-` become `_`.
