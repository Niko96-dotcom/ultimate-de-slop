#!/usr/bin/env python3
"""Shared FINDING_ID validation and safe run directory resolution."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

FINDING_ID_RE = re.compile(r"^DSL-\d{6}$")


def finding_id_error(finding_id: str) -> str | None:
    if not finding_id:
        return "finding id is required"
    if "/" in finding_id or "\\" in finding_id or ".." in finding_id:
        return f"invalid finding id: {finding_id!r}"
    if not FINDING_ID_RE.fullmatch(finding_id):
        return f"invalid finding id: {finding_id!r} (expected DSL-NNNNNN)"
    return None


def resolve_run_dir(root: Path, kind: str, finding_id: str, timestamp: str) -> Path:
    err = finding_id_error(finding_id)
    if err:
        raise ValueError(err)
    runs_root = (root / ".deslop" / "runs").resolve()
    run_dir = (runs_root / f"{timestamp}-{kind}-{finding_id}").resolve()
    try:
        run_dir.relative_to(runs_root)
    except ValueError as exc:
        raise ValueError(f"run directory resolves outside .deslop/runs: {run_dir}") from exc
    return run_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate")
    validate.add_argument("finding_id")
    validate.add_argument("--prefix", default="deslop")

    run_dir_cmd = sub.add_parser("run-dir")
    run_dir_cmd.add_argument("root")
    run_dir_cmd.add_argument("kind")
    run_dir_cmd.add_argument("finding_id")
    run_dir_cmd.add_argument("timestamp")

    args = parser.parse_args(argv)

    if args.command == "validate":
        err = finding_id_error(args.finding_id)
        if err:
            print(f"{args.prefix}: error: {err}", file=sys.stderr)
            return 1
        return 0

    if args.command == "run-dir":
        try:
            path = resolve_run_dir(Path(args.root), args.kind, args.finding_id, args.timestamp)
        except ValueError as exc:
            print(f"deslop: error: {exc}", file=sys.stderr)
            return 1
        print(path)
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
