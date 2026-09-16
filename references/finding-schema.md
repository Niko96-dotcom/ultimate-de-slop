# Finding Schema

A meaningful finding must include severity, confidence, concrete evidence, why the issue matters, a bounded proposed fix, acceptance criteria, expected checks or an explanation for no checks, risk, effort, and lifecycle status. If `expected_checks` is empty, include `expected_checks_explanation`, `no_expected_checks_reason`, or `checks_explanation`.

Required shape:

```json
{
  "id": "DSL-000001",
  "title": "Example structural issue",
  "severity": "P1",
  "confidence": 0.86,
  "category": "boundary-abstraction",
  "status": "candidate",
  "files": ["src/example.ts"],
  "evidence": [
    {
      "file": "src/example.ts",
      "lines": "120-260",
      "symbol": "optional symbol name",
      "claim": "concrete evidence"
    }
  ],
  "why_it_matters": "Why this hurts correctness, velocity, or maintainability.",
  "proposed_fix": "Bounded repair.",
  "acceptance_criteria": ["Observable condition"],
  "expected_checks": ["npm test -- example"],
  "risk": "low",
  "dependencies": [],
  "estimated_effort": "small",
  "reviewer": "deslop-reviewer",
  "created_at": "ISO-8601",
  "updated_at": "ISO-8601"
}
```

Lifecycle states:

- `candidate`
- `accepted`
- `rejected`
- `fixing`
- `fixed_unverified`
- `verified`
- `blocked`
- `false_positive`
- `needs_human`

Dedupe key:

```text
category + normalized title + sorted files + normalized evidence claims
```

The arbiter may merge duplicates into an existing active finding. Verified, blocked, false-positive, and human-needed findings should not be silently reopened.

## Evidence lifetime

The model-output schemas describe the payload before the harness enriches it. Generated fix/check/verify artifacts additionally record attempt identity and worktree content for stale-proof rejection. `changed_files` in persisted fix evidence comes from actual content changes; the model's list is retained separately for comparison.

Terminal findings are deduplicated while their relevant source remains unchanged. A new report after that source changes is independently arbitrated as a new finding, preserving the prior resolution as history. Stronger evidence can also overcome a confidence-threshold rejection; a rejected finding is not a permanent suppression rule.
