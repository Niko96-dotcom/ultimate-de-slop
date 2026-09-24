# Getting Started with Ultimate De-Slop

Companion to [../README.md](../README.md). Read before your first model-backed edit.

You will: install once, inspect a target repo deterministically, then deliberately choose a small bounded model-backed run and inspect the outcome.

## 1. What it does

Local CLI harness (Python 3 + Bash, stdlib only) for bounded loops: read-only review, arbitration, one-finding fix, deterministic checks, independent read-only verification, explicit stop policy.

Good fit for small, verifiable structural cleanups with an audit trail under `.deslop/`. Poor fit for quick single-file fixes, broad rewrites, or formatting-only work.

Limits:

- No guarantee weak agents finish or all bugs are found. Vague or stylistic findings are rejected or escalated.
- Only P0/P1/P2 findings fuel the loop; P3/style is rejected by default (see [severity rubric](../references/severity-rubric.md)).
- `until-clean` is scope-limited: two complete consecutive empty sweeps at the goal scope plus zero unresolved work (see [stop policy](../references/stop-policy.md)).
- OpenClaw is guarded: it fails closed until its non-interactive contract is confirmed (see [runtime adapters](../references/runtime-adapters.md)).

## 2. Prerequisites

- Python 3 (CI uses 3.12), Bash, git. No third-party packages, servers, or ports.
- Run every harness command with cwd inside a git repository.
- Deterministic stages need no provider: `deslop-init.sh`, `deslop-inventory.py`, `deslop-status.py`, `deslop-next.py`, `deslop-run-checks.sh`.
- Model stages (`deslop-review.sh`, `deslop-fix.sh`, `deslop-verify.sh`, fix/verify parts of `deslop-loop.sh`) invoke the `DESLOP_HARNESS` CLI (default `codex`; also `claude`, `opencode`, `cursor`, `pi`, `commandcode`, `hermes`; `openclaw` guarded). Without that CLI on `PATH` they exit `127`.
- Model stages send repository context (prompts, code excerpts, diffs, check logs) to the selected provider. That provider's usage, billing, and data policies apply and can differ from local runtime. Timeouts bound local runtime only, not spend.

## 3. Install once, run in the target

Install and run use different directories.

Copy the package into an agent home once. Installers live at `scripts/install/` and accept `--scope global|local` (default `global`), `--project-dir PATH`, `--home PATH`, `--dry-run`.

| Installer | Global target | Local target (`--project-dir TARGET`) |
| --- | --- | --- |
| `install-codex.sh` | `$HOME/.codex/skills/ultimate-de-slop` | `TARGET/.codex/skills/ultimate-de-slop` |
| `install-claude.sh` | `$HOME/.claude/skills/ultimate-de-slop` | `TARGET/.claude/skills/ultimate-de-slop` |
| `install-opencode.sh` | `$HOME/.config/opencode/skills/ultimate-de-slop` | `TARGET/.opencode/skills/ultimate-de-slop` |
| `install-cursor.sh` | `$HOME/.cursor/skills/ultimate-de-slop` | `TARGET/.cursor/skills/ultimate-de-slop` |
| `install-pi.sh` | `$HOME/.pi/skills/ultimate-de-slop` | `TARGET/.pi/skills/ultimate-de-slop` |
| `install-commandcode.sh` | `$HOME/.commandcode/skills/ultimate-de-slop` | `TARGET/.commandcode/skills/ultimate-de-slop` |
| `install-hermes.sh` | `$HOME/.hermes/skills/software-development/ultimate-de-slop` | `TARGET/.hermes/skills/software-development/ultimate-de-slop` |
| `install-openclaw.sh` | `$HOME/.openclaw/skills/ultimate-de-slop` | `TARGET/.openclaw/skills/ultimate-de-slop` |

```sh
git clone https://github.com/Niko96-dotcom/ultimate-de-slop.git
cd ultimate-de-slop
scripts/install/install-codex.sh --dry-run
scripts/install/install-codex.sh
```

Call the installed copy `SKILL_DIR` (for example `$HOME/.codex/skills/ultimate-de-slop`). Do not manually copy scripts; use the installers. With `--scope local` the skill lives under `TARGET`, but cwd for every run is still `TARGET`:

```sh
SKILL_DIR="$HOME/.codex/skills/ultimate-de-slop"
cd /path/to/your/repo
"$SKILL_DIR/scripts/deslop-init.sh"
```

## 4. Prepare a clean tree first

`deslop-fix.sh` refuses on any dirty tree unless passed `--allow-dirty`. The loop ignores `.deslop/` paths but blocks on other uncommitted changes. Do this before `init`, because `init` writes `.deslop/` state and editing `.gitignore` itself dirties the tree.

In the target repo, edit `.gitignore` in an editor to add `.deslop/` (ensure a trailing newline) and commit only that intended ignore change if you want it recorded. To avoid touching tracked files, add `.deslop/` to `.git/info/exclude` instead. Do not blindly commit or stash unrelated work.

```sh
cd /path/to/your/repo
git status --porcelain
```

If output is non-empty, resolve it deliberately or pass `--allow-dirty` for that invocation. After fixing, `deslop-status.py` shows verified-but-uncommitted work; commit it yourself unless you passed `--commit`.

## 5. Safe first run: deterministic inspection

No provider, no source edits. Writes only `.deslop/` state (`config.json`, `state.json`, `findings.jsonl`, `inventory.json`, `index.md`).

```sh
SKILL_DIR="$HOME/.codex/skills/ultimate-de-slop"
cd /path/to/your/repo
"$SKILL_DIR/scripts/deslop-init.sh"
"$SKILL_DIR/scripts/deslop-inventory.py" --write
"$SKILL_DIR/scripts/deslop-status.py"
"$SKILL_DIR/scripts/deslop-next.py"
```

Useful options (all verified against source): `deslop-inventory.py --json`, `--max-files N` (default 20000); `deslop-status.py --json`; `deslop-next.py --priority P0,P1,P2` (default), `--json`. Read `.deslop/index.md` for partitions and detected commands. Nothing has called a model yet.

## 6. Deliberately go model-backed

Install and log in to the selected agent CLI, then check readiness:

```sh
"$SKILL_DIR/scripts/deslop-doctor.py"
```

Doctor may invoke the CLI to check authentication. It exits 1 if the CLI or login is missing; it is not required for the provider-free inspection above. Use `--harness NAME` to select a harness or `--json` for structured output.

Read-only review first (findings, no source edits):

```sh
"$SKILL_DIR/scripts/deslop-review.sh"
"$SKILL_DIR/scripts/deslop-review.sh" --partition src
```

`--partition` must be an existing repo-relative directory. Inspect the result with `deslop-status.py` before fixing.

Then prefer a small bounded loop over `--until-clean`:

```sh
"$SKILL_DIR/scripts/deslop-loop.sh" --max-iterations 5 --priority P0,P1
```

Caps (stated once): bounded default is 5 iterations at `P0,P1`. `--until-clean` defaults to scope `P0,P1,P2`, 100 fix attempts, 200 review calls, 28800 seconds, with the goal in `TARGET/.deslop/state.json`. `--max-review-calls` / `--max-seconds` require `--until-clean`. `--continue` retains stored limits/usage; `--new-goal` (only with `--until-clean`) starts a fresh goal on explicit request. If an `until-clean` goal is active/exhausted/failed, bare bounded runs refuse; follow the message.

```sh
DESLOP_HARNESS=opencode "$SKILL_DIR/scripts/deslop-review.sh"
DESLOP_HARNESS=codex "$SKILL_DIR/scripts/deslop-loop.sh" --until-clean
```

Child agents use the harness session/OAuth model; set `DESLOP_MODEL` only to override.

Local artifacts live under `TARGET/.deslop/` (`runs/` holds prompts, raw output, JSON, check logs) and can contain code excerpts. Timeouts (wall default 5400s; idle 1200s for Codex, disabled `0` for buffering harnesses, fix-only 3600s) bound runtime only. `deslop-doctor.py` prints effective values.

## 7. Inspect outcomes and stop safely

After every loop command:

```sh
"$SKILL_DIR/scripts/deslop-status.py"
```

Read `loop_outcome`: stop reason, verified IDs, queued next, and `needs_human` / `blocked` / `fixing` / `fixed_unverified` detail. An empty queue alone is not clean.

Stop cooperatively from the target root:

```sh
touch .deslop/stop
```

The loop finishes the current stage and records `stop_file`. `deslop-continue.sh` only runs `deslop_loop.py --continue` and does not remove the stop file, so resume deliberately within remaining budget:

```sh
rm .deslop/stop
"$SKILL_DIR/scripts/deslop-continue.sh"
```

Findings left in `fixing` / `fixed_unverified` need explicit recovery: inspect the diff and checks first, then record a decision after investigating:

```sh
"$SKILL_DIR/scripts/deslop-resume.py" FINDING_ID --as accepted --reason "..."
```

Do not fabricate `verified`; checks plus finalize are required. `needs_human` / `blocked` items need a person.

Advanced single-finding path:

```sh
"$SKILL_DIR/scripts/deslop-fix.sh" FINDING_ID
"$SKILL_DIR/scripts/deslop-run-checks.sh" FINDING_ID
"$SKILL_DIR/scripts/deslop-verify.sh" FINDING_ID  # needs --checks-json or prior checks output
```

## 8. Troubleshooting

| Symptom | Fix |
| --- | --- |
| `could not resolve git root` | Run with cwd inside a git checkout. |
| Exit `127`, `cli not found` | Selected `DESLOP_HARNESS` CLI is missing; run `deslop-doctor.py` or pick one on `PATH`. |
| `git tree is dirty` | Resolve the tree (section 4) or re-run with `--allow-dirty`. |
| `until-clean goal exists ...` | Follow the message; use `--until-clean --continue` or `--until-clean --new-goal`. |
| `openclaw_unsupported` | Expected; use a supported harness. |
| `needs_human` / `blocked` / `fixed_unverified` remain | Inspect finding, diff, and `runs/` logs; resolve, then `deslop-resume.py` if appropriate. |
| Stale `complete` goal after edits | `deslop-continue.sh` reopens a full re-sweep within remaining budget. |

More depth: [architecture](../references/architecture.md), [reviewer rubric](../references/reviewer-rubric.md), [verifier rubric](../references/verifier-rubric.md), [examples](../references/examples.md), [proof run](../references/proof-run.md), [soak runs](../references/soak-runs.md), [Cursor cloud](../references/cursor-cloud.md).

## 9. Verify your setup

From THIS harness source checkout, not the target repo:

```sh
make syntax
make test
make ci
```
