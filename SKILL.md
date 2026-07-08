---
name: ultimate-de-slop
description: "Run a bounded whole-repo code-quality improvement loop: strict structural review, finding arbitration, one-finding fixes, deterministic validation, independent verification, and explicit stop policy."
---

# Ultimate De-Slop

Use this skill when the user asks for a repo-wide code-quality loop, strict structural review, bounded cleanup, high-confidence maintainability fixes, or an iterative review -> repair -> validate -> verify cycle.

Do not use it for ordinary code review, quick bug fixes, broad rewrites, style polishing, formatting-only work, or when the user has not asked for de-slopping.

Default intent: when the user invokes "Ultimate De Slop", "de-slop", or a repo-wide cleanup/improvement loop without explicitly saying "review only", run bounded loop mode. Do not stop after review if accepted findings exist.

Review-only is an explicit mode. Use it only when the user asks for review, audit, diagnose, findings only, no edits, or read-only behavior.

## Safety Defaults

- Review, arbitration, and verification are read-only.
- Fixers handle exactly one accepted finding per run.
- No parallel writers. Run only one fixer at a time.
- No nits. P3 and subjective style findings are rejected by default.
- Deterministic checks run before verifier judgment when practical.
- Auto-revert and commits are opt-in.
- The loop is bounded by max iterations, max attempts, stop files, dirty-tree checks, and verifier outcomes.

## Modes

Review-only:

```sh
scripts/deslop-init.sh
scripts/deslop-review.sh
```

Fix one accepted finding:

```sh
scripts/deslop-fix.sh DSL-000001
scripts/deslop-run-checks.sh DSL-000001
scripts/deslop-verify.sh DSL-000001
scripts/deslop-finalize.py DSL-000001
```

Bounded loop:

```sh
scripts/deslop-loop.sh --max-iterations 5 --priority P0,P1
```

Continue while work remains:

```sh
scripts/deslop-continue.sh
```

Default de-slop request:

```sh
scripts/deslop-loop.sh --max-iterations 5 --priority P0,P1
```

Before starting a new review, check `scripts/deslop-next.py --priority P0,P1,P2`. If it returns a finding ID, continue the loop and fix queued findings first. Do not run another review first unless the user explicitly asked for review-only or a fresh review.

## Severity Summary

- P0: correctness, security, data loss, build break, test break, serious production breakage.
- P1: serious structural issue already hurting maintainability or correctness velocity.
- P2: bounded cleanup with clear payoff and low or regression-controlled risk.
- P3: ignored by default; not loop fuel.

Default confidence thresholds are P0 >= 0.70, P1 >= 0.75, and P2 >= 0.85.

## Operating Rules

Before any review or fix, run `scripts/deslop-doctor.py` when harness/auth readiness is uncertain, then run init so `.deslop/config.json`, `.deslop/state.json`, `.deslop/findings.jsonl`, `.deslop/inventory.json`, and `.deslop/index.md` exist. Use `.deslop/index.md` and `.deslop/inventory.json` to partition the repo.

Loop defaults persist in `.deslop/config.json` (`loop_priority`, `max_iterations`, `review_every`, `empty_review_waves_required`). CLI flags override and refresh those values on each loop run.

Partition-scoped review keeps reviewer context bounded:

```sh
scripts/deslop-review.sh --partition src
```

The loop walks `inventory.json` risk partitions automatically when the accepted queue is empty.

Set `DESLOP_HARNESS=<harness>` to override the child-agent harness. When unset, the harness is read from `.ultimate-de-slop-install.json` in the installed skill directory (for example `cursor` after `install-cursor.sh`); otherwise the default is `codex`.
Supported harness values are `codex`, `claude`, `opencode`, `cursor`, `pi`, `commandcode`, `hermes`, and `openclaw`.
Set `DESLOP_MODEL=<model>` to select a model for any harness. `DESLOP_CODEX_MODEL=<model>` remains supported for Codex compatibility.

Child agent sessions must be invoked through `scripts/deslop-agent-runner.py` by the harness scripts. `scripts/deslop-codex-runner.py` remains as a Codex compatibility shim. The neutral runner captures raw output, `last-message.txt`, and `runner.json`, applies wall/idle timeouts, records missing/unsupported CLIs clearly, and keeps JSON extraction and state transitions in the deterministic shell/Python harness.

Adapter matrix:

- `codex`: `codex exec` with `--output-schema`, `--output-last-message`, `--sandbox`, and optional `--add-dir`.
- `claude`: `claude -p` with JSON/schema flags where supported and `--permission-mode plan` for read-only roles.
- `opencode`: `opencode run` with `--agent`, `--format json`, `--dir`, `--file <prompt>`, and optional model.
- `cursor`: `cursor-agent --print` with `--workspace`, `--output-format json`, prompt-file instructions, and read-only plan mode.
- `pi`: `pi --print` with the installed skill path, `@<prompt>`, JSON mode, and role-specific tool lists.
- `commandcode`: `commandcode --print`, using `--plan` for read-only roles and explicit permission mode for fixers.
- `hermes`: `hermes --skills ultimate-de-slop --toolsets ... -z <prompt-file instruction>`; `--yolo` is only added when `DESLOP_HERMES_YOLO=1` and the sandbox is `danger-full-access`.
- `openclaw`: conservative tested failure until the exact noninteractive schema-output CLI contract is confirmed.

Accept only findings with concrete evidence, bounded proposed fixes, testable acceptance criteria, expected checks or an explanation for no checks, and enough confidence for the severity. Reject vague cleanup requests, speculative rewrites, thin taste preferences, and style nits.

The fixer must repair exactly one finding. It must not opportunistically clean nearby code. The verifier must inspect the original finding, diff, checks, and acceptance criteria before deciding PASS, FAIL, NEEDS_HUMAN, or FALSE_POSITIVE.

For multi-iteration runs without `--commit`, the verifier must use the per-finding fix snapshots written by `deslop-fix.sh` as its primary patch context. The full current git diff is regression context only, because it may contain earlier verified but uncommitted findings.

Stop when no accepted P0/P1 findings remain after a review wave, two consecutive review waves find no new accepted high-confidence P0/P1 findings, remaining P2s are low-value or below threshold, max iterations or attempts are reached, `.deslop/stop` exists, the tree is unexpectedly dirty, verification deadlocks, or human review is required.

## Parent-Agent Continue Rules

When you are the parent agent (Cursor, Claude Code, OpenCode, etc.) orchestrating the harness:

1. Run `scripts/deslop-loop.sh` or `scripts/deslop-continue.sh`; do not stop after a single child-agent call.
2. After every loop/continue command, run `scripts/deslop-status.py`.
3. Do not load `.deslop/runs/` or raw agent logs into chat unless debugging.
4. If `next` is not `NONE`, or the loop has not yet hit two consecutive empty review waves at the chosen priority, run `scripts/deslop-continue.sh` again in the same task.
5. Stop only when `loop_outcome.stop_reason` is `no_eligible_findings`, `max_iterations_reached`, `finalize_halt`, or `.deslop/stop` exists.

## Multi-Session Loops and Context

The loop is designed to run across many sessions without carrying chat history forward.

- State lives on disk under `.deslop/` (`findings.jsonl`, `state.json`, per-run artifacts). Each child-agent call is a fresh CLI invocation with a bounded prompt.
- Review uses `.deslop/index.md` and `.deslop/inventory.json` partitions instead of reloading the whole repo into one conversation.
- Fix and verify one finding at a time. The verifier judges the per-finding fix snapshot first; the full git diff is regression context only.
- Do not read `.deslop/runs/` or raw agent logs into the parent chat unless debugging. Use `scripts/deslop-status.py` and `scripts/deslop-next.py` to decide what happens next.

Typical multi-session flow:

```sh
scripts/deslop-loop.sh --max-iterations 5 --priority P0,P1
# later, in a new session:
scripts/deslop-status.py
scripts/deslop-continue.sh
```

If a run stops after one fix, check `scripts/deslop-status.py` for `loop_outcome.stop_reason`:

- `no_eligible_findings`: two consecutive review waves found no new accepted findings at the chosen priority. Re-run with `--priority P0,P1,P2` if you want medium-tier cleanup.
- `finalize_halt`: verifier returned FAIL/NEEDS_HUMAN or checks failed. Resume with `scripts/deslop-resume.py` after human review if appropriate.
- `max_iterations_reached`: raise `--max-iterations`.

The loop auto-allows a dirty tree when verified-but-uncommitted fixes are already present. Otherwise pass `--allow-dirty` intentionally.

Use `--review-every N` to drain several queued findings before spending another review wave. Example: `--review-every 3` fixes up to three accepted findings before the next review wave.

## Default Stop Priority

Default loop fuel is **P0 and P1**, not P2 alone.

- P0: correctness, security, build/test breaks.
- P1: serious structural issues already hurting maintainability.
- P2: bounded medium-risk cleanup with a higher confidence bar (default >= 0.85). Include it only when you explicitly want that tier: `--priority P0,P1,P2`.
- P3: never loop fuel.

So the loop should usually stop once P0/P1 are clear, even if P2 findings remain. That is intentional. To continue into medium-tier cleanup, re-run with `--priority P2` or `--priority P0,P1,P2`.

Resume halted findings with `scripts/deslop-resume.py FINDING_ID --as accepted|rejected|false_positive|verified`. After a loop exits, read `scripts/deslop-status.py` / `.deslop/state.json` `loop_outcome` for stop reason, verified finding IDs, queued next work, and any `needs_human` / `false_positive` details. Verifier verdicts must include non-empty evidence; `NEEDS_HUMAN` also requires concerns or required follow-up.

## References

Read these only as needed:

- `references/architecture.md` for harness design and agent roles.
- `references/finding-schema.md` for finding lifecycle and dedupe keys.
- `references/severity-rubric.md` for severity thresholds.
- `references/reviewer-rubric.md` for strict structural review guidance.
- `references/verifier-rubric.md` for independent verification guidance.
- `references/stop-policy.md` for loop termination rules.
- `references/prompt-templates.md` for generated prompt wording.
- `references/examples.md` for example artifacts.
- `references/proof-run.md` for the deterministic control-plane proof.
- `references/soak-runs.md` for live multi-harness soak recording.
