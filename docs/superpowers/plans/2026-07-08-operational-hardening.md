# Operational Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement doctor, test-gap verify gate, P2 status clarity, fix budgets, soak docs, and resume.

**Architecture:** Deterministic Python/shell gates around existing loop; docs for soak; no new harness adapters.

**Tech Stack:** Python 3 stdlib, bash, unittest

---

### Task 1: deslop-doctor

**Files:** Create `scripts/deslop-doctor.py`; modify README, `deslop-init.sh`; test in `tests/test_harness.py`

- [ ] Failing tests for missing CLI / cursor auth / happy path
- [ ] Implement doctor
- [ ] Commit

### Task 2: Test-gap verify gate + fixer prompt

**Files:** `deslop-verify.sh`, fixer prompt in `deslop-fix.sh`, rubrics/templates, tests

- [ ] Failing test: PASS + unittest expected_checks + no test file changes → verify fails
- [ ] Implement gate + prompt wording
- [ ] Commit

### Task 3: P2-remaining status clarity

**Files:** `deslop-status.py`, examples, tests

- [ ] Failing test for priority_note
- [ ] Implement
- [ ] Commit

### Task 4: Post-fix budgets

**Files:** `deslop-fix.sh`, tests

- [ ] Failing test over budget → needs_human
- [ ] Enforce after snapshots
- [ ] Commit

### Task 5: Soak docs

**Files:** `references/soak-runs.md`, README, proof-run.md, CHANGELOG

- [ ] Add template + links
- [ ] Commit

### Task 6: deslop-resume

**Files:** Create `scripts/deslop-resume.py`; status suggested commands; tests

- [ ] Failing tests for resume transitions
- [ ] Implement
- [ ] Commit

### Task 7: Full CI + push PR update
