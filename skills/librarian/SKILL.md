---
name: librarian
description: "Maintain a local cache of remote Git repositories (GitHub, GitLab, Bitbucket, etc.) for code research and exploration. Use when users share a GitHub/GitLab URL, ask to look at a remote codebase, reference an open-source project by name (e.g. 'look at how kubernetes does X'), or want to compare code across repos. Always use this instead of fetching individual files via HTTP or cloning manually."
---

# Librarian — Local Cache for Remote Repositories

Keep a local, up-to-date mirror of remote Git repositories under `~/.cache/librarian/` so you can read and search code without re-cloning every time.

## How It Works

Each repository is cached at `~/.cache/librarian/<owner>/<repo>`. A metadata file at `~/.cache/librarian/<owner>/<repo>/.git/librarian_meta.json` tracks when the last fetch happened. Before reading any file from a cached repo, ensure the cache is fresh — if it's stale (older than the freshness window), do a `git fetch && git reset`.

## Step-by-step: Ensure a Repo Is Cached and Fresh

Use the bundled helper script for all cache operations. It handles cloning, freshness checks, and updates in one call:

```bash
bash <skill-dir>/scripts/ensure-repo.sh <clone-url> [freshness-seconds]
```

- `<clone-url>` — Any valid Git remote URL (HTTPS or SSH).
  - If the user gives you just `owner/repo`, expand to `https://github.com/<owner>/<repo>.git`.
  - If the user gives a full GitHub/GitLab URL like `https://github.com/owner/repo`, append `.git` if needed.
- `[freshness-seconds]` — Optional. How many seconds a cache is considered fresh. **Default: 600** (10 minutes). Pass `0` to force a refresh.

The script prints the absolute path to the local repo directory on success. Use that path for all subsequent file reads and searches.

### Examples

```bash
# Cache a repo with default 10-minute freshness
LOCAL=$(bash <skill-dir>/scripts/ensure-repo.sh https://github.com/kubernetes/kubernetes.git)
# Read files from it
cat "$LOCAL/README.md"

# Force refresh
bash <skill-dir>/scripts/ensure-repo.sh https://github.com/hashicorp/terraform.git 0

# Use 1-hour freshness
bash <skill-dir>/scripts/ensure-repo.sh https://github.com/golang/go.git 3600
```

## After Caching

Once you have the local path, use your normal file-reading and search tools to explore the code:

- `File_read` to read specific files
- `Grep_tool` to search across the codebase
- `Glob_tool` to find files by pattern

The local path is a regular Git working tree, so you can also run `git log`, `git diff`, `git branch -r`, etc. inside it.

## Switching Branches or Tags

By default the cache tracks the remote's default branch. To check out a specific branch or tag:

```bash
LOCAL=$(bash <skill-dir>/scripts/ensure-repo.sh https://github.com/owner/repo.git)
(cd "$LOCAL" && git checkout origin/<branch-name>)
```

For tags:
```bash
(cd "$LOCAL" && git checkout tags/<tag-name>)
```

## Rules

1. **Always run `ensure-repo.sh` before reading any files** from a remote repo — even if you think it's already cached. The script is fast when the cache is fresh (just a timestamp check).
2. **Never modify files** in the cached repos. These are read-only mirrors for research.
3. **Use HTTPS URLs by default** unless the user specifically provides an SSH URL.
4. **Respect freshness** — if the user says "get the latest", pass `0` as freshness to force a refresh.
5. **Shallow clone** — the script uses `--depth 1` by default to save disk space and time. If the user needs full history (e.g., for `git log` or `git blame`), run `(cd "$LOCAL" && git fetch --unshallow)` after the initial cache.
