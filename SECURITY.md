# Security Policy

Ultimate De-Slop runs local commands and external agent CLIs against user
repositories, so hardening and safety reports are welcome.

## Supported Versions

| Version | Supported |
| --- | --- |
| `main` | yes |
| tagged releases | best effort |

## Reporting a Vulnerability

For sensitive reports, use a private security advisory for this
repository if available:

<https://github.com/Niko96-dotcom/ultimate-de-slop/security/advisories/new>

Do not open a public issue for a sensitive vulnerability, and do not
post private repository contents, API keys, tokens, credentials, raw
agent transcripts, or proprietary code. A public issue is only for
non-sensitive hardening questions, with all excerpts sanitized.

## Intended permissions versus isolation

The documented roles describe intended permissions:

- Review, arbitration, and verification are intended to be read-only.
- Fixing is intended to be workspace-write scoped to one accepted finding.
- Finalize writes `.deslop/` state and optionally commits only with
  opt-in flags.

These intentions are harness conventions. They are not OS-level
sandboxing, container isolation, or privilege separation. Assume agent
and check processes run with your local user privileges.

## External execution

- Each agent stage shells out to an external agent CLI selected via
  `DESLOP_HARNESS` (default `codex`). That CLI and any model behind it
  execute with your credentials, network access, and filesystem access.
- Check commands are filtered before execution
  (see `scripts/deslop_check_safety.py`), but filtering is an allowlist
  heuristic, not a sandbox. Review expected checks from
  `scripts/deslop-inventory.py` and `.deslop/findings.jsonl` before
  approving a run, and run untrusted repositories in a disposable
  checkout or VM you control.

## Sensitive artifacts

- `.deslop/runs/` holds prompts, raw agent output, extracted JSON, and
  check logs. `.deslop/state.json`, `.deslop/findings.jsonl`, and
  `runner.json` records may contain local paths, command output, or
  repository excerpts. Review and redact before sharing.
- Runtime artifacts are gitignored in this repository, but they appear
  in target repositories unless that repo ignores `.deslop/`.
