# Stop Policy

The runtime records why it stopped. Agents must report that outcome rather than infer completion from queue length.

## Goal model

- The active goal (scope priorities, fix-attempt cap, review-call cap, seconds cap, usage so far) persists in `.deslop/state.json`. No host goal API is required.
- `--until-clean` defaults: scope `P0,P1,P2`; 100 total fix attempts; 200 review calls; 28800 seconds.
- `--max-iterations` / `--max-review-calls` / `--max-seconds` (and `--priority`) configure the goal. `--continue` retains the stored limits and usage. `--new-goal` starts a deliberately renewed goal only when the user explicitly asks for one.
- Do not call `continue` repeatedly to bypass an exhausted cap. When a cap is hit, stop and report `budget_exhausted` (or the specific cap) plus the resume options that require a deliberate user decision.

## What "clean" means

The `until_clean` outcome requires ALL of the following at the goal scope. Explicit bounded mode uses its configured empty-wave threshold and reports `no_eligible_findings`; it does not claim an until-clean goal was completed.

1. Two complete consecutive empty sweeps at the goal priority: two full coverage passes over every partition that each produce zero new accepted findings.
2. An empty accepted queue alone is NOT clean. Sweeps, not queue length, prove cleanliness.
3. No finding left in `fixing`, `fixed_unverified`, `blocked`, or `needs_human`. Unresolved work prevents completion even when the queue looks empty.
4. Full coverage is invalidated by any tree change, priority/scope change, or partition/inventory change since the sweeps. After invalidation, the two-sweep count restarts.
5. The inventory must be present, structurally valid, and untruncated; a missing or malformed inventory cannot prove complete coverage.
6. OpenCode partition reviews must show a completed read of a source file in scope. A review with no such access fails the stage instead of counting as empty coverage.

## Hard stops (stop immediately, report incomplete)

- Any budget cap reached (fix attempts, review calls, seconds, iterations).
- `.deslop/stop` exists (`stop_file`).
- External failure: agent CLI missing/failing, checks infrastructure blocked, timeout, or other environment error.
- Unexpected dirty tree without an explicit dirty allowance.
- `needs_human` / `blocked` items requiring a person; broad risky surgery the loop must not attempt.

## Reporting

- After every loop/continue command, read `scripts/deslop-status.py` / `.deslop/state.json` `loop_outcome`: stop reason, verified IDs this run, queued next, `needs_human` / `false_positive` reasons.
- State the exact stop reason (`until_clean`, `no_eligible_findings`, `max_iterations_reached`, `max_review_calls_reached`, `max_seconds_reached`, `stop_file`, `stage_failed`, `needs_recovery`, `finalize_halt`). Never report `clean` when the two-sweep proof or unresolved-work rule is unsatisfied. Never claim passes for missing or blocked checks.
- Suggest the next command (continue, resume after human review, or deliberate `--new-goal`), without auto-running past the cap.

## Resume decisions

A source change after completion invalidates prior coverage. `deslop-continue.sh` reopens the same goal with its remaining budget; it does not grant more attempts or time. Exhaustion requires a deliberately renewed `deslop-loop.sh --until-clean --new-goal` with authorized limits.

After `needs_recovery`, inspect the specific finding and snapshots. Preserve partial edits until their correctness is decided. Record a safe retry using `deslop-resume.py ID --as accepted --reason "..."`; do not manufacture `verified` through manual status changes. Missing authentication, permission, or CLI capability requires resolving that actual failure, never changing providers or permissions silently.
