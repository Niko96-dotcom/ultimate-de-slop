#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: deslop-review.sh [--partition PATH] [--help]

Run a read-only ultimate-de-slop review, extract JSON, and arbitrate findings.
This invokes the harness selected by DESLOP_HARNESS or the install marker for this skill copy, defaulting to codex, and does not edit product code.
When --partition is set, review only that repo-relative partition to keep reviewer context bounded.
EOF
}

PARTITION=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --help|-h)
      usage
      exit 0
      ;;
    --partition)
      PARTITION="${2:-}"
      if [ -z "$PARTITION" ]; then
        printf 'deslop-review: error: --partition requires a path\n' >&2
        exit 1
      fi
      shift 2
      ;;
    *)
      printf 'deslop-review: error: unknown option: %s\n' "$1" >&2
      exit 1
      ;;
  esac
done

extract_json() {
  python3 - "$1" "$2" <<'PY'
import json
import re
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
def parse_candidate(candidate):
    try:
        return json.loads(candidate, strict=False)
    except json.JSONDecodeError:
        repaired = re.sub(r'\\([^"\\/bfnrtu])', r'\1', candidate)
        if repaired == candidate:
            raise
        return json.loads(repaired, strict=False)

try:
    parsed = parse_candidate(text)
    if isinstance(parsed, dict):
        valid.append(parsed)
except json.JSONDecodeError:
    pass
for candidate in scan_balanced(text):
    try:
        parsed = parse_candidate(candidate)
    except json.JSONDecodeError:
        continue
    if isinstance(parsed, dict):
        valid.append(parsed)

required = {"repo_summary", "review_wave_id", "partitions_reviewed", "findings"}
obj = next((item for item in reversed(valid) if required.issubset(item)), None)
if obj is None:
    if valid:
        seen = set()
        for item in valid:
            seen.update(item.keys())
        missing = ", ".join(sorted(required - seen))
        print(f"could not extract review JSON object from {source}; missing required keys: {missing}", file=sys.stderr)
    else:
        print(f"could not extract JSON object from {source}", file=sys.stderr)
    raise SystemExit(1)
target.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n")
PY
}

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
: "${DESLOP_HARNESS:=$(python3 "$SCRIPT_DIR/deslop_harness.py" --print)}"
export DESLOP_HARNESS
ROOT="$(git -C "$PWD" rev-parse --show-toplevel 2>/dev/null)" || {
  printf 'deslop-review: error: could not resolve git root; run from inside a git repository\n' >&2
  exit 1
}

if [ -n "$PARTITION" ]; then
  PARTITION="$(python3 - "$ROOT" "$PARTITION" <<'PY'
import re
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
raw = sys.argv[2]

if re.search(r"[\x00-\x1f\x7f]", raw):
    print("deslop-review: error: --partition contains control characters", file=sys.stderr)
    raise SystemExit(1)

if raw.startswith("/") or raw.startswith("\\"):
    print("deslop-review: error: --partition must be a repo-relative path", file=sys.stderr)
    raise SystemExit(1)

candidate = (root / raw).resolve()
try:
    candidate.relative_to(root)
except ValueError:
    print("deslop-review: error: --partition resolves outside the repository", file=sys.stderr)
    raise SystemExit(1)

if not candidate.is_dir():
    print("deslop-review: error: --partition is not an existing directory", file=sys.stderr)
    raise SystemExit(1)

print(candidate.relative_to(root).as_posix())
PY
)" || exit 1
fi

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

if [ -n "$PARTITION" ]; then
  python3 - "$prompt" "$PARTITION" <<'PY'
import json
import sys
from pathlib import Path

prompt_path = Path(sys.argv[1])
partition_label = sys.argv[2]
partition_json = json.dumps(partition_label)

prompt_path.write_text(
    f"""You are performing a bounded ultimate-de-slop review for one repo partition, not a latest-diff review. Review ONLY the partition at repo-relative path: {partition_label}

Use `.deslop/index.md` and `.deslop/inventory.json` for orientation, but limit findings to files under {partition_label}. Do not load skill files recursively; this harness already selected the reviewer role. Use these perspectives where useful:
- architecture/maintainability
- correctness/bug risk
- testability
- security/boundaries
- dependency/coupling

Treat `.deslop` as harness state. Read only `.deslop/index.md` and `.deslop/inventory.json`; Do not inspect `.deslop/runs`, `.deslop/tmp`, raw outputs, runner logs, or generated review/fix/check/verify artifacts as product code.
Do not edit files. Return at most 5 findings per reviewer. Only report severity P0/P1/P2. No style nits.

Return exactly one JSON object, no markdown fences and no prose, with this top-level shape:
{{
  "repo_summary": "short summary",
  "review_wave_id": "wave-YYYYMMDDTHHMMSSZ",
  "partitions_reviewed": [{partition_json}],
  "findings": []
}}

Each finding object must use these exact keys and value types:
id, title, severity, confidence, category, status, files, evidence, why_it_matters,
proposed_fix, acceptance_criteria, expected_checks, expected_checks_explanation,
no_expected_checks_reason, checks_explanation, risk, dependencies, estimated_effort,
reviewer, created_at, updated_at.

Use `severity`, not `priority`; use `estimated_effort`, not `effort`; use `status` as `candidate`.
Use `confidence` as a number from 0.0 to 1.0, not a label such as "high".
Use `risk` as one of: low, medium, high. Use `estimated_effort` as one of: small, medium, large.
Use arrays of strings for `files`, `acceptance_criteria`, `expected_checks`, and `dependencies`.
Each evidence item must be an object with string fields: file, lines, symbol, claim.
Evidence must be concrete enough for a fixer to act: include exact line ranges or a symbol when possible, and quote the risky code shape in claim. Do not report filename-only or "likely/appears/flagged candidate" findings; they will be rejected by arbitration.
Expected checks must be simple JSON-safe command strings. Avoid shell redirection, pipes, and nested quotes in expected_checks; if a useful check needs complex quoting, leave expected_checks empty and explain it in no_expected_checks_reason.
Return ONLY JSON matching the review schema.
"""
)
PY
else
  cat > "$prompt" <<'EOF'
You are performing a whole-codebase ultimate-de-slop review, not a latest-diff review. Review the repo by partitions using `.deslop/index.md` and `.deslop/inventory.json`. Do not load skill files recursively; this harness already selected the reviewer role. Use these perspectives where useful:
- architecture/maintainability
- correctness/bug risk
- testability
- security/boundaries
- dependency/coupling

Treat `.deslop` as harness state. Read only `.deslop/index.md` and `.deslop/inventory.json`; Do not inspect `.deslop/runs`, `.deslop/tmp`, raw outputs, runner logs, or generated review/fix/check/verify artifacts as product code.
Do not edit files. Return at most 5 findings per reviewer. Only report severity P0/P1/P2. No style nits.

Return exactly one JSON object, no markdown fences and no prose, with this top-level shape:
{
  "repo_summary": "short summary",
  "review_wave_id": "wave-YYYYMMDDTHHMMSSZ",
  "partitions_reviewed": ["partition name"],
  "findings": []
}

Each finding object must use these exact keys and value types:
id, title, severity, confidence, category, status, files, evidence, why_it_matters,
proposed_fix, acceptance_criteria, expected_checks, expected_checks_explanation,
no_expected_checks_reason, checks_explanation, risk, dependencies, estimated_effort,
reviewer, created_at, updated_at.

Use `severity`, not `priority`; use `estimated_effort`, not `effort`; use `status` as `candidate`.
Use `confidence` as a number from 0.0 to 1.0, not a label such as "high".
Use `risk` as one of: low, medium, high. Use `estimated_effort` as one of: small, medium, large.
Use arrays of strings for `files`, `acceptance_criteria`, `expected_checks`, and `dependencies`.
Each evidence item must be an object with string fields: file, lines, symbol, claim.
Evidence must be concrete enough for a fixer to act: include exact line ranges or a symbol when possible, and quote the risky code shape in claim. Do not report filename-only or "likely/appears/flagged candidate" findings; they will be rejected by arbitration.
Expected checks must be simple JSON-safe command strings. Avoid shell redirection, pipes, and nested quotes in expected_checks; if a useful check needs complex quoting, leave expected_checks empty and explain it in no_expected_checks_reason.
Return ONLY JSON matching the review schema.
EOF
fi

set +e
"$SCRIPT_DIR/deslop-agent-runner.py" --root "$ROOT" --prompt "$prompt" --raw-output "$raw" --last-message "$last_message" --runner-json "$runner_json" --schema "$schema" --sandbox read-only --kind review
code=$?
set -e
if [ "$code" -ne 0 ]; then
  printf 'deslop-review: error: %s review failed with exit code %s. Raw output: %s Runner: %s\n' "$DESLOP_HARNESS" "$code" "$raw" "$runner_json" >&2
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
