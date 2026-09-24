#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

# The shared .agents fallback is opt-in: installing it alongside Codex exposes
# the same skill twice in Codex's discovery paths.
for harness in codex claude opencode cursor pi commandcode hermes openclaw; do
  python3 "$SCRIPT_DIR/install-skill.py" --harness "$harness" "$@"
done
