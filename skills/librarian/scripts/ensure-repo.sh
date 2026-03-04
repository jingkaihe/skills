#!/usr/bin/env bash
# ensure-repo.sh — Clone or refresh a cached Git repo.
# Usage: ensure-repo.sh <clone-url> [freshness-seconds]
# Prints the local repo path on stdout. Logs to stderr.
set -euo pipefail

CACHE_ROOT="${HOME}/.cache/librarian"
CLONE_URL="${1:?Usage: ensure-repo.sh <clone-url> [freshness-seconds]}"
FRESHNESS="${2:-600}"

# Parse owner/repo from URL
p="$CLONE_URL"
[[ "$p" == git@* ]] && p="${p#*:}" || { p="${p#*://}"; p="${p#*/}"; }
p="${p%.git}"; p="${p%/}"
OWNER="${p%%/*}"
REPO="${p#*/}"
[[ -n "$OWNER" && -n "$REPO" ]] || { echo "Error: cannot parse owner/repo from $CLONE_URL" >&2; exit 1; }

REPO_DIR="${CACHE_ROOT}/${OWNER}/${REPO}"
META="${REPO_DIR}/.git/librarian_meta.json"

write_meta() { echo "{\"last_fetched\":$(date +%s)}" > "$META"; }

# Clone if missing
if [[ ! -d "$REPO_DIR/.git" ]]; then
  echo "Cloning $CLONE_URL ..." >&2
  mkdir -p "$(dirname "$REPO_DIR")"
  git clone --depth 1 "$CLONE_URL" "$REPO_DIR" >&2
  write_meta
  echo "$REPO_DIR"
  exit 0
fi

# Check freshness
if [[ -f "$META" ]]; then
  age=$(( $(date +%s) - $(grep -o '[0-9]*' "$META" | head -1) ))
  if [[ "$FRESHNESS" -gt 0 && "$age" -lt "$FRESHNESS" ]]; then
    echo "Cache fresh (${age}s old)." >&2
    echo "$REPO_DIR"
    exit 0
  fi
  echo "Cache stale (${age}s old). Refreshing..." >&2
else
  echo "No metadata. Refreshing..." >&2
fi

# Fetch & reset
(
  cd "$REPO_DIR"
  git fetch --depth 1 origin >&2
  git reset --hard FETCH_HEAD >&2
  git clean -fd >&2
)
write_meta
echo "$REPO_DIR"
