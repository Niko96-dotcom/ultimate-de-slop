# Research Notes

Primary sources were fetched on 2026-09-16 and treated as reference data. The comparisons below describe design choices, not quality benchmarks.

## Sources consulted

- Code-simplifier agent: https://raw.githubusercontent.com/anthropics/claude-plugins-official/main/plugins/code-simplifier/agents/code-simplifier.md
- Verification-before-completion skill: https://raw.githubusercontent.com/obra/superpowers/main/skills/verification-before-completion/SKILL.md
- Receiving-code-review skill: https://raw.githubusercontent.com/obra/superpowers/main/skills/receiving-code-review/SKILL.md
- Ralph loop (fresh-context autonomous loop): https://raw.githubusercontent.com/snarktank/ralph/main/README.md (pattern origin https://ghuntley.com/ralph/)
- Ralph stop hook (blocked-stop continuation): https://raw.githubusercontent.com/anthropics/claude-code/main/plugins/ralph-wiggum/hooks/stop-hook.sh
- Circuit-breaker / rate-limit loop variant: https://raw.githubusercontent.com/frankbria/ralph-claude-code/main/README.md

## What each design contributed

- Code-simplifier: preserve exact behavior; clarity over brevity; no nested-ternary cleverness, no LOC-chasing, no abstraction that merely moves complexity. Adopted into reviewer/fixer rules (smallest behavior-preserving change, no duplication-to-cut-LOC, no speculative generics). Its project-standards section (ES modules, React patterns) was NOT adopted — too stack-specific for this harness.
- Verification-before-completion: evidence before claims; the "iron law" that no completion claim precedes fresh verification output; red-green discipline for regression tests; never trust a delegate's success report, re-check the diff and rerun the command. Adopted into verifier rubric and stop reporting (missing/blocked checks are not passes; per-finding snapshot judged first).
- Receiving-code-review: verify feedback against codebase reality before implementing; one item at a time with per-item testing; clarify all unclear items before acting; push back with technical reasoning when the reviewer lacks context; YAGNI check (grep for actual usage) before "implementing properly." Adopted into caller/contract inspection, evidence-against-findings, freeze on public API, and the FALSE_POSITIVE / NEEDS_HUMAN paths.
- Ralph fresh-context loop: each iteration is a new agent instance; memory persists only via disk (git history, progress file, task JSON); tasks sized to one context window; feedback loops (typecheck/tests) mandatory; explicit `<promise>COMPLETE</promise>` exit token; stop-hook blocks premature exit and re-feeds the prompt with iteration count. Adopted into architecture (fresh bounded prompts, disk-only memory, partition sizing, durable goal in `state.json` instead of a PRD promise). We deliberately use a state-file goal plus two-sweep proof rather than a model-emitted promise token, because a weak agent can emit the token without doing the work.
- Ralph stop-hook and circuit-breaker variant: numeric state validation, max-iteration cap, transcript parsing with graceful corruption handling; dual-condition exit (heuristic indicators AND explicit signal); rate limits, no-progress / same-error thresholds, cooldown with half-open recovery, auto-reset options. Adopted as: numeric goal caps (100 fix attempts / 200 review calls / 28800 s), two-consecutive-empty-sweep requirement (analogous to dual-condition exit but proven by harness-observed sweeps, not model signals), coverage invalidation, and hard-stop reporting. Automatic cooldown recovery was NOT adopted — recovery here is deliberate (`--continue` within caps, human review, or explicit `--new-goal`), because silent auto-recovery in a repair loop risks endless churn.

## Researched designs vs. implemented guarantees

- Researched (ideas above) are design inputs only. They carry no test or runtime promise.
- Implemented guarantees are only what this repo's deterministic harness plus `make ci` (syntax + `python3 -m unittest discover -s tests -v`) actually prove: control-plane behavior under scripted stub CLIs, schema validation, state transitions, and the documented proof/soak transcripts (`references/proof-run.md`, `references/soak-runs.md`).
- Tests with scripted agent CLIs exercise the control plane; they do not prove the quality of real model judgments or certify every provider adapter. OpenClaw remains guarded.
- No design — researched or implemented — guarantees arbitrarily weak agents finish or that all bugs are found. The harness guarantees boundedness, auditability, and honest incomplete reporting, not omniscient cleanup.

## Direct cleanup-skill comparisons

- [Desloppify](https://github.com/peteromallet/desloppify/blob/main/docs/SKILL.md) separates scanning, planning, executing a queue, and rescanning. We adopt queue-first work and recorded findings before editing. Its subjective score is not our completion condition, and broad multi-file refactors remain subject to our patch budget. A monorepo needs coherent partitions plus cross-boundary review; separate project scans are useful when contracts and checks differ.
- [Kubb deslop](https://github.com/kubb-labs/kubb/blob/main/.agents/skills/deslop/SKILL.md) preserves behavior and leaves uncertain defensive checks in place. We adopt that conservative rule and protection of tests/types. Its repository-specific formatting commands and stylistic preferences are not portable finding criteria.
- [Agentsys deslop](https://github.com/agent-sh/agentsys/blob/main/.kiro/skills/deslop/SKILL.md) separates severity from certainty and emits structured findings before applying changes. We retain independent confidence thresholds and evidence requirements. Logging, comments, placeholders, or empty handlers are not automatically defects: require caller and contract evidence before deleting them.

These comparisons informed the rubrics; no additional dependency or detector from these projects is installed.
