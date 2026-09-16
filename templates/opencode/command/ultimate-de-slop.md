---
description: Run the bounded Ultimate De-Slop review, fix, check, verify loop in the current repository.
argument-hint: "[--review-only] [--until-clean] [--max-iterations N] [--max-review-calls M] [--max-seconds S] [--priority P0,P1,P2] [--allow-dirty] [--continue] [--new-goal]"
tools:
  bash: true
  read: true
  glob: true
  grep: true
---

<objective>
Run Ultimate De-Slop in the current repository through the installed local harness.
</objective>

<context>
Arguments: $ARGUMENTS
</context>

<process>
Find the installed skill directory in this order:

1. `.opencode/skills/ultimate-de-slop`
2. `.opencode/skill/ultimate-de-slop`
3. `$HOME/.config/opencode/skills/ultimate-de-slop`
4. `$HOME/.config/opencode/skill/ultimate-de-slop`
5. `$HOME/.agents/skills/ultimate-de-slop`
6. `$HOME/.claude/skills/ultimate-de-slop`

`SKILL_DIR` is the absolute directory containing the loaded skill (resolve it from the skill path; do not guess). Run every command with cwd set to the current repository; the target repo never needs to contain skill scripts.

If `--review-only` is present (review, audit, findings-only, no edits, read-only), run:

```sh
DESLOP_HARNESS=opencode "$SKILL_DIR/scripts/deslop-review.sh"
```

Report findings and stop. Do not fix, do not continue.

If `--continue` is present, run:

```sh
DESLOP_HARNESS=opencode "$SKILL_DIR/scripts/deslop-continue.sh"
```

`--continue` retains the stored goal limits and usage from `.deslop/state.json`; it does not reset them. Use `--new-goal` only when the user explicitly asked for a fresh goal.

Otherwise run the loop. Default to `--until-clean` (scope P0,P1,P2; 100 total fix attempts; 200 review calls; 28800 seconds):

```sh
DESLOP_HARNESS=opencode "$SKILL_DIR/scripts/deslop-loop.sh" --until-clean
```

Bounded alternative (only on explicit user bound):

```sh
DESLOP_HARNESS=opencode "$SKILL_DIR/scripts/deslop-loop.sh" --max-iterations 5 [--max-review-calls M] [--max-seconds S] [--priority P0,P1,P2]
```

After every loop/continue command, run status and read `loop_outcome`. While work remains within caps (`next` is not `NONE`, or the two-sweep clean proof at goal scope is unsatisfied), continue in the same task. Stop and report the exact stop reason (`clean`, budget cap, `stop_file`, external failure, dirty tree, `needs_human`/`blocked`, `finalize_halt`); an empty queue alone is not clean. Never call `continue` repeatedly to bypass an exhausted cap.

Forward any explicit `$ARGUMENTS` flags that belong to the harness. Report the `.deslop/` run directory, goal usage, status, and next command.
</process>
