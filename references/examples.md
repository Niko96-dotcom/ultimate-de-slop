# Examples

## Finding (accepted P1)

```json
{
  "id": "DSL-000001",
  "title": "Request validation is duplicated across three entrypoints",
  "severity": "P1",
  "confidence": 0.88,
  "category": "boundary-abstraction",
  "status": "accepted",
  "files": ["src/api/create.ts", "src/api/update.ts", "src/api/import.ts"],
  "evidence": [
    {
      "file": "src/api/create.ts",
      "lines": "40-92",
      "symbol": "createHandler",
      "claim": "Validation rules are repeated with divergent defaults; update.ts defaults locale to null while create.ts defaults to 'en'."
    }
  ],
  "why_it_matters": "Divergent validation can produce inconsistent writes on the create/update import path.",
  "proposed_fix": "Move shared validation into the existing request schema layer and update the three callers; add no new layer.",
  "acceptance_criteria": ["All three entrypoints use one validation path.", "Existing validation tests pass."],
  "expected_checks": ["npm test -- validation", "npm run typecheck"],
  "risk": "medium",
  "dependencies": [],
  "estimated_effort": "medium",
  "reviewer": "deslop-reviewer",
  "created_at": "2026-05-31T00:00:00Z",
  "updated_at": "2026-05-31T00:00:00Z"
}
```

## Rejected finding (speculative, no path/cost)

A proposal to "re-architect the API layer to event-driven handlers for future scale" with no failing path, no present-tense cost, and no bounded fix is rejected: speculative architecture, no affected execution path, vague acceptance criteria. The arbiter rejects rather than feeding the loop.

## Arbiter Result

```json
{
  "accepted": ["DSL-000001"],
  "rejected": [],
  "merged": [],
  "next": "DSL-000001",
  "stop_recommendation": null
}
```

## Verifier Result (PASS)

```json
{
  "finding_id": "DSL-000001",
  "verdict": "PASS",
  "confidence": 0.91,
  "evidence": ["The three handlers now call the same validator; validation suite 34/34 pass; typecheck exit 0."],
  "concerns": [],
  "required_follow_up": []
}
```

## Status Output (`--until-clean`, incomplete on human-needed work)

```text
Ultimate De-Slop Status
Score: 87
Findings by status: {'accepted': 1, 'verified': 2, 'needs_human': 1}
Findings by severity: {'P0': 1, 'P1': 2, 'P2': 1}
Next: DSL-000005
Stop file: absent
Loop outcome
  Stop reason: needs_human
  Goal: scope P0,P1,P2; fix attempts 41/100; review calls 12/200; elapsed 3120s/28800s
  Clean proof: 0/2 consecutive empty sweeps at P0,P1,P2 (invalidated by tree change)
  Verified this run:
    - DSL-000001 Share request validation
    - DSL-000002 Null-locale default
  Queued next: DSL-000005
  Needs human:
    - DSL-000004 Auth rewrite needs review
      Ambiguous logout semantics
Suggested commands:
  scripts/deslop-continue.sh
```

See also [proof-run.md](proof-run.md) for the deterministic control-plane proof transcript shape.
