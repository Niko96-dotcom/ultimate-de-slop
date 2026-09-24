# Ultimate De-Slop

[![CI](https://github.com/Niko96-dotcom/ultimate-de-slop/actions/workflows/ci.yml/badge.svg)](https://github.com/Niko96-dotcom/ultimate-de-slop/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-111827.svg)](LICENSE)
[![GitHub Pages](https://img.shields.io/badge/landing-page-0f766e.svg)](https://niko96-dotcom.github.io/ultimate-de-slop/)
[![Built for agent CLIs](https://img.shields.io/badge/agent--cli-Codex%20%7C%20Claude%20%7C%20OpenCode%20%7C%20Cursor-2563eb.svg)](#adapter-matrix)

**Ultimate De-Slop is a local, auditable harness for bounded AI-agent code-quality repair loops.** It turns vague "clean this repo up" into a controlled pipeline: strict structural review, conservative arbitration, one-finding fixes, deterministic checks, independent verification, and explicit stop rules.

![Ultimate De-Slop bounded loop hero](docs/assets/ultimate-de-slop-hero.png)

> New here? Start with [docs/getting-started.md](docs/getting-started.md) for the safe first run, install-vs-target explanation, and troubleshooting.

## How the Loop Flows

| Stage | What happens | Boundary |
| --- | --- | --- |
| Inventory | Map files, commands, and risk partitions | local, no model |
| Review | Report concrete P0/P1/P2 structural findings | read-only, no source edits |
| Arbitrate | Drop weak, duplicate, or low-confidence findings | deterministic |
| Fix | Edit exactly one accepted finding | one finding, snapshots kept |
| Checks | Run expected commands via allowlist | local, logged under `.deslop/runs/` |
| Verify | Independent read-only judgment of the patch | read-only, never self-review |

Full roles and stop rules: [references/architecture.md](references/architecture.md), [SKILL.md](SKILL.md).

## Who It Is For

- Maintainers who want small, reviewable, verified cleanups — not broad rewrites.
- Teams that already use an agent CLI (Codex default; also Claude, OpenCode, Cursor, Pi, Command Code, Hermes) and want the loop bounded and inspectable.
- Skeptics: every model edit must pass deterministic checks and an independent read-only verifier, or it stops with an explicit reason.

Not for: one-off bug fixes, formatting-only runs, style polishing, or repos where you cannot run commands inside a git checkout.

## Limitations

- No guarantee that arbitrarily weak agents finish, nor that every bug is covered. Weak or vague output is rejected or escalated, never silently passed.
- Only P0/P1/P2 structural findings fuel the loop. P3/style nits are rejected by default (see [references/severity-rubric.md](references/severity-rubric.md)).
- Clean claims are scope-limited: `until-clean` needs two complete consecutive empty sweeps at the goal scope plus no unresolved work (see [references/stop-policy.md](references/stop-policy.md)).
- OpenClaw is a guarded adapter: it fails closed until its non-interactive schema-output contract is confirmed. It is not claimed as working (see [references/runtime-adapters.md](references/runtime-adapters.md)).

## Prerequisites

- Python 3 (CI pins 3.12), Bash, git. Stdlib only — nothing to install.
- Run every harness command **inside a git repository** (root is resolved via `git rev-parse`).
- Model-backed stages need an agent CLI on `PATH` (for example `codex`, `claude`, `opencode`, `cursor-agent`). Deterministic inspection needs no provider.

```sh
python3 --version  # 3.12 in CI (.github/workflows/ci.yml)
# From THIS harness source checkout (the clone below), not the target repo:
make ci             # syntax + unit tests; mirrors CI
```

## Install vs. Run: Don't Mix Them Up

- **Install** copies this package into an agent home (once per machine or project). Example global Codex install targets `$HOME/.codex/skills/ultimate-de-slop`; `--scope local` targets the project instead.
- **Run** executes the installed scripts with your shell's cwd set to the **target repo** you want to improve. The target repo never needs skill scripts.

```sh
git clone https://github.com/Niko96-dotcom/ultimate-de-slop.git
cd ultimate-de-slop
scripts/install/install-codex.sh --dry-run   # preview
scripts/install/install-codex.sh              # default --scope global
# per-project alternative:
# scripts/install/install-codex.sh --scope local --project-dir /path/to/your/repo
```

Installers live under `scripts/install/` and accept `--scope global|local`, `--project-dir PATH`, `--home PATH`, `--dry-run`. See [docs/getting-started.md](docs/getting-started.md) for per-harness paths and what gets copied.

Codex also discovers skills in `$HOME/.agents/skills`. `install-all.sh` therefore skips the optional shared `.agents` fallback to avoid showing the same skill twice; use `install-shared-agents.sh` only when that fallback is needed. The Cursor cloud entry is shipped as `templates/cursor/cloud-entry.md`; save it as `SKILL.md` only in Cursor User Context.

## Safe First Run

Do deterministic inspection first (free, local, no model; writes only `.deslop/` state, no source edits). Only then choose a bounded model-backed run deliberately.

Prepare the target tree before any command that writes state. Editing `.gitignore` itself dirties the tree, so decide deliberately: edit `.gitignore` in an editor to add `.deslop/` (ensure a trailing newline) and commit only that intended ignore change if you want it recorded, or add `.deslop/` to `.git/info/exclude` to keep it local without touching tracked files. Do not blindly commit or stash unrelated work.

```sh
SKILL_DIR="$HOME/.codex/skills/ultimate-de-slop"
cd /path/to/your/repo
git status --porcelain  # must be empty for deslop-fix.sh; otherwise pass --allow-dirty deliberately
"$SKILL_DIR/scripts/deslop-init.sh"      # creates .deslop/ + inventory
"$SKILL_DIR/scripts/deslop-status.py"    # score, queue, stop reason, next command
```

Before model-backed stages, install and log in to your selected agent CLI. Run `"$SKILL_DIR/scripts/deslop-doctor.py"` to check readiness; it exits 1 when the CLI or authentication is missing.

Optional read-only model review (no source edits; writes `.deslop/` state and artifacts):

```sh
"$SKILL_DIR/scripts/deslop-review.sh"                 # whole repo
"$SKILL_DIR/scripts/deslop-review.sh" --partition src  # one partition only
```

Deliberate bounded edit loop (starts small):

```sh
"$SKILL_DIR/scripts/deslop-loop.sh" --max-iterations 5 --priority P0,P1
```

Full walkthrough, costs/privacy notes, and troubleshooting: [docs/getting-started.md](docs/getting-started.md).

## Bounded Usage and Stop/Resume

| Goal | Command |
| --- | --- |
| Run until clean at P0,P1,P2 | `deslop-loop.sh --until-clean` |
| Narrow scope | `deslop-loop.sh --until-clean --priority P0,P1` |
| Small bounded run | `deslop-loop.sh --max-iterations 5 --priority P0,P1` |
| Continue within caps | `deslop-continue.sh` (retains limits/usage) |
| Fresh goal only on request | `deslop-loop.sh --until-clean --new-goal` |
| Stop safely | `touch .deslop/stop` from the target root |
| Inspect outcome | `deslop-status.py` (`loop_outcome`: stop reason, verified IDs, queued next) |

Defaults: `--until-clean` is scope `P0,P1,P2` with 100 fix attempts, 200 review calls, 28800 seconds; goal persists in `.deslop/state.json`. `--max-review-calls` / `--max-seconds` require `--until-clean`. Bounded default is 5 iterations at `P0,P1`. Never call `continue` repeatedly to bypass an exhausted cap; `--new-goal` needs an explicit decision. `deslop-continue.sh` only runs `deslop_loop.py --continue` and does not remove `.deslop/stop`: after a `stop_file` halt, deliberately remove it first (`rm .deslop/stop` from the target root) and only continue within remaining budget. Findings left in `fixing` / `fixed_unverified` / `needs_human` / `blocked` need explicit recovery (inspect diff/checks, then `deslop-resume.py`), not just `continue`. Details: [references/stop-policy.md](references/stop-policy.md).

## Safety Defaults

| Default | Value |
| --- | --- |
| Review, arbitration, verification | read-only; fixer takes exactly one accepted finding |
| P3/style fuel | rejected |
| Parallel writers | disabled |
| Commits / auto-revert | opt-in (`--commit` / `--auto-revert`) |
| Dirty tree | `deslop-fix.sh` blocks on any dirty tree unless `--allow-dirty`; loop ignores `.deslop/` paths but blocks on other dirt — add `.deslop/` to the target repo's `.gitignore` |
| Runtime stop | `touch .deslop/stop` (resume needs deliberate `rm .deslop/stop` first) |
| Agent wall timeout | 5400s per child call, bounds runtime only, not provider spend (`--agent-timeout-seconds` / `DESLOP_TIMEOUT_SECONDS`) |
| Agent idle timeout | 1200s for Codex; disabled (0) for buffering harnesses (Cursor, Claude, OpenCode, Pi, Command Code, Hermes); fix-only 3600s for streaming harnesses |

`scripts/deslop-doctor.py` prints effective timeouts. Long silent tool sessions tripping the idle cap are reported as external stops, never as clean.

## Adapter Matrix

| Harness | Status | Invocation style |
| --- | --- | --- |
| Codex | supported default | `codex exec` with schema and last-message capture |
| Claude | supported adapter | `claude -p` with JSON/schema flags where available |
| OpenCode | supported adapter | `opencode run --format json --file <prompt>` |
| Cursor | supported adapter | `cursor-agent --print --output-format json` |
| Pi | supported adapter | `pi --print` with prompt-file instructions |
| Command Code | supported adapter | `commandcode --print` |
| Hermes | supported adapter | `hermes --skills ultimate-de-slop ... -z <prompt-file>` |
| OpenClaw | guarded adapter | fails closed until CLI contract is confirmed; not claimed as working |

Select per run with `DESLOP_HARNESS`. Child agents use the harness session/OAuth model; set `DESLOP_MODEL` only to override. See [references/runtime-adapters.md](references/runtime-adapters.md); cloud/native path: [references/cursor-cloud.md](references/cursor-cloud.md).

```sh
DESLOP_HARNESS=opencode "$SKILL_DIR/scripts/deslop-review.sh"
DESLOP_HARNESS=codex "$SKILL_DIR/scripts/deslop-loop.sh" --until-clean
```

## Artifacts, Costs, Privacy

- Deterministic stages (`init`, `inventory`, `status`, `next`, `run-checks`) are local and free. Model stages call your agent CLI and may incur that provider's normal usage/cost, which can differ from local runtime.
- Model-backed stages send repository context (prompts, code excerpts, diffs, check logs) to the selected provider; that provider's usage, billing, and data policies apply.
- Timeouts bound local runtime only, not monetary spend.
- Runtime state lives under `TARGET/.deslop/`: `config.json`, `state.json` (goal, caps, usage, `loop_outcome`), `inventory.json`, `index.md`, `findings.jsonl`, `runs/` (prompts, raw output, extracted JSON, check logs). These may contain code excerpts — keep them local.
- This repo gitignores `.deslop/`. Add `.deslop/` to your target repo's `.gitignore` so harness state neither blocks `deslop-fix.sh` nor gets committed accidentally.
- After a run, `deslop-status.py` tells you why it stopped, what was verified, what is queued, and what needs a human.

## Repository Map

| Path | Contents |
| --- | --- |
| `scripts/` | deterministic harness, loop, status, scoring, checks, agent runner |
| `scripts/install/` | multi-harness installers |
| `references/` | architecture, rubrics, schemas, stop policy, adapters, examples |
| `templates/` | agent profiles and portable role guidance |
| `tests/` | local unittest coverage for the control plane |
| `docs/` | onboarding ([getting-started](docs/getting-started.md)) plus landing page |

## Advanced References

- Lifecycle and roles: [SKILL.md](SKILL.md), [references/architecture.md](references/architecture.md)
- Review/verify bars: [references/reviewer-rubric.md](references/reviewer-rubric.md), [references/verifier-rubric.md](references/verifier-rubric.md), [references/severity-rubric.md](references/severity-rubric.md)
- Stop/resume: [references/stop-policy.md](references/stop-policy.md)
- Adapters/timeouts: [references/runtime-adapters.md](references/runtime-adapters.md)
- Proven control plane (no live model): [references/proof-run.md](references/proof-run.md) — `python3 -m unittest tests.test_harness.HarnessTests.test_deterministic_proof_run -v`
- Live soaks: [references/soak-runs.md](references/soak-runs.md); examples: [references/examples.md](references/examples.md)
- Cloud Projects: [references/cursor-cloud.md](references/cursor-cloud.md)

## Development

Run from THIS harness source checkout:

```sh
python3 -m unittest discover -s tests -v
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
