# Contributing

Thanks for helping keep Ultimate De-Slop bounded and auditable.

This is a local CLI harness (Python 3 + Bash, stdlib only). There are no
services, servers, or databases to run. Propose behavior changes as small,
reviewed patches with tests.

## Dependencies

- Python 3 (CI runs `3.12`, see `.github/workflows/ci.yml`).
- Bash for `scripts/*.sh` and `scripts/install/*.sh`.
- Standard library only. There is no `requirements.txt`,
  `pyproject.toml`, or lockfile. Nothing needs installing.

Check versions with:

```sh
python3 --version
bash --version
```

## Setup

```sh
git clone https://github.com/Niko96-dotcom/ultimate-de-slop.git
cd ultimate-de-slop
make ci
```

No service credentials are needed for the deterministic stages
(`deslop-init.sh`, `deslop-inventory.py`, `deslop-status.py`,
`deslop-next.py`, `deslop-run-checks.sh`). Stages that call an external
agent CLI (`deslop-review.sh`, `deslop-fix.sh`, `deslop-verify.sh`,
selected via `DESLOP_HARNESS`, default `codex`) exit `127` when that CLI
is not on `PATH`. Unit tests stub those CLIs; see `tests/test_harness.py`.

Commands must run inside a git repository. Keep `.deslop/` out of git in
target repos so `deslop-fix.sh` dirty-tree protection works as documented.

## Checks

`Makefile` is the source of truth (mirrors `.github/workflows/ci.yml`):

```sh
make ci
make syntax
make test
```

That is:

```sh
python3 -m compileall scripts tests
bash -n scripts/*.sh scripts/install/*.sh
python3 -m unittest discover -s tests -v
```

Paste the exact command and result in your PR.

Focused examples (all resolve in `tests/`):

```sh
python3 -m unittest tests.test_harness.HarnessTests.test_deterministic_proof_run -v
python3 -m unittest tests.test_loop_reliability.LoopSupportTests.test_two_sweeps_required -v
python3 -m unittest tests.test_gate_reliability.GateReliabilityTests.test_fix_rejects_wrong_finding_id -v
python3 -m unittest tests.test_proof_safety.ProofSafetyTests.test_proof_rejects_new_attempt_or_content_change -v
```

The first example is the deterministic proof run documented in
`references/proof-run.md`.

## Architecture expectations

Read `README.md` and `references/architecture.md` before changing the
control plane.

- Deterministic harness owns state, scoring, checks, snapshots, and
  lifecycle transitions under `.deslop/`.
- Adapters stay thin; they format prompts and extract JSON, never own
  finding state or validation.
- Fixers stay narrow: one accepted finding per run.
- Prefer explicit `blocked` or `needs_human` over treating a partial run
  as success.
- When the control plane changes, add or update `tests/` coverage and
  keep `references/`, `README.md` tables, and schemas consistent.

## Review checklist

| Area | Expected proof |
| --- | --- |
| Control-plane change | New or updated test in `tests/` |
| Python | `python3 -m compileall scripts tests` passes |
| Shell | `bash -n scripts/*.sh scripts/install/*.sh` passes |
| Full gate | `make ci` passes |
| Safety | Fixer scope stays bounded; no hidden writes outside documented `.deslop/` and single-finding paths |
| Privacy | Shared logs and examples contain no secrets, private repo contents, or raw agent transcripts |

## Reporting bugs

Open an issue with OS, `python3 --version`, harness and agent CLI
version, exact command, sanitized reproduction, and expected versus
actual behavior. Sanitize `.deslop/runs/` excerpts before posting; never
include secrets or private code.
