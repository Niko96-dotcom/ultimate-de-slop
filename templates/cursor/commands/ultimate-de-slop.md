---
description: Run the bounded Ultimate De-Slop review, fix, check, verify loop in the current repository.
argument-hint: "[--review-only] [--max-iterations N] [--priority P0,P1[,P2]] [--allow-dirty] [--continue]"
---

# Ultimate De-Slop

Run Ultimate De-Slop in the current repository through the installed local harness.

Arguments: $ARGUMENTS

## Find the skill

Look for the installed skill in this order:

1. `.cursor/skills/ultimate-de-slop`
2. `$HOME/.cursor/skills/ultimate-de-slop`
3. `$HOME/.agents/skills/ultimate-de-slop`

## Run

If `--review-only` is present:

```sh
"$SKILL_DIR/scripts/deslop-review.sh"
```

If `--continue` is present:

```sh
"$SKILL_DIR/scripts/deslop-continue.sh"
```

Otherwise run the bounded loop. Default to persisted config / `--max-iterations 5 --priority P0,P1` when those flags are not supplied:

```sh
"$SKILL_DIR/scripts/deslop-loop.sh" --max-iterations 5 --priority P0,P1
```

Forward explicit harness flags from `$ARGUMENTS`.

## Parent-agent continue rules

After every loop or continue command:

1. Run `"$SKILL_DIR/scripts/deslop-status.py"`.
2. Do **not** load `.deslop/runs/` or raw agent logs into chat unless debugging.
3. If `next` is not `NONE`, or status suggests another review wave is still allowed, run `"$SKILL_DIR/scripts/deslop-continue.sh"` again in the same task.
4. Stop only when `loop_outcome.stop_reason` is `no_eligible_findings`, `max_iterations_reached`, `finalize_halt`, or `.deslop/stop` exists.
5. Report run directory, loop summary, verified findings, and the next suggested command.
