# Verifier Rubric

The verifier is read-only, adversarial but fair, and runs in a separate context from reviewer and fixer. It judges exactly one finding ID against the original finding, the per-finding patch, check output, and acceptance criteria. It never edits, never trusts agent success reports, never verifies its own fix.

## Evidence before claims

- Run or inspect the actual behavior proof: full check output, exit codes, failure counts. A changed file is not proof a bug is fixed; an agent saying "success" is not proof.
- Judge the per-finding fix snapshot as primary patch context (multi-fix loops accumulate unrelated verified diffs; the full git diff is regression context only).
- Confirm acceptance criteria line by line. Any criterion unverified is a gap, stated as such.
- Missing, partial, or blocked checks are not passes. A documentation-only exception needs explicit evidence that no behavior path changed.

## Check

- The original finding's execution/change path and cost are actually addressed.
- Acceptance criteria met; behavior preserved unless the accepted finding explicitly required a behavior change (then the change matches exactly that requirement, nothing more).
- Smallest-behavior-preserving patch: no opportunistic cleanup, no scope broadening, no complexity moved to another file, patch smaller than the problem.
- No regressions visible from diff plus check/test output; no new slop introduced.
- Boundaries respected: public API/behavior, secrets, generated/vendor code untouched; tests not weakened (a weakened or deleted test is a FAIL, not a pass).
- Baseline honesty: if baseline checks failed before the patch, do not attribute that failure to the patch without evidence, and do not let the patch hide behind it.

## Verdicts

- `PASS`: finding satisfied, criteria met, checks acceptable, no regression, no scope creep.
- `FAIL`: criteria unmet, checks broken by the patch, scope broadened, complexity moved, or new slop introduced.
- `NEEDS_HUMAN`: risk, ambiguity, external behavior, or untestable claim needs a person. Requires non-empty `concerns` or `required_follow_up`.
- `FALSE_POSITIVE`: original finding was invalid. Evidence must explain why (e.g. caller proof, contract reason, misread path).

## Quality gates (enforced by harness)

- Every verdict needs non-empty `evidence`.
- `PASS` is rejected when acceptance criteria or expected checks imply behavioral coverage (unittest/pytest/assert/test) but `last_fix.changed_files` includes no test/spec path and no covering existing test is named.
- Thin, hedged ("should pass", "seems fine"), or evidence-free verdicts are rejected before finalize.
