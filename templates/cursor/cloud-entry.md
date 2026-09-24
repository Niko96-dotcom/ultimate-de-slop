---
name: ultimate-de-slop
description: Run a persistent repo-wide de-slop loop in Cursor Projects and Cloud Agents using native subagents, deterministic checks, and verified stopping conditions.
---

# Ultimate De-Slop — cloud entry

This template is named `cloud-entry.md` in the package so other skill scanners do
not discover it as a second skill. When adding it to Cursor User Context, save
its contents as `SKILL.md` there.

Use only when the user asks for Ultimate De-Slop or an iterative repo-wide code-quality cleanup. Review-only requests remain read-only. Do not apply this to unrelated tasks or ordinary small fixes.

This cloud entry loads the maintained standalone package from https://github.com/Niko96-dotcom/ultimate-de-slop. Do not execute the older supporting files beside this Context document. No Subscription Squad, external model CLI, API key, or additional login is required.

## Project coordinator

Delegate ONE repository execution agent. Give it the user's repository, scope, and limits plus ALL instructions below. The execution agent uses its native subagents in its own checkout. Do not launch one controller per role or put the role agents in separate VMs/worktrees.

## Repository execution agent

1. On resume or context compaction, reconnect to the existing controller, its package path, and workers first. Do not clone a new runtime into an active run, restart workers, or reset the stored goal.
2. For a new run, fetch the current package outside the target repository:
   ```sh
   PACKAGE_DIR="$(mktemp -d)"
   git clone --depth 1 https://github.com/Niko96-dotcom/ultimate-de-slop.git "$PACKAGE_DIR/skill"
   SKILL_DIR="$PACKAGE_DIR/skill"
   git -C "$SKILL_DIR" rev-parse HEAD
   ```
   Record the absolute package path and revision in the task's progress so they survive compaction. If the user specified a revision, fetch and use that revision instead. If GitHub access fails, report that concrete failure; do not silently use stale files.
3. Read `$SKILL_DIR/SKILL.md` and `$SKILL_DIR/references/cursor-cloud.md`. Follow the NATIVE cloud procedure, regardless of install markers. Confirm Git, Python 3, Bash, a writable target checkout and native subagent tools are available. Never guess a host tool API or impersonate a separate verifier.
4. With cwd set to the TARGET repository, launch `python3 "$SKILL_DIR/scripts/deslop-cloud.py" start -- --until-clean`. Forward explicit user limits. For review-only use `start -- --review-only`; for a recovered existing goal use `start -- --continue`.
5. Keep servicing `poll --wait 20` and `submit` as described in the cloud procedure. Each request goes to one fresh native role subagent sharing the target checkout. Only the fixer edits product code; reviewers and verifiers are read-only. Return each worker's exact JSON through the controller. The parent never manufactures verdicts, weakens tests, or changes goal counters.
6. Launching the controller is NOT completion. Continue until the recorded stop condition. Default scope is P0/P1/P2 with caps of 100 fix attempts, 200 review calls and 8 hours; clean requires two complete empty sweeps. Preserve these budgets across resume. Stop on exhausted caps, failed tools/checks, unresolved recovery, or user cancellation. Cancel any outstanding native worker before recovery or another writer.
7. Report verified findings, actual checks, stop reason, usage, package revision, and remaining limitations. No commits, pushes, deployment, or automatic revert unless the user requested them.
