#!/usr/bin/env bash
# Usage: wait-for.sh -t <target> -p <pattern> [-F] [-T timeout] [-i interval] [-l lines]
# Polls a tmux pane (-L llm-agent) until pattern appears. Exits 0 on match, 1 on timeout.
set -euo pipefail

target="" pattern="" grep_flag="-E" timeout=30 interval=0.5 lines=1000

while [[ $# -gt 0 ]]; do
  case "$1" in
    -t) target="$2"; shift 2 ;;
    -p) pattern="$2"; shift 2 ;;
    -F) grep_flag="-F"; shift ;;
    -T) timeout="$2"; shift 2 ;;
    -i) interval="$2"; shift 2 ;;
    -l) lines="$2"; shift 2 ;;
    *)  echo "Unknown: $1" >&2; exit 1 ;;
  esac
done

[[ -z "$target" || -z "$pattern" ]] && { echo "Usage: wait-for.sh -t target -p pattern [-F] [-T timeout] [-i interval] [-l lines]" >&2; exit 1; }

deadline=$(( $(date +%s) + timeout ))
while true; do
  text="$(tmux -L llm-agent capture-pane -p -J -t "$target" -S "-${lines}" 2>/dev/null || true)"
  printf '%s\n' "$text" | grep -q $grep_flag -- "$pattern" && exit 0
  if (( $(date +%s) >= deadline )); then
    echo "Timed out after ${timeout}s waiting for: $pattern" >&2
    printf '%s\n' "$text" >&2
    exit 1
  fi
  sleep "$interval"
done
