# AGENTS.md

## Cursor Cloud specific instructions

Ultimate De-Slop is a local CLI harness (Python 3 + Bash) that orchestrates bounded
AI-agent code-quality repair loops. There are **no long-running services, servers,
databases, caches, or ports** — everything is one-shot CLI commands.

### Dependencies
- Python 3 (CI pins 3.12) and Bash only. **No third-party packages** (stdlib only;
  there is no `requirements.txt`/`pyproject.toml`/lockfile). Nothing needs installing.

### Lint / test / build / run
Standard commands live in the `Makefile` and `README.md`; use them directly:
- `make ci` — full gate (syntax check + unit tests), mirrors `.github/workflows/ci.yml`.
- `make syntax` — `python3 -m compileall scripts tests` + `bash -n scripts/*.sh scripts/install/*.sh`.
- `make test` — `python3 -m unittest discover -s tests -v`.
There is no build step and no application server to start.

### Non-obvious caveats
- The harness stages `deslop-review.sh`, `deslop-fix.sh`, and `deslop-verify.sh` invoke
  an **external agent CLI** (selected via `DESLOP_HARNESS`, default `codex`; also
  claude/opencode/cursor/pi/commandcode/hermes/openclaw). **None of these agent CLIs are
  installed in the cloud VM**, so those stages exit `127` (CLI not found) unless a matching
  CLI is on `PATH`. The unit tests do not need them — they inject scripted stub CLIs on
  `PATH`. To exercise the loop manually, put a stub executable named after the harness on
  `PATH` that reads the `--output-schema` argument (review/fix/verify) and writes schema-shaped
  JSON to the `--output-last-message` path (see `tests/test_harness.py` for the contract).
- Deterministic stages need **no** agent CLI and run anywhere: `deslop-init.sh`,
  `deslop-inventory.py`, `deslop-status.py`, `deslop-next.py`, `deslop-run-checks.sh`.
- All harness commands must run **inside a git repository** (they resolve the repo root via
  `git rev-parse`).
- `deslop-fix.sh` refuses to run on a dirty git tree unless `--allow-dirty` is passed; add
  `.deslop/` to the target repo's `.gitignore` so harness state does not dirty the tree.
