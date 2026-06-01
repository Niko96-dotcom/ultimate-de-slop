#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: deslop-review.sh [--help]

Run a read-only whole-codebase ultimate-de-slop review, extract JSON, and arbitrate findings.
This invokes the harness selected by DESLOP_HARNESS, defaulting to codex, and does not edit product code.
EOF
}

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
  usage
  exit 0
fi

extract_json() {
  python3 - "$1" "$2" <<'PY'
import json
import sys
from pathlib import Path

source = Path(sys.argv[1])
target = Path(sys.argv[2])
text = source.read_text(errors="ignore")

def scan_balanced(s):
    for start, ch in enumerate(s):
        if ch != "{":
            continue
        depth = 0
        in_str = False
        escape = False
        for idx in range(start, len(s)):
            c = s[idx]
            if in_str:
                if escape:
                    escape = False
                elif c == "\\":
                    escape = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    yield s[start:idx + 1]
                    break

valid = []
try:
    parsed = json.loads(text, strict=False)
    if isinstance(parsed, dict):
        valid.append(parsed)
except json.JSONDecodeError:
    pass
for candidate in scan_balanced(text):
    try:
        parsed = json.loads(candidate, strict=False)
    except json.JSONDecodeError:
        continue
    if isinstance(parsed, dict):
        valid.append(parsed)

required = {"repo_summary", "review_wave_id", "partitions_reviewed", "findings"}
obj = next((item for item in reversed(valid) if required.issubset(item)), None)
if obj is None and valid:
    obj = valid[-1]
if obj is None:
    print(f"could not extract JSON object from {source}", file=sys.stderr)
    raise SystemExit(1)
target.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n")
PY
}

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(git -C "$PWD" rev-parse --show-toplevel 2>/dev/null)" || {
  printf 'deslop-review: error: could not resolve git root; run from inside a git repository\n' >&2
  exit 1
}

if [ ! -f "$ROOT/.deslop/config.json" ]; then
  "$SCRIPT_DIR/deslop-init.sh"
fi
"$SCRIPT_DIR/deslop-inventory.py" --write

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
run_dir="$ROOT/.deslop/runs/${timestamp}-review"
mkdir -p "$run_dir"
prompt="$run_dir/prompt.txt"
raw="$run_dir/raw-review-output.txt"
review_json="$run_dir/review.json"
last_message="$run_dir/last-message.txt"
runner_json="$run_dir/runner.json"
schema="$SCRIPT_DIR/../references/review.schema.json"

cat > "$prompt" <<'EOF'
Use $ultimate-de-slop if available.

You are performing a whole-codebase ultimate-de-slop review, not a latest-diff review. Review the repo by partitions using `.deslop/index.md` and `.deslop/inventory.json`. Spawn specialized read-only reviewer subagents where useful and wait for all results. Use these perspectives where useful:
- architecture/maintainability
- correctness/bug risk
- testability
- security/boundaries
- dependency/coupling

Do not edit files. Return at most 5 findings per reviewer. Only report P0/P1/P2. No style nits. Every finding must include concrete evidence, why it matters, proposed bounded fix, acceptance criteria, expected checks, risk, effort, confidence. Return ONLY JSON matching the review schema.
EOF

set +e
"$SCRIPT_DIR/deslop-agent-runner.py" --root "$ROOT" --prompt "$prompt" --raw-output "$raw" --last-message "$last_message" --runner-json "$runner_json" --schema "$schema" --sandbox read-only --kind review
code=$?
set -e
if [ "$code" -ne 0 ]; then
  printf 'deslop-review: error: %s review failed with exit code %s. Raw output: %s Runner: %s\n' "${DESLOP_HARNESS:-codex}" "$code" "$raw" "$runner_json" >&2
  exit "$code"
fi

if ! extract_json "$last_message" "$review_json" && ! extract_json "$raw" "$review_json"; then
  printf 'deslop-review: error: JSON extraction failed. Raw output: %s\n' "$raw" >&2
  exit 1
fi

"$SCRIPT_DIR/deslop-arbitrate.py" "$review_json" --run-dir "$run_dir"

cat <<EOF
Review complete.
Run directory: $run_dir
Review JSON: $review_json
Next suggested command:
  next_id="\$($SCRIPT_DIR/deslop-next.py)"
  [ "\$next_id" = "NONE" ] || "$SCRIPT_DIR/deslop-fix.sh" "\$next_id"
EOF
