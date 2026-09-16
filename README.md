# Ultimate De-Slop

[![CI](https://github.com/Niko96-dotcom/ultimate-de-slop/actions/workflows/ci.yml/badge.svg)](https://github.com/Niko96-dotcom/ultimate-de-slop/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-111827.svg)](LICENSE)
[![GitHub Pages](https://img.shields.io/badge/landing-page-0f766e.svg)](https://niko96-dotcom.github.io/ultimate-de-slop/)
[![Built for agent CLIs](https://img.shields.io/badge/agent--cli-Codex%20%7C%20Claude%20%7C%20OpenCode%20%7C%20Cursor-2563eb.svg)](#adapter-matrix)

**Ultimate De-Slop is a local, auditable harness for bounded AI-agent code-quality repair loops.** It turns vague "clean this repo up" energy into a controlled pipeline: strict structural review, conservative arbitration, one-finding fixes, deterministic checks, independent verification, and explicit stop rules. Bare invocation runs until no actionable slop remains at goal scope — robustly even with weak agents — or stops with an honest incomplete report.

![Ultimate De-Slop workflow hero](docs/assets/ultimate-de-slop-hero.png)

## Why It Exists

Most agent cleanup sessions fail in one of two ways: they either stop at a pile of findings, or they keep editing until the diff is too broad to trust. Ultimate De-Slop sits outside the agent and keeps the loop narrow, restartable, and inspectable.

| Problem | Ultimate De-Slop response |
| --- | --- |
| Broad rewrites | Fixers receive exactly one accepted finding at a time. |
| Weak or subjective findings | A read-only arbiter rejects vague, low-confidence, or stylistic work. |
| Hidden agent output | Prompts, raw output, extracted JSON, check logs, and state are written under `.deslop/`. |
| Verification drift | A separate read-only verifier judges the patch against the original finding and checks. |
| Infinite loops | Fix-attempt, review-call, and seconds caps plus the two-sweep clean proof halt the run. |
| False "done" | An empty queue alone is not clean; two complete consecutive empty sweeps at goal scope are required. |

## Quick Start

Clone and install the skill for your agent harness:

```sh
git clone https://github.com/Niko96-dotcom/ultimate-de-slop.git
cd ultimate-de-slop
scripts/install/install-codex.sh
```

Run the harness inside the repository you want to improve (`SKILL_DIR` is the absolute directory containing the loaded `SKILL.md`; commands run with cwd set to your repo; the target repo never needs skill scripts):

```sh
SKILL_DIR="$HOME/.codex/skills/ultimate-de-slop"
cd /path/to/your/repo
"$SKILL_DIR/scripts/deslop-init.sh"
"$SKILL_DIR/scripts/deslop-doctor.py"
"$SKILL_DIR/scripts/deslop-status.py"
"$SKILL_DIR/scripts/deslop-loop.sh" --until-clean
```

`--until-clean` defaults to scope `P0,P1,P2` with 100 total fix attempts, 200 review calls, and 28800 seconds. The goal persists in `.deslop/state.json`. Continue within caps with `deslop-continue.sh` (limits and usage retained); start a deliberate fresh goal only with `--new-goal`. Override caps explicitly:

```sh
"$SKILL_DIR/scripts/deslop-loop.sh" --until-clean --priority P0,P1
"$SKILL_DIR/scripts/deslop-loop.sh" --max-iterations 5 --priority P0,P1
"$SKILL_DIR/scripts/deslop-loop.sh" --max-iterations 5 --max-review-calls 50 --max-seconds 7200
```

Use review-only mode when you want findings without edits:

```sh
"$SKILL_DIR/scripts/deslop-review.sh"
```

## Loop Lifecycle

| Stage | Actor | Write access | Output |
| --- | --- | --- | --- |
| Inventory | deterministic harness | `.deslop/` only | repo map, command hints, risk partitions |
| Review | reviewer agent | read-only | candidate P0/P1/P2 findings |
| Arbitration | arbiter agent + harness | read-only | accepted, rejected, merged, next finding |
| Fix | fixer agent | workspace-write | one bounded patch |
| Checks | deterministic harness | run logs only | pass/fail/blocked check records |
| Verify | verifier agent | read-only | PASS, FAIL, NEEDS_HUMAN, or FALSE_POSITIVE |
| Finalize | deterministic harness | `.deslop/` and optional git commit | updated state and next command |

Each child-agent call is a fresh bounded invocation; memory between calls is disk state (`.deslop/`, git history, per-finding snapshots), never chat history. Reviewer, fixer, and verifier always run in separate contexts.

## Safety Defaults

| Default | Value |
| --- | --- |
| Entry invocation | `--until-clean` (scope P0,P1,P2; 100 fix attempts; 200 review calls; 28800s) |
| Bounded alternative | `--max-iterations N` with optional `--max-review-calls` / `--max-seconds` |
| Continuation | `--continue` retains limits/usage; `--new-goal` only on explicit request |
| Clean proof | two complete consecutive empty sweeps at goal scope (empty queue alone not clean) |
| Review, arbitration, verification | read-only |
| Fixer scope | one accepted finding |
| P3/style nit fuel | rejected by default |
| Parallel writers | disabled |
| Commits | opt-in with `--commit` |
| Auto revert | opt-in with `--auto-revert` |
| Dirty tree | blocked unless `--allow-dirty` |
| Runtime stop | `touch .deslop/stop` |
| Agent wall timeout | 5400s (90 min) per child call |
| Agent idle timeout | 1200s for Codex; **disabled** for buffering harnesses (Cursor, Claude, …) |

Long fix/review runs can hit the **idle** cap when a harness CLI goes silent on stdout (common with `cursor-agent` during long tool sessions). **Buffering harnesses** (Cursor, Claude, OpenCode, Pi, Command Code, Hermes) now default to **idle disabled** — only the 90-minute wall cap applies. Codex keeps the 20-minute idle cap because it streams output more reliably.

```sh
# persist a custom idle cap (applies to all harnesses)
scripts/deslop-loop.sh --agent-idle-timeout-seconds 3600 --allow-dirty

# force idle cap off everywhere
export DESLOP_IDLE_TIMEOUT_SECONDS=0

# fix-only idle cap for streaming harnesses like codex (default 3600s)
export DESLOP_FIX_IDLE_TIMEOUT_SECONDS=7200
```

Wall-clock cap: `--agent-timeout-seconds` / `DESLOP_TIMEOUT_SECONDS` (default 5400). `scripts/deslop-doctor.py` prints effective values for your harness. Never call `continue` repeatedly to bypass an exhausted cap; report the exact stop reason and resume only via deliberate user decision.

## Adapter Matrix

| Harness | Status | Invocation style |
| --- | --- | --- |
| Codex | supported default | `codex exec` with schema and last-message capture |
| Claude | supported adapter | `claude -p` with JSON/schema flags where available |
| OpenCode | supported adapter | `opencode run --format json --file <prompt>` |
| Cursor | supported adapter | `cursor-agent --print --output-format json` |
| Pi | supported adapter | `pi --print` with prompt-file instructions |
| Command Code | supported adapter | `commandcode --print` |
| Hermes | supported adapter | `hermes -z <prompt-file instruction>` |
| OpenClaw | guarded adapter | conservative actionable failure until CLI contract is confirmed |

Select a harness per run:

```sh
DESLOP_HARNESS=opencode "$SKILL_DIR/scripts/deslop-review.sh"
DESLOP_HARNESS=codex DESLOP_MODEL=gpt-5.3-codex-spark "$SKILL_DIR/scripts/deslop-loop.sh" --until-clean
```

See `references/runtime-adapters.md` for the guarded-adapter rule (no invented CLI guarantees).

## Installation Options

Installers live under `scripts/install/` and accept `--scope global`, `--scope local`, `--project-dir PATH`, `--home PATH`, and `--dry-run`.

| Command | Installs for |
| --- | --- |
| `scripts/install/install-codex.sh` | Codex skill, agent profiles, and `/ultimate-de-slop` slash command |
| `scripts/install/install-claude.sh` | Claude skill directory and `/ultimate-de-slop` slash command |
| `scripts/install/install-opencode.sh` | OpenCode skill directory and `/ultimate-de-slop` command |
| `scripts/install/install-cursor.sh` | Cursor skill directory and `/ultimate-de-slop` slash command |
| `scripts/install/install-pi.sh` | Pi skill directory |
| `scripts/install/install-commandcode.sh` | Command Code skill directory |
| `scripts/install/install-hermes.sh` | Hermes software-development skill directory |
| `scripts/install/install-openclaw.sh` | OpenClaw skill directory |
| `scripts/install/install-all.sh` | every supported layout |

## Artifacts

| Path | Purpose |
| --- | --- |
| `.deslop/config.json` | local loop settings |
| `.deslop/state.json` | durable goal (scope, caps, usage), counters, stop state, loop outcome |
| `.deslop/inventory.json` | deterministic repository inventory |
| `.deslop/index.md` | human-readable inventory and command summary |
| `.deslop/findings.jsonl` | append-friendly finding state |
| `scripts/deslop-doctor.py` | harness PATH/auth/model readiness check |
| `scripts/deslop-resume.py` | resolve `needs_human` / `blocked` findings |
| `.deslop/runs/` | prompts, raw output, extracted JSON, check logs |

Runtime artifacts are intentionally gitignored in this repository.

## Repository Map

| Path | Contents |
| --- | --- |
| `scripts/` | deterministic harness, loop, status, scoring, checks, agent runner |
| `scripts/install/` | multi-harness installers |
| `references/` | architecture notes, rubrics, schemas, stop policy, examples |
| `templates/` | Codex agent profiles and portable role guidance |
| `tests/` | local unittest coverage for the control plane |
| `docs/` | GitHub Pages landing page |

## Loop Outcome Summary

After a loop exits, `deslop-status.py` prints why it stopped (including `clean` vs. exact incomplete reason), the goal usage (fix attempts / review calls / elapsed seconds), which findings were verified in that run, what is still queued, and any `needs_human` / `false_positive` reasons. The same summary is persisted as `.deslop/state.json` → `loop_outcome`.

## Deterministic Proof

CI proves the control plane without a live model. A fake Codex CLI drives review → fix → checks → verify → finalize and asserts a clear `no_eligible_findings` stop outcome. See [references/proof-run.md](references/proof-run.md) and the live soak template in [references/soak-runs.md](references/soak-runs.md). Researched designs in [references/research-notes.md](references/research-notes.md) are design inputs only, distinct from these proven guarantees; no design promises arbitrarily weak agents finish or all bugs are found.

```sh
python3 -m unittest tests.test_harness.HarnessTests.test_deterministic_proof_run -v
```

## Development

Run the local control-plane tests:

```sh
python3 -m unittest discover -s tests -v
```

Run syntax checks:

```sh
python3 -m compileall scripts tests
bash -n scripts/*.sh scripts/install/*.sh
```

## Project Links

| Resource | Link |
| --- | --- |
| Landing page | <https://niko96-dotcom.github.io/ultimate-de-slop/> |
| Issues | <https://github.com/Niko96-dotcom/ultimate-de-slop/issues> |
| Discussions | <https://github.com/Niko96-dotcom/ultimate-de-slop/discussions> |
| Security policy | [SECURITY.md](SECURITY.md) |
| Contributing guide | [CONTRIBUTING.md](CONTRIBUTING.md) |
| Changelog | [CHANGELOG.md](CHANGELOG.md) |

## License

MIT. See [LICENSE](LICENSE).
