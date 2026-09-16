# Reliability validation — 2026-09-16

`make ci` passed locally: **132 tests**, Python 3.14.7 on macOS. The original baseline passed 74 tests. No third-party runtime dependency was added. Linux/Python 3.12 remains the repository CI target; this task did not run hosted CI.

## What was exercised

- A single `--until-clean` invocation completes seven queued fixes, validates each with existing behavioral tests, finishes complete empty review sweeps, and resumes without additional budget use. The target path contains spaces.
- Goal reservations survive restart; bare `--continue` preserves limits; exhausted goals do not silently renew; changed committed content invalidates prior completion.
- Stop files, deadlines, concurrent locks, interrupted findings, missing evidence, incomplete inventory and ineligible queued findings prevent false completion.
- Supplied wrong finding IDs, stale attempt/content proof, failed or empty check results, malformed confidence values and unsupported mutation permissions are rejected.
- Fix ownership comes from content manifests, including untracked files. New files count against patch budgets. Commit/revert refuses pre-existing work.
- Relevant source changes allow a previously resolved finding to be independently reconsidered; unchanged repeats remain deduplicated.

## External work and research

Fourteen subscription-squad calls were used: eight Muse Spark Contributor calls through OpenCode Go and six Grok 4.6 calls through Cursor. Work included independent audits, implementation, regression tests, source comparisons, a scenario-based skill evaluation and cross-model review. The final Grok review gave bounded acceptance. Coordinator verification, integration and repairs were still required; no percentage of Codex usage savings is claimed.

See [research notes](../references/research-notes.md) for primary-source comparisons. The initial Grok research call could not use live search/fetch; later research received primary-source text fetched by the coordinator.

## Limits of the evidence

The tests use scripted agent CLIs. They prove control-plane behavior, not universal model judgment or production compatibility with every CLI version. Two empty sweeps mean no eligible findings were accepted in the reviewed scope; they do not prove that no defects exist. Ignored/generated/vendor content remains outside the default source scope. CLI permissions and content checks are not an operating-system sandbox. OpenClaw remains explicitly unsupported until its invocation contract is verified.

## Native Cursor cloud handoff (2026-09-16)

Added a `native` adapter that publishes per-stage requests and receives the parent
agent's native-subagent JSON through an atomic file handoff. The original runner
continues to enforce source/control-file ownership; stage validators, checks,
proof binding, finalization, goal budgets and empty-sweep coverage are unchanged.
`deslop-cloud.py` provides start, bounded poll, submit and stop commands. The host
must service requests and cancel its native workers if a request is cancelled.

Validation: `make ci` passed 137 tests. After adding review-only controller locking,
all six cloud tests passed again, including duplicate/nonobject responses and
response destination confinement. The tests cover a real fix/check/verify/clean
pipeline using deterministic native responses, unchanged-goal resume, stale
response rejection, duplicate launch prevention, stop cancellation, read-only
mutation rejection, and exclusion of nested CLI configuration from installs.
These are harness integration tests, not evidence of a live Cursor Cloud run.

### Live Cursor Cloud smoke test

In the Cursor task `De-slop native cloud test`, the cloud VM executed package
revision `939a1f18ab259e9875d62022b0e190ac0d415ca2` against the disposable Git fixture
`/tmp/deslop-native-cloud-smoke.CzpyUM`. Three actual native Cursor Grok 4.6 subagents
ran: fixer, independent verifier, and reviewer. The fixer corrected `identity(x)`
from `x + 1` to `x`; the recorded test passed and the verifier returned PASS at
0.96 confidence. The deterministic controller recorded `DSL-000001` as verified.
A separate terminal run confirmed `test_identity ... ok`, `Ran 1 test`, `OK`.

The smoke test's explicit 600-second cap expired during the subsequent review:
state recorded `stage_failed` / `review_failed`, elapsed 600.11474345 seconds,
one verified finding and zero completed empty sweeps. This is **not** a live
until-clean proof. All native workers completed and the parent test task was
stopped; no claim of a fully clean repository is made. Deterministic integration
tests cover complete empty-sweep convergence. GitHub CI subsequently passed all
138 tests on the hardened handoff implementation.

Live use exposed a brief reappearance of submitted requests before consumption.
The published implementation now hides submitted requests from dispatch polling,
with regression coverage, while still rejecting duplicate submissions.

Cursor's User Context copy remained stale despite enabled skill sync. It was
replaced through Cursor's editor with `templates/cursor/cloud/SKILL.md`; its new
cloud-entry content and description were visibly verified after saving. Scoped
User Context preferences now route explicit Project de-slop requests to that
entry, with a published-package fallback when Context files are unavailable.
Local and old-Mac CLI packages retain the full standalone runtime.
