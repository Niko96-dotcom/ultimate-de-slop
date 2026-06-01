# Verifier Rubric

The verifier is read-only and adversarial but fair.

Check:

- The original finding is satisfied.
- Acceptance criteria are met.
- Deterministic checks passed, or failures are clearly explained and not caused by the patch.
- No behavior regression is visible from the diff and tests.
- Complexity did not merely move to another file.
- The patch is smaller than the original problem.
- No unrelated cleanup was mixed in.

Verdicts:

- `PASS`: fix satisfies the finding and checks are acceptable.
- `FAIL`: fix does not satisfy the finding, breaks checks, broadens scope, or introduces new slop.
- `NEEDS_HUMAN`: risk, ambiguity, or external behavior needs human judgment.
- `FALSE_POSITIVE`: original finding was invalid.
