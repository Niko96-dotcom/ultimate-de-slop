# Loop Summary and Proof Run Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist clear loop stop outcomes, surface NEEDS_HUMAN/FALSE_POSITIVE reasons in status/finalize, enforce verifier evidence quality, and add a deterministic end-to-end proof fixture.

**Architecture:** Keep lifecycle ownership in the deterministic harness. `deslop-loop.sh` writes `state.loop_outcome`; `deslop-status.py` renders a human/JSON loop summary; `deslop-verify.sh` rejects thin verdicts; tests drive a fake Codex CLI through one full loop without network.

**Tech Stack:** Bash + Python 3 stdlib, unittest, git fixtures under `tests/`.

---

### Task 1: Verifier quality gates

**Files:**
- Modify: `scripts/deslop-verify.sh`
- Modify: `references/verifier-rubric.md`
- Modify: `references/prompt-templates.md`
- Modify: `tests/test_harness.py`

- [ ] **Step 1: Write failing tests for thin verdicts**

Add tests that fake Codex returns:
1. `NEEDS_HUMAN` with empty `concerns` and empty `required_follow_up` → verify exits non-zero
2. `PASS` with empty `evidence` → verify exits non-zero
3. `FALSE_POSITIVE` with evidence explaining invalid finding → verify succeeds

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_harness.HarnessTests.test_verify_rejects_needs_human_without_concerns_or_follow_up tests.test_harness.HarnessTests.test_verify_rejects_empty_evidence -v`

Expected: FAIL (tests missing or gates missing)

- [ ] **Step 3: Implement quality gate after extract_json**

In `deslop-verify.sh`, after writing `verify.json`, validate:
- `evidence` non-empty for all verdicts
- `NEEDS_HUMAN` requires non-empty `concerns` or `required_follow_up`
- Print actionable stderr on failure

Update rubric + prompt templates to require those fields.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_harness.HarnessTests.test_verify_rejects_needs_human_without_concerns_or_follow_up tests.test_harness.HarnessTests.test_verify_rejects_empty_evidence tests.test_harness.HarnessTests.test_verify_accepts_false_positive_with_evidence tests.test_harness.HarnessTests.test_verify_prompt_uses_per_finding_attempt_snapshot -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/deslop-verify.sh references/verifier-rubric.md references/prompt-templates.md tests/test_harness.py
git commit -m "feat: reject thin verifier verdicts"
```

### Task 2: Finalize human output for halt verdicts

**Files:**
- Modify: `scripts/deslop-finalize.py`
- Modify: `tests/test_harness.py`

- [ ] **Step 1: Write failing finalize output tests**

For `NEEDS_HUMAN` and `FALSE_POSITIVE`, assert stdout includes concerns/follow-up/evidence lines.

- [ ] **Step 2: Implement finalize printing**

After status/verdict lines, print verifier `concerns`, `required_follow_up`, and `evidence` when present.

- [ ] **Step 3: Run tests and commit**

```bash
git commit -m "feat: print verifier reasons on finalize halt"
```

### Task 3: Persist loop_outcome and enrich status

**Files:**
- Create: `scripts/deslop-record-outcome.py`
- Modify: `scripts/deslop-loop.sh`
- Modify: `scripts/deslop-status.py`
- Modify: `references/examples.md`
- Modify: `references/state.schema.json` (document optional `loop_outcome`)
- Modify: `tests/test_harness.py`

- [ ] **Step 1: Write failing status/loop tests**

Cover:
- status JSON includes `loop_summary` with stop reason, verified ids, needs_human, false_positives
- recording outcome writes `state.loop_outcome`
- loop exit with no eligible findings records `no_eligible_findings`

- [ ] **Step 2: Implement `deslop-record-outcome.py`**

CLI:
```
deslop-record-outcome.py --stop-reason CODE --max-iterations N --priority LIST --iterations-completed N [--halt-finding-id ID] [--halt-status STATUS] [--baseline-verified-ids ID,ID]
```

Writes `loop_outcome` and updates `stop.reason` when appropriate.

- [ ] **Step 3: Wire loop.sh exit paths**

Snapshot verified IDs at start. On stop-file / no-eligible / max-iterations / finalize-halt, call record-outcome then status.

- [ ] **Step 4: Enrich `deslop-status.py`**

Build `loop_summary` from `state.loop_outcome` + findings; print human block; surface latest unsupported/not_found runner diagnostic if present.

- [ ] **Step 5: Run tests and commit**

```bash
git commit -m "feat: persist loop outcomes in status"
```

### Task 4: Deterministic proof fixture

**Files:**
- Create: `references/proof-run.md`
- Modify: `tests/test_harness.py` (or `tests/test_proof_run.py`)
- Modify: `README.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Write failing end-to-end proof test**

Temp repo with duplicated validation in `app.py`. Fake `codex` on PATH:
- review → one P1 finding
- fix → edit file to share helper + return fix JSON
- verify → PASS with evidence

Run `deslop-loop.sh --max-iterations 2 --priority P0,P1 --allow-dirty` (or staged clean tree). Assert verified finding, `loop_outcome.stop_reason`, status summary, and run artifacts.

- [ ] **Step 2: Implement fake harness helper in test and make green**

- [ ] **Step 3: Document proof-run.md and link from README/examples; bump changelog**

- [ ] **Step 4: Run full suite**

Run: `make ci`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git commit -m "test: add deterministic de-slop proof run"
```

### Task 5: Ship

- [ ] Push branch, open/update PR, summarize.
