#!/usr/bin/env bash
set -euo pipefail

[[ $# -ge 1 && $# -le 2 ]] || {
  echo "Usage: validate_mermaid.sh <diagram.mmd|-> [output.svg]" >&2
  exit 1
}

input="$1"
output="${2:-}"
tmpdir="$(mktemp -d /tmp/mermaid-validate.XXXXXX)"
err="$tmpdir/mmdc.err"
trap 'rm -rf "$tmpdir"' EXIT

if [[ "$input" == "-" ]]; then
  src="$tmpdir/input.mmd"
  cat > "$src"
else
  [[ -f "$input" ]] || {
    echo "File not found: $input" >&2
    exit 1
  }
  src="$input"
fi

dest="${output:-$tmpdir/out.svg}"
[[ -n "$output" ]] && mkdir -p "$(dirname "$output")"

if npx --yes -p @mermaid-js/mermaid-cli mmdc -i "$src" -o "$dest" >/dev/null 2>"$err"; then
  [[ -n "$output" ]] && echo "$input: valid -> $dest" || echo "$input: valid"
  exit 0
fi

echo "$input: invalid" >&2
cat "$err" >&2
exit 1
