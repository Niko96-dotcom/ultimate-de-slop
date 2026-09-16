# Runtime Adapters

Child-agent sessions run through `scripts/deslop-agent-runner.py` (plus the Codex compatibility shim). Adapters construct the CLI command only; the deterministic harness owns lifecycle, JSON extraction, timeouts, snapshots, checks, and state.

Set `DESLOP_HARNESS=<harness>` per run. When unset, the harness is read from `.ultimate-de-slop-install.json` in the installed skill directory; otherwise the default is `codex`. Child agents use the harness session/OAuth model; set `DESLOP_MODEL` only on explicit override (`DESLOP_CODEX_MODEL` remains accepted for Codex compatibility). Prefer harness `login` flows over API-key env vars.

## Adapter matrix

| Harness | Status | Invocation style |
| --- | --- | --- |
| Codex | supported default | `codex exec` with schema and last-message capture |
| Claude | supported adapter | `claude -p` with JSON/schema flags where available; plan mode with Read/Glob/Grep tools for read-only roles |
| OpenCode | supported adapter | `opencode run --format json --file <prompt>` |
| Cursor | supported adapter | `cursor-agent --print --output-format json`; ask mode for read-only roles |
| Pi | supported adapter | `pi --print` with prompt-file instructions and role tool lists |
| Command Code | supported adapter | `commandcode --print`; `--plan` for read-only roles |
| Hermes | supported adapter | `hermes --skills ultimate-de-slop --toolsets ... -z <prompt-file>`; `--yolo` only for a write role when `DESLOP_HERMES_YOLO=1` with `danger-full-access` sandbox |
| OpenClaw | guarded adapter | conservative actionable failure until the exact noninteractive schema-output CLI contract is confirmed. Not claimed as working. |

## Guarded-adapter rule

For OpenClaw (and any future harness without a confirmed contract): fail closed with an actionable message naming what is missing (command, flags, schema-output path). Do not invent CLI flags, output guarantees, or success claims. Record the failure as an external stop, not a pass.

## Timeout notes

Wall-clock cap: `--agent-timeout-seconds` / `DESLOP_TIMEOUT_SECONDS` (default 5400). Idle cap: `--agent-idle-timeout-seconds` / `DESLOP_IDLE_TIMEOUT_SECONDS` (Codex streams reliably so it keeps an idle cap; known-buffering harnesses default idle-disabled). `scripts/deslop-doctor.py` prints effective values. Long silent tool sessions tripping the idle cap are environment behavior, reported as external stops — never as clean.

Read-only adapter commands follow the [Claude CLI tool restriction contract](https://code.claude.com/docs/en/cli-usage) and [Cursor Ask mode](https://prod.cursor.com/docs/cli/overview). They are not an operating-system jail. The runner also detects source changes and rejects unauthorized control-state edits. OpenCode receives its deny-mutation role policy through invocation-local configuration.
