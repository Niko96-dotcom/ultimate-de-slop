# Operational Hardening: Doctor, Budgets, Test Gaps, Resume

## Goal

Ship the six follow-ups from the live Composer proof: harness doctor, verifier test-gap pressure, P2-remaining status clarity, post-fix change budgets, multi-harness soak docs, and `needs_human` resume.

## Non-goals

- Enabling OpenClaw or adding new harness adapters
- Changing severity thresholds or making P2 default loop fuel
- Building a web UI for soak results

## Design

### 1. `deslop-doctor.py`

Read-only diagnostics for first-run failures:

- Resolve `DESLOP_HARNESS` (default `codex`) and `DESLOP_MODEL` / harness-specific model env
- Check CLI on `PATH` for the selected harness
- Check auth env when relevant (`CURSOR_API_KEY` / `CURSOR_AUTH_TOKEN` for cursor; note login requirement)
- Flag OpenClaw as intentionally unsupported
- Check `.deslop` init files when inside a git repo
- Print human + `--json` report; exit `0` if ready, `1` if actionable failures

Wire into README quick start and `deslop-init` “next commands”.

### 2. Verifier / fixer test-gap pressure

- Fixer prompt: when acceptance criteria or expected checks imply behavioral coverage, add or update a focused test in the same fix.
- After verify JSON extract: if verdict is `PASS` and the finding’s `expected_checks` include unittest/pytest **or** any acceptance criterion matches `(?i)test|assert|unittest|pytest|spec`, and `last_fix.changed_files` touches no test path (`test`, `spec`, `_test.`, `/tests/`), reject the PASS with an actionable error (ask for tests or `NEEDS_HUMAN`).
- Rubric + prompt templates updated to match.

### 3. P2-remaining status clarity

When `loop_outcome.priority` excludes P2 (or next eligible at full priority is P2 while outcome priority was P0,P1):

- `loop_summary.priority_note`: e.g. `P0/P1 clear; 2 P2 remain (not loop fuel at this priority)`
- Human status prints that note under Loop outcome
- Suggested commands mention optional `--priority P0,P1,P2` only when P2s remain and P0/P1 are clear

### 4. Post-fix change budgets

In `deslop-fix.sh` after attempt snapshots, enforce config:

- `max_changed_files_per_fix` (default 8)
- `max_changed_lines_per_fix` (default 400)

Count from the attempt delta (files touched / added+removed lines). Over budget → do not mark `fixed_unverified`; mark `needs_human` with `block_reason` naming the budget breach. Soft budgets become hard gates.

### 5. Multi-harness soak documentation

Add `references/soak-runs.md` with:

- How to record a live soak (harness, model, fixture/repo, commands, before/after score, verified IDs, stop reason)
- Template table rows for Codex / Claude / Cursor+Composer
- Link from README and proof-run.md
- No fabricated live results; template only plus pointer to local proof artifacts path convention

### 6. `deslop-resume.py`

Resolve `needs_human` (and optionally `blocked`) findings:

```
deslop-resume.py FINDING_ID --as accepted|rejected|false_positive|verified [--reason TEXT]
```

- Updates finding status + `updated_at` + `resume` metadata
- Writes state `last_run`
- Prints next suggested command
- Refuses illegal transitions from `verified` / `accepted` without `--force` (not needed for v1: only allow from `needs_human` and `blocked`)

## Testing

- Doctor: missing CLI → exit 1; cursor without API key → failure entry; fake PATH success path
- Verify: PASS without test file changes when expected_checks include unittest → rejected
- Status: priority note when P2s remain after P0,P1 outcome
- Fix: over file/line budget → needs_human
- Resume: needs_human → accepted; illegal source status fails
- Docs exist and are linked

## Risks

- Test-gap gate may force NEEDS_HUMAN on small pure-refactor fixes that list unittest only as regression check; acceptable—prefer human or a one-line test over silent PASS.
- Budget counts must use attempt delta, not full dirty tree, so no-commit loops stay fair.
