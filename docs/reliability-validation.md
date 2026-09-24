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
replaced through Cursor's editor with the file now at `templates/cursor/cloud-entry.md`; its new
cloud-entry content and description were visibly verified after saving. Scoped
User Context preferences now route explicit Project de-slop requests to that
entry, with a published-package fallback when Context files are unavailable.
Local and old-Mac CLI packages retain the full standalone runtime.

## Live OpenCode repair-to-clean goal (2026-09-24)

The locally installed skill ran one uninterrupted `--until-clean` goal against a
disposable one-partition Git repository at `/private/tmp/deslop-live-e2e-sdh8kzf4`.
The harness was OpenCode with `opencode-go/muse-spark-1.3-contributor`. The
fixture contained a documented ASCII-decimal parser contract, an `eval(raw)`
defect, and existing regression tests for accepted values and rejected Python
expressions. The loop used caps of three fixes, six reviews, 900 seconds, and
180 seconds per agent call.

In that same goal, the first review accepted one P0 finding (`DSL-000001`),
the fixer replaced `eval` with ASCII-digit validation and base-10 conversion,
both recorded check commands passed, and the independent verifier returned
PASS. Two further complete reviews of the single partition accepted no
findings. Persisted state reports one fix attempt, three review calls, two
consecutive empty sweeps, `status=complete`, and `stop_reason=until_clean`.
An independent `make test` also passed all three fixture tests after the run.
No manual patch, recovery action, or new goal occurred between finding and
completion.

This is a live end-to-end proof for this small fixture and provider route.
Empty sweeps remain scope-limited review evidence, not proof that every defect
in a repository is found. OpenCode also created an untracked
`.opencode/goals/state.json`; the harness treats this known provider state as
outside the source fingerprint. The fixture's product change remains uncommitted.

## Live Codex context and goal tests (2026-09-24)

The installed Codex adapter was exercised with `gpt-5.6-luna` at `medium`
reasoning in disposable Git repositories. The per-run reasoning override did
not change the user's global Codex configuration. Ten review/fix/verify calls
in the repair fixture had ten distinct Codex session IDs; the harness passed
finding IDs, diffs and check evidence through files between those sessions.

In the repair fixture at `/private/tmp/deslop-live-codex-luna-_o63ycy3`, Luna
found and fixed the seeded `eval` defect. Checks passed and independent
verification marked it verified. Luna then accepted a P2 input-length cap that
changed the fixture's documented contract, and its verifier passed that
change. A third finding arose from that cap; its fix added a regression test
using `sys.get_int_max_str_digits`, which was unavailable in the deterministic
check environment's Python 3.9. Both recorded test commands failed, the
verifier returned FAIL, and the loop stopped without a clean claim. A
continuation retained the original three-fix budget, reported
`max_iterations_reached` with four of eight reviews used, and a second
continuation preserved that exhausted goal without launching another model.
This run demonstrates safe stopping and budget persistence, while also showing
that a cheaper model can accept a poor finding and miss contract drift.

A separate clean fixture at `/private/tmp/deslop-live-codex-luna-clean-8ox9rehf`
completed after two full empty reviews. An unchanged `deslop-continue.sh`
invocation preserved completion without spending budget. After a harmless
source comment was committed, the same goal reopened coverage and completed
two new empty reviews. Persisted state ended at four of five review calls,
zero fix attempts, two empty sweeps, and `stop_reason=until_clean`. All four
reviews used distinct Codex sessions. This proves the local Codex adapter's
clean-goal, unchanged-resume, and stale-content-reopen paths on this small
fixture; it does not establish model-independent finding quality.

The CLI's configured `gpt-6-sol` was rejected by the ChatGPT account before
review even though static doctor checks reported ready. Doctor output now
labels its checks as static and warns that a provider can reject a configured
model at run time. A separate `gpt-5.6-sol` run exposed a verifier prompt that
incorrectly required a new test even when existing focused tests already
covered the fix; the prompt and its explicit-new-test gate were corrected
before the Luna runs. Repository `make ci` passed all 147 tests after the
verifier, per-run reasoning override, and doctor wording changes.
