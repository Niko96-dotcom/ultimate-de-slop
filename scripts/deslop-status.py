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


def finding_details(item: dict[str, Any]) -> list[str]:
    verification = item.get("verification") if isinstance(item.get("verification"), dict) else {}
    details: list[str] = []
    for key in ("concerns", "required_follow_up", "evidence"):
        values = verification.get(key) or []
        if isinstance(values, list):
            details.extend(str(value).strip() for value in values if str(value).strip())
        elif str(values).strip():
            details.append(str(values).strip())
    block_reason = str(item.get("block_reason", "")).strip()
    if block_reason:
        details.append(block_reason)
    return details


def summarize_findings(findings: list[dict[str, Any]], status: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in findings:
        if item.get("status") != status:
            continue
        rows.append(
            {
                "details": finding_details(item),
                "id": item.get("id"),
                "title": item.get("title"),
            }
        )
    rows.sort(key=lambda row: str(row.get("id") or ""))
    return rows


def latest_runner_diagnostic(root: Path) -> dict[str, Any] | None:
    runs = root / ".deslop" / "runs"
    if not runs.exists():
        return None
    runners = sorted(runs.glob("*/runner.json"), key=lambda path: path.as_posix(), reverse=True)
    for path in runners[:20]:
        data = load_json(path, None)
        if not isinstance(data, dict):
            continue
        status = str(data.get("status", ""))
        if status.endswith("_unsupported") or status.endswith("_not_found"):
            return {
                "path": str(path.relative_to(root)) if path.is_relative_to(root) else str(path),
                "status": status,
                "unsupported_reason": data.get("unsupported_reason"),
                "harness": data.get("harness"),
            }
    return None


def build_loop_summary(
    root: Path,
    findings: list[dict[str, Any]],
    state: dict[str, Any],
) -> dict[str, Any]:
    outcome = state.get("loop_outcome") if isinstance(state.get("loop_outcome"), dict) else {}
    stop = state.get("stop") if isinstance(state.get("stop"), dict) else {}
    verified_ids = [str(item) for item in (outcome.get("verified_ids") or []) if str(item).strip()]
    by_id = {str(item.get("id")): item for item in findings}
    verified_rows = []
    for finding_id in verified_ids:
        item = by_id.get(finding_id, {})
        verified_rows.append({"id": finding_id, "title": item.get("title")})
    if not verified_rows and not outcome:
        verified_rows = [
            {"id": item.get("id"), "title": item.get("title")}
            for item in findings
            if item.get("status") == "verified"
        ]

    stop_reason = outcome.get("stop_reason") or stop.get("reason")
    return {
        "false_positives": summarize_findings(findings, "false_positive"),
        "halt_finding_id": outcome.get("halt_finding_id"),
        "halt_status": outcome.get("halt_status"),
        "iterations_completed": outcome.get("iterations_completed"),
        "max_iterations": outcome.get("max_iterations"),
        "needs_human": summarize_findings(findings, "needs_human"),
        "next": choose_next(findings),
        "priority": outcome.get("priority"),
        "runner_diagnostic": latest_runner_diagnostic(root),
        "stop_reason": stop_reason,
        "verified": verified_rows,
    }


def build_status(root: Path) -> dict[str, Any]:
    findings = read_findings(root / ".deslop" / "findings.jsonl")
    state = load_json(root / ".deslop" / "state.json", {})
    if not isinstance(state, dict):
        state = {}
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
        "loop_summary": build_loop_summary(root, findings, state),
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


def print_loop_summary(summary: dict[str, Any]) -> None:
    print("Loop outcome")
    print(f"  Stop reason: {summary.get('stop_reason') or 'NONE'}")
    verified = summary.get("verified") or []
    if verified:
        print("  Verified this run:")
        for item in verified:
            title = item.get("title") or ""
            suffix = f" {title}" if title else ""
            print(f"    - {item.get('id')}{suffix}")
    else:
        print("  Verified this run: NONE")
    print(f"  Queued next: {summary.get('next') or 'NONE'}")
    needs_human = summary.get("needs_human") or []
    if needs_human:
        print("  Needs human:")
        for item in needs_human:
            title = item.get("title") or ""
            suffix = f" {title}" if title else ""
            print(f"    - {item.get('id')}{suffix}")
            for detail in item.get("details") or []:
                print(f"      {detail}")
    false_positives = summary.get("false_positives") or []
    if false_positives:
        print("  False positives:")
        for item in false_positives:
            title = item.get("title") or ""
            suffix = f" {title}" if title else ""
            print(f"    - {item.get('id')}{suffix}")
            for detail in item.get("details") or []:
                print(f"      {detail}")
    diagnostic = summary.get("runner_diagnostic")
    if isinstance(diagnostic, dict) and diagnostic.get("status"):
        reason = diagnostic.get("unsupported_reason") or diagnostic.get("status")
        harness = diagnostic.get("harness") or "harness"
        print(f"  Runner diagnostic: {harness} {diagnostic.get('status')} ({reason})")


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
        print_loop_summary(status["loop_summary"])
        print("Suggested commands:")
        for command in status["suggested_commands"]:
            print(f"  {command}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
