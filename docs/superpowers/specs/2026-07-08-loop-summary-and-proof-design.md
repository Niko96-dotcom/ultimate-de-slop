# Loop Summary, Verifier Clarity, and Proof Run

## Goal

Make Ultimate De-Slop outcomes trustworthy after a loop ends: users should see why the loop stopped, what changed, what is still queued, and why any `NEEDS_HUMAN` / `FALSE_POSITIVE` verdicts happened. Prove the control plane with a deterministic end-to-end fixture that does not call a real model.

## Non-goals

- New harness adapters or OpenClaw enablement.
- A web UI, dashboard, or analytics product.
- Changing severity thresholds, arbitration policy, or fixer scope.
- Live LLM proof runs against external repos in CI.

## Approaches considered

1. **Status-only polish** — prettier `deslop-status.py` text. Fast, but stop reasons stay ephemeral and verifier thinness stays invisible.
2. **Recommended: status + persisted stop reason + verifier quality gates + deterministic proof** — keep the control plane as source of truth; make outcomes inspectable and testable.
3. **Full session journal / scribe agent** — richer narrative, but adds agent cost and another failure mode. Deferred.

## Design

### 1. Persisted loop outcome

Extend `.deslop/state.json`:

- `stop.reason` becomes a stable machine string when the loop or finalize halts, not only `"stop file exists"`.
- Add optional `loop_outcome` object written by the loop and readable by status:

```json
{
  "at": "2026-07-08T00:00:00Z",
  "stop_reason": "no_eligible_findings",
  "iterations_completed": 2,
  "max_iterations": 5,
  "priority": "P0,P1",
  "verified_ids": ["DSL-000001"],
  "halt_finding_id": null,
  "halt_status": null
}
```

`stop_reason` values:

| Value | When |
| --- | --- |
| `stop_file` | `.deslop/stop` present |
| `no_eligible_findings` | next finding is `NONE` |
| `max_iterations_reached` | loop exits after completing max iterations |
| `finalize_halt` | finalize returned non-zero (`needs_human`, failed checks, `FAIL`, blocked) |
| `dirty_tree` | existing dirty-tree refusal (loop never starts; no outcome required) |

`deslop-loop.sh` records `loop_outcome` on every normal or halt exit path that reaches status printing. Finalize continues to update `last_run` and finding status; the loop aggregates verified IDs from findings whose status became `verified` during the run (compare pre-loop snapshot of verified IDs vs post-loop).

### 2. Human loop summary in status

`deslop-status.py` gains a `loop_summary` section in JSON and a short human block:

```text
Loop outcome
  Stop reason: no_eligible_findings
  Verified this run: DSL-000001 Request validation is duplicated...
  Queued next: NONE
  Needs human: DSL-000004 Broad auth rewrite (see concerns)
  False positives: DSL-000003 Speculative unused helper
```

Rules:

- Prefer `state.loop_outcome` when present; otherwise infer a lightweight summary from findings + `state.stop` + `state.last_run`.
- For `needs_human` / `false_positive` / `blocked`, include id, title, and the most useful verifier fields (`concerns`, `required_follow_up`, or `evidence`).
- Keep existing score / counts / suggested commands; do not replace them.

### 3. Verifier outcome quality gates

After JSON extraction in `deslop-verify.sh`, validate verdict quality before writing success:

- Every verdict requires non-empty `evidence`.
- `NEEDS_HUMAN` requires non-empty `concerns` or non-empty `required_follow_up`.
- `FALSE_POSITIVE` requires non-empty `evidence` that explains why the original finding was invalid (same evidence rule; document in rubric).
- On failure: exit non-zero with an actionable message naming the missing fields; do not finalize a thin verdict as success.

Update `references/verifier-rubric.md` and prompt wording to match.

`deslop-finalize.py` human output for `NEEDS_HUMAN` / `FALSE_POSITIVE` prints concerns / follow-up / evidence so a halted loop is readable without digging through run dirs.

### 4. Adapter honesty (keep, surface)

OpenClaw remains a guarded unsupported adapter. Status should surface the latest runner failure when `state.last_run` or the newest `runner.json` indicates `*_unsupported` / `*_not_found`, as a single diagnostic line under the loop summary. No adapter behavior change beyond surfacing.

### 5. Deterministic proof fixture

Add a control-plane proof that runs without network or real agent CLIs:

1. Temp git repo with a small intentional maintainability issue (duplicated validation helper).
2. Fake harness on `PATH` selected via `DESLOP_HARNESS` that returns canned review / fix / verify JSON based on `--kind` / role.
3. Drive `deslop-init` → review → arbitrate → fix → checks → verify → finalize (or one `deslop-loop.sh` iteration with the fake harness).
4. Assert: one finding accepted, fixed, verified; status / `loop_outcome` reports verified id and a clear stop reason; artifacts exist under `.deslop/runs/`.

Document the expected transcript shape in `references/proof-run.md` and link it from README / examples.

## Testing

- Unit/integration tests for status summary fields and stop-reason persistence.
- Verify extraction rejects thin `NEEDS_HUMAN` / empty-evidence verdicts.
- Finalize message coverage for `NEEDS_HUMAN` / `FALSE_POSITIVE`.
- Full deterministic proof test as above.
- Existing harness tests remain green; OpenClaw unsupported test unchanged.

## Risks

- Loop stop-reason bookkeeping in bash must not invent reasons on dirty-tree early exit.
- Fake harness must stay thin and kind-driven so it does not become a second agent runner.
- Status inference fallback must not claim “verified this run” without `loop_outcome` or equivalent evidence.
