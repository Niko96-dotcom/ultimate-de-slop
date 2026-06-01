#!/usr/bin/env python3
"""Print ultimate-de-slop status."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any


NON_OPEN = {"verified", "rejected", "false_positive"}


def fail(message: str) -> None:
    print(f"deslop-status: error: {message}", file=sys.stderr)
    raise SystemExit(1)


def repo_root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=Path.cwd(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        fail("could not resolve git root; run from inside a git repository")
    return Path(result.stdout.strip()).resolve()


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        fail(f"invalid JSON in {path}: {exc}")


def read_findings(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    findings = []
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            fail(f"invalid JSONL in {path}:{number}: {exc}")
        if isinstance(item, dict):
            findings.append(item)
    return findings


def choose_next(findings: list[dict[str, Any]]) -> str | None:
    priorities = ["P0", "P1", "P2"]
    effort_rank = {"small": 0, "medium": 1, "large": 2}
    eligible = [
        item
        for item in findings
        if item.get("status") == "accepted" and str(item.get("severity", "")).upper() in priorities
    ]
    if not eligible:
        return None
    eligible.sort(
        key=lambda item: (
            priorities.index(str(item.get("severity", "")).upper()),
            -float(item.get("confidence", 0) or 0),
            effort_rank.get(str(item.get("estimated_effort", "")).lower(), 9),
            str(item.get("id")),
        )
    )
    return str(eligible[0].get("id"))


def score(root: Path, findings: list[dict[str, Any]]) -> int:
    inventory = load_json(root / ".deslop" / "inventory.json", {})
    open_counts = Counter(
        str(item.get("severity", "unknown")).upper()
        for item in findings
        if str(item.get("status", "")) not in NON_OPEN
    )
    penalty = (
        25 * open_counts.get("P0", 0)
        + 10 * open_counts.get("P1", 0)
        + 3 * open_counts.get("P2", 0)
        + 2 * len(inventory.get("files_over_1000_lines", []) or [])
        + min(25, len(inventory.get("files_over_500_lines", []) or []))
    )
    return max(0, 100 - penalty)


def last_runs(root: Path) -> list[str]:
    runs = root / ".deslop" / "runs"
    if not runs.exists():
        return []
    return [path.name for path in sorted((p for p in runs.iterdir() if p.is_dir()), key=lambda p: p.name, reverse=True)[:5]]


def build_status(root: Path) -> dict[str, Any]:
    findings = read_findings(root / ".deslop" / "findings.jsonl")
    by_status = Counter(str(item.get("status", "unknown")) for item in findings)
    by_severity = Counter(str(item.get("severity", "unknown")).upper() for item in findings)
    next_id = choose_next(findings)
    stop_file = root / ".deslop" / "stop"
    return {
        "score": score(root, findings),
        "counts_by_status": dict(sorted(by_status.items())),
        "counts_by_severity": dict(sorted(by_severity.items())),
        "next": next_id,
        "last_runs": last_runs(root),
        "stop_file": {"present": stop_file.exists(), "path": ".deslop/stop"},
        "suggested_commands": suggested(next_id),
    }


def suggested(next_id: str | None) -> list[str]:
    if next_id:
        return [
            "scripts/deslop-loop.sh --max-iterations 5 --priority P0,P1",
            f"scripts/deslop-fix.sh {next_id}",
            f"scripts/deslop-run-checks.sh {next_id}",
            f"scripts/deslop-verify.sh {next_id}",
            f"scripts/deslop-finalize.py {next_id}",
        ]
    return [
        "scripts/deslop-loop.sh --max-iterations 5 --priority P0,P1",
        "scripts/deslop-review.sh",
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Print ultimate-de-slop status.")
    parser.add_argument("--json", action="store_true", help="print machine-readable status")
    args = parser.parse_args()
    root = repo_root()
    status = build_status(root)
    if args.json:
        print(json.dumps(status, indent=2, sort_keys=True))
    else:
        print("Ultimate De-Slop Status")
        print(f"Score: {status['score']}")
        print(f"Findings by status: {status['counts_by_status']}")
        print(f"Findings by severity: {status['counts_by_severity']}")
        print(f"Next: {status['next'] or 'NONE'}")
        print(f"Stop file: {'present' if status['stop_file']['present'] else 'absent'}")
        if status["last_runs"]:
            print(f"Last runs: {', '.join(status['last_runs'])}")
        print("Suggested commands:")
        for command in status["suggested_commands"]:
            print(f"  {command}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
