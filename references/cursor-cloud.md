# Cursor Projects and Cloud Agents

Use this path in Cursor Projects / Cloud Agents, or when the host provides native
subagents but no separately authenticated CLI. No `cursor-agent`, API key, SDK,
Subscription Squad, Codex coordinator, or service installation is needed.
Python 3, Bash, Git, a writable checkout and the host's native subagent tool are
required. The native subagents must share this checkout and filesystem.

## Project coordinator

A Project coordinator delegates implementation. Give ONE repository execution
agent this skill's absolute path and the user's scope/limits. That execution agent
runs the controller below in its checkout and dispatches the role subagents there.
Do not launch competing controllers or isolate each role into another VM/worktree.
If this skill was explicitly requested but omitted from the Project's skill catalog,
read `skills/ultimate-de-slop/SKILL.md` in User Context using the host's context-file
reader, then pass the full instructions and supporting files to the execution agent.
Do not treat a missing catalog entry as an absent skill.

## Repository execution agent

1. Resolve the loaded skill's real filesystem directory as `SKILL_DIR` and the target
   Git checkout as `TARGET`. If the skill is only a virtual Context file, materialize
   the whole skill folder in the execution VM first, using the host's context-file
   tools. Never pass virtual paths to Bash. A repository checkout of this package
   outside `TARGET` is another supported source. If the Context tools cannot
   materialize the supporting files, fetch the published package into a fresh
   temporary directory (never clone into or overwrite the target checkout):
   ```sh
   PACKAGE_DIR="$(mktemp -d)"
   git clone --depth 1 https://github.com/Niko96-dotcom/ultimate-de-slop.git "$PACKAGE_DIR/skill"
   SKILL_DIR="$PACKAGE_DIR/skill"
   git -C "$SKILL_DIR" rev-parse HEAD
   ```
   Read its `SKILL.md` and this reference before continuing, and record the fetched
   revision. Use a requested revision when one is specified. Do not copy credentials.
2. Check that `scripts/deslop-cloud.py` and the referenced rubrics/schemas exist.
   Confirm native subagents can use this same checkout. If the host cannot delegate,
   report the missing capability; do not impersonate independent verification.
3. Run all commands below with cwd=`TARGET`. Start the controller once:
   ```sh
   python3 "$SKILL_DIR/scripts/deslop-cloud.py" start -- --until-clean
   ```
   Forward user scope/caps as loop flags. For explicit review-only, replace
   `--until-clean` with `--review-only` (optional `--partition PATH`). For a resumed
   goal use `start -- --continue`. Do not reset budgets with `--new-goal` unless
   explicitly requested. The launcher returns immediately; this is NOT completion.
4. Service the controller in the same active task:
   ```sh
   python3 "$SKILL_DIR/scripts/deslop-cloud.py" poll --wait 20
   ```
   For each live request, dispatch exactly ONE fresh native subagent. Use the actual
   host tool/schema, not an invented API. Pass the request's `root`, `prompt` and
   `schema` paths, role and `readonly` policy. Have it read that exact prompt and
   output a JSON object matching that schema. Read-only roles get read-only tools
   wherever supported and must not edit any files. Only the fix role may edit
   product code. All roles must leave `.deslop` control files untouched, must not
   delegate again, and must not recursively invoke this skill. Use the current
   host's configured model unless the user specifies one.
5. Prefer a background native subagent with bounded waits so the parent can poll
   the controller during its work. If the request expires or the controller stops,
   cancel that worker using the host tool and wait for termination; do not submit
   its late answer or start another writer. If only foreground dispatch is exposed,
   use a host timeout no greater than the request's remaining `expires_at` time
   where supported and report any inability to cancel promptly. Wait for the
   subagent to finish. The parent writes its exact returned JSON
   object to `TARGET/.deslop/tmp/native-<request-id>.json` using a file-write tool
   or a literal quoted heredoc. Do not interpolate model text into shell code, and
   do not invent, improve or replace its verdict. Submit it:
   ```sh
   python3 "$SKILL_DIR/scripts/deslop-cloud.py" submit REQUEST_ID RESULT_FILE
   ```
   The controller checks request identity, rejects stale/duplicate responses and
   runs the same arbitration, ownership snapshots, deterministic checks, verifier
   and finalizer as CLI mode. Return to poll. Never run manual fix/check/finalize
   stages alongside the controller. Only the requested prompt/schema and final
   result need to enter role context; do not scan raw historical run logs.
6. While the session is running and no request is pending, checks or finalization
   are executing: poll again with a bounded wait. Do not declare clean. When
   `session.status` is `finished`, run `deslop-status.py` and report its actual
   stop reason and usage. Review-only ends after its review, never starts a loop.
   On interrupted sessions inspect the state before recovery; call `start --
   --continue` only after the original worker is stopped and unresolved patches
   are recovered. Do not silently create a fresh goal.
7. On user stop, subagent failure or cancelled request, stop/cancel the active
   native subagent via the host tool and run `deslop-cloud.py stop`. Wait for the
   worker to stop before permitting another writer. The controller can cancel a
   pending response, but cannot kill a host-managed subagent itself. Keep this
   limitation explicit. A context compaction is not a new run: poll the existing
   controller and reconnect to its worker rather than launching a duplicate.

The active host agent services these requests. Closing a local terminal isn't a
cloud deployment. Cursor Cloud owns the host task lifetime; an expired or cancelled
host task is an interruption, never a clean result. Existing goal and stage wall
limits still apply, including time spent waiting for the parent/subagent.

## Distribution

The entire installed `~/.cursor/skills/ultimate-de-slop/` folder can be synced to
Cloud Agents from Customize → Skills / Settings → Agents. A tracked
`.cursor/skills/ultimate-de-slop/` folder in a target repository is also discoverable;
run `scripts/install/install-cursor.sh --scope local --project-dir TARGET` from this
package to create it. Review and commit those target-repo files if repository-based
distribution is desired. Merely pushing this package's root `SKILL.md` does not
install it into unrelated projects.

Projects may omit personal skills from their initial catalog. A short User Context
preference naming this skill's Context path makes the explicit invocation resolvable
without making it always-on. Do not enable de-slopping for unrelated tasks.

Sources checked 2026-09-16: [skills and sync](https://prod.cursor.com/docs/skills),
[native subagents and shared checkouts](https://prod.cursor.com/docs/subagents),
[Projects](https://cursor.com/changelog/projects).

### Cloud Context loader

`templates/cursor/cloud-entry.md` is a single-file cloud entry for hosts whose
User Context copy does not follow local updates or cannot mount supporting files.
Save that entry as `SKILL.md` in the cloud User Context skill. It fetches the current published
package into the execution VM and pins that checkout for the life of the run.
Keep the full package installed for local CLIs; the loader is only a cloud entry.
