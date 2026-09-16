---
description: Run the bounded Ultimate De-Slop review, fix, check, verify loop in the current repository.
argument-hint: "[--review-only] [--until-clean] [--max-iterations N] [--max-review-calls M] [--max-seconds S] [--priority P0,P1,P2] [--allow-dirty] [--continue] [--new-goal]"
---

# Ultimate De-Slop

Run Ultimate De-Slop in the current repository through the installed local harness.

Arguments: $ARGUMENTS

## Find the skill

`SKILL_DIR` is the absolute directory containing the loaded skill (resolve it from the skill path; do not guess). Run every command with cwd set to the current repository (`TARGET`); `TARGET` never needs to contain skill scripts. Look for the installed skill in this order:

1. `.claude/skills/ultimate-de-slop`
2. `$HOME/.claude/skills/ultimate-de-slop`
3. `$HOME/.agents/skills/ultimate-de-slop`

## Run

If `--review-only` is present (review, audit, findings-only, no edits, read-only):

```sh
"$SKILL_DIR/scripts/deslop-review.sh"
```

Report findings and stop. Do not fix, do not continue.

If `--continue` is present:

```sh
"$SKILL_DIR/scripts/deslop-continue.sh"
```

`--continue` retains the stored goal limits and usage from `.deslop/state.json`; it does not reset them. Use `--new-goal` only when the user explicitly asked for a fresh goal.

Otherwise run the loop. Default to `--until-clean` (scope P0,P1,P2; 100 total fix attempts; 200 review calls; 28800 seconds) when the user did not explicitly bound the run:

```sh
"$SKILL_DIR/scripts/deslop-loop.sh" --until-clean
```

Bounded alternative (only on explicit user bound):

```sh
"$SKILL_DIR/scripts/deslop-loop.sh" --max-iterations 5 [--max-review-calls M] [--max-seconds S] [--priority P0,P1,P2]
```

Forward explicit harness flags from `$ARGUMENTS`.

Do not set `DESLOP_MODEL` unless the user asked to override the OAuth/session model. Child agents should use the model already selected in Claude Code.

## Parent-agent continue rules

After every loop or continue command:

1. Run `"$SKILL_DIR/scripts/deslop-status.py"` and read `loop_outcome`.
2. Do **not** load `.deslop/runs/` or raw agent logs into chat unless debugging.
3. Only when none of the stop conditions below applies, while work remains within caps (`next` is not `NONE`, or the two-sweep clean proof at goal scope is unsatisfied), run `"$SKILL_DIR/scripts/deslop-continue.sh"` again in the same task.
4. Stop and report the exact stop reason (`clean`, budget cap, `stop_file`, external failure, dirty tree, `needs_human`/`blocked`, `finalize_halt`). An empty queue alone is not clean. Never call `continue` repeatedly to bypass an exhausted cap; `--new-goal` only on explicit user request.
5. Report run directory, goal usage, loop summary, verified findings, and the next suggested command.
