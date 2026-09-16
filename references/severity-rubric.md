# Severity Rubric

Loop fuel is findings at or above threshold in the goal scope. Default `--until-clean` scope is P0,P1,P2; bounded runs may narrow it (e.g. `--priority P0,P1`).

## P0 — correctness / security / breakage

Data loss or corruption path, bypassable security boundary, broken default build or test command, serious production breakage.

Default confidence threshold: `0.70`.

## P1 — serious structural drag, happening now

Repeated ad-hoc conditionals making a busy flow fragile; mixed ownership forcing cross-layer edits; giant file already blocking safe change; unenforced invariant already causing defects.

Default confidence threshold: `0.75`.

Cost must be concrete and present-tense: name the execution/change path and what it costs today.

## P2 — bounded cleanup with clear payoff and controlled risk

Duplicated adapter collapsible behind the existing canonical helper; medium flow simplifiable by a small extraction with tests; dead code removable with caller/contract proof.

Default confidence threshold: `0.85`. Higher bar because payoff must justify loop fuel.

## P3 — never loop fuel

Style, taste, naming, formatting, speculative cleanup, broad rewrite ideas, quota-driven churn ("split everything over N lines"). Rejected by default.

## Confidence and evidence

Confidence is the reviewer's calibrated probability the path, cost, and fix are all correct — not enthusiasm. Below-threshold findings are rejected (or `needs_human` when risk genuinely needs a person, never as a way to smuggle weak findings into the loop).
