# Architecture

`ultimate-de-slop` is a deterministic outer harness around narrow, fresh-context child-agent calls.

## Deterministic layer owns

Repo-root discovery, inventory, state files (`.deslop/config.json`, `.deslop/state.json`, `.deslop/findings.jsonl`, `.deslop/inventory.json`, `.deslop/index.md`), finding IDs, scoring, check execution, child-agent execution, JSON extraction, snapshots, and stop conditions. The durable `--until-clean` goal (scope, fix-attempt / review-call / seconds caps, usage) lives in `.deslop/state.json`; no host goal API is required. `--continue` retains limits and usage; `--new-goal` renews them only on explicit user request.

## Agent layer is intentionally narrow

- Reviewer: read-only whole-codebase structural review, by inventory partitions.
- Arbitration: `deslop-arbitrate.py` deterministically deduplicates, thresholds, rejects, and prioritizes findings. Optional arbiter role templates are guidance, not a required model call.
- Fixer: workspace-write, exactly one accepted finding ID, smallest behavior-preserving patch.
- Verifier: read-only independent judgment of one patch against its finding and checks.
- State and summaries: deterministic scripts; the optional scribe role is not required.

Each child call is a fresh invocation with a bounded prompt; memory between calls is disk state (git history, `.deslop/` artifacts, per-finding snapshots) plus the inventory map — never chat history. This is the researched fresh-context loop pattern (see `research-notes.md`): it lets even weak agents make progress because each step is small, re-derivable, and validated before the next begins. Reviewer, fixer, and verifier contexts are always separate; a role never judges its own output.

## Loop flow

1. Initialize `.deslop`. 2. Inventory the repo. 3. Serve any queued accepted finding before opening a new review wave. 4. Review by partitions (read-only). 5. Arbitrate and persist. 6. Fix exactly one finding. 7. Run deterministic checks. 8. Verify independently (read-only). 9. Finalize state, optionally commit. 10. Repeat only until the stop policy proves `clean` (two complete consecutive empty sweeps at goal scope) or a hard stop fires.

## Execution and safety

- Child calls go through `scripts/deslop-agent-runner.py` (plus the Codex compatibility shim). The runner owns capture, timeouts, and missing-CLI reporting; adapters stay thin (command construction only) and never own lifecycle, extraction, or state.
- Fix attempts snapshot git status/diff before and after; the verifier judges the per-finding snapshot first, full diff second. Fixer failure/timeout with a dirty tree marks `needs_human`; stale dirt is never a successful fix.
- Budgets and circuit behavior: fix-attempt, review-call, and seconds caps plus the two-sweep clean proof act as the loop's circuit breaker (see `research-notes.md`). They halt runaway, stuck, or externally-failing loops; recovery is deliberate (`--continue` within caps, human review, or explicit `--new-goal`), never automatic cap evasion.
- Optimizer: high-confidence structural improvement. Never a style-churn machine or rewrite launcher. No guarantee covers arbitrarily weak agents or all bugs.
