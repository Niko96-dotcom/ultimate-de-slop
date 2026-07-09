#!/usr/bin/env python3
"""Diagnose Ultimate De-Slop harness readiness before a loop."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from deslop_harness import resolve_harness, resolve_agent_timeouts
from deslop_oauth import auth_status, resolve_model


HARNESS_CLI = {
    "claude": "claude",
    "codex": "codex",
    "commandcode": "commandcode",
    "cursor": "cursor-agent",
    "hermes": "hermes",
    "opencode": "opencode",
    "openclaw": "openclaw",
    "pi": "pi",
}

def fail(message: str) -> None:
    print(f"deslop-doctor: error: {message}", file=sys.stderr)
    raise SystemExit(1)


def repo_root_optional() -> Path | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=Path.cwd(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip()).resolve()


def check_item(ok: bool, code: str, message: str, *, level: str | None = None) -> dict[str, Any]:
    resolved_level = level or ("ok" if ok else "error")
    return {"ok": ok, "code": code, "level": resolved_level, "message": message}


def build_report(harness: str | None = None) -> dict[str, Any]:
    harness_name = resolve_harness(
        explicit=harness,
        script_dir=Path(__file__).resolve().parent,
    )
    checks: list[dict[str, Any]] = []
    if harness_name not in HARNESS_CLI:
        checks.append(
            check_item(
                False,
                "unknown_harness",
                f"Unsupported DESLOP_HARNESS={harness_name!r}; expected one of: {', '.join(sorted(HARNESS_CLI))}",
            )
        )
        return {
            "harness": harness_name,
            "model": resolve_model(harness_name),
            "ready": False,
            "checks": checks,
        }

    cli = HARNESS_CLI[harness_name]
    cli_path = shutil.which(cli)
    checks.append(
        check_item(
            cli_path is not None,
            "cli_on_path",
            f"{cli} found at {cli_path}" if cli_path else f"{cli} not found on PATH for DESLOP_HARNESS={harness_name}",
        )
    )

    if harness_name == "openclaw":
        checks.append(
            check_item(
                False,
                "openclaw_unsupported",
                "OpenClaw adapter intentionally fails until its noninteractive schema-output CLI contract is confirmed.",
            )
        )

    auth = auth_status(harness_name, cli)
    if auth["mode"] != "none":
        checks.append(
            check_item(
                bool(auth["ok"]),
                "auth",
                str(auth["message"]),
            )
        )

    model = resolve_model(harness_name)
    if model:
        model_message = f"Model: {model}"
        if not os.environ.get("DESLOP_MODEL"):
            model_message += " (OAuth/session default; set DESLOP_MODEL to override)"
    else:
        model_message = "Model: harness CLI default (set DESLOP_MODEL to pin one)"
    checks.append(
        check_item(
            True,
            "model",
            model_message,
            level="ok",
        )
    )

    root = repo_root_optional()
    if root is None:
        checks.append(
            check_item(
                False,
                "git_repo",
                "Not inside a git repository; deslop commands require a git root.",
                level="warning",
            )
        )
    else:
        deslop = root / ".deslop"
        required = ["config.json", "state.json", "findings.jsonl", "inventory.json", "index.md"]
        missing = [name for name in required if not (deslop / name).exists()]
        if missing:
            checks.append(
                check_item(
                    False,
                    "deslop_init",
                    f".deslop is incomplete (missing {', '.join(missing)}). Run scripts/deslop-init.sh.",
                    level="warning",
                )
            )
        else:
            checks.append(
                check_item(
                    True,
                    "deslop_init",
                    f".deslop initialized under {deslop}",
                )
            )
            timeouts = resolve_agent_timeouts(
                root,
                harness=harness_name,
                kind="fix",
                script_dir=Path(__file__).resolve().parent,
            )
            idle = int(timeouts["idle_timeout_seconds"])
            idle_note = "disabled (wall only)" if idle == 0 else f"{idle}s no-stdout idle cap"
            checks.append(
                check_item(
                    True,
                    "agent_timeouts",
                    (
                        f"Fix runs on {harness_name}: wall={int(timeouts['timeout_seconds'])}s, "
                        f"idle={idle_note} [{timeouts.get('idle_timeout_source')}]. "
                        "Buffering harnesses (cursor, claude, opencode, …) disable idle by default. "
                        "Override with DESLOP_IDLE_TIMEOUT_SECONDS or --agent-idle-timeout-seconds."
                    ),
                    level="ok",
                )
            )

    ready = all(item["ok"] or item["level"] == "warning" for item in checks) and all(
        item["level"] != "error" for item in checks
    )
    # ready means no error-level failures
    ready = not any(item["level"] == "error" for item in checks)
    return {
        "checks": checks,
        "harness": harness_name,
        "model": model,
        "ready": ready,
        "repo_root": str(root) if root else None,
        "suggested_commands": suggested(ready, harness_name, cli),
    }


def suggested(ready: bool, harness: str, cli: str) -> list[str]:
    if not ready:
        return [
            "Fix the error checks above, then re-run scripts/deslop-doctor.py",
            f"export DESLOP_HARNESS={harness}",
            f"`{cli} login` for OAuth auth; DESLOP_MODEL only if you need to override the session default",
        ]
    return [
        "scripts/deslop-init.sh",
        "scripts/deslop-status.py",
        "scripts/deslop-continue.sh",
    ]


def print_human(report: dict[str, Any]) -> None:
    print("Ultimate De-Slop Doctor")
    print(f"Harness: {report['harness']}")
    print(f"Model: {report.get('model') or 'default'}")
    print(f"Ready: {'yes' if report['ready'] else 'no'}")
    if report.get("repo_root"):
        print(f"Repo: {report['repo_root']}")
    print("Checks:")
    for item in report["checks"]:
        mark = "OK" if item["ok"] else item["level"].upper()
        print(f"  [{mark}] {item['code']}: {item['message']}")
    print("Suggested commands:")
    for command in report["suggested_commands"]:
        print(f"  {command}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose Ultimate De-Slop harness readiness.")
    parser.add_argument("--harness", help="override DESLOP_HARNESS")
    parser.add_argument("--json", action="store_true", help="print machine-readable report")
    args = parser.parse_args()
    report = build_report(args.harness)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_human(report)
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
