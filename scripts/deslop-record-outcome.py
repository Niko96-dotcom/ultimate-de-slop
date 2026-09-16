#!/usr/bin/env python3
"""Record a bounded-loop outcome into .deslop/state.json."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


STOP_REASONS = {
    "stop_file",
    "no_eligible_findings",
    "max_iterations_reached",
    "finalize_halt",
    "until_clean",
    "max_review_calls_reached",
    "max_seconds_reached",
    "stage_failed",
    "needs_recovery",
}

GOAL_STATUS_FOR_STOP = {
    "until_clean": "complete",
    "no_eligible_findings": "complete",
    "max_iterations_reached": "exhausted",
    "max_review_calls_reached": "exhausted",
    "max_seconds_reached": "exhausted",
    "stop_file": "stopped",
    "finalize_halt": "failed",
    "stage_failed": "failed",
    "needs_recovery": "failed",
}


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def fail(message: str) -> None:
    print(f"deslop-record-outcome: error: {message}", file=sys.stderr)
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
    findings: list[dict[str, Any]] = []
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


def parse_id_list(raw: str | None) -> set[str]:
    if raw is None or not raw.strip():
        return set()
    return {part.strip() for part in raw.split(",") if part.strip()}


def validate_priorities(raw: str) -> list[str]:
    parts = [part.strip().upper() for part in str(raw).split(",") if part.strip()]
    if not parts:
        fail("priority must include at least one of P0,P1,P2")
    for part in parts:
        if part not in {"P0", "P1", "P2"}:
            fail(f"invalid priority {part!r}; expected subset of P0,P1,P2")
    return parts


def main() -> int:
    parser = argparse.ArgumentParser(description="Record ultimate-de-slop loop outcome.")
    parser.add_argument("--stop-reason", required=True, choices=sorted(STOP_REASONS))
    parser.add_argument("--max-iterations", type=int, required=True)
    parser.add_argument("--priority", required=True)
    parser.add_argument("--iterations-completed", type=int, required=True)
    parser.add_argument("--halt-finding-id")
    parser.add_argument("--halt-status")
    parser.add_argument(
        "--baseline-verified-ids",
        default="",
        help="comma-separated finding IDs already verified before this loop",
    )
    parser.add_argument("--max-review-calls", type=int, default=None)
    parser.add_argument("--review-calls-completed", type=int, default=None)
    parser.add_argument("--max-seconds", type=float, default=None)
    parser.add_argument("--elapsed-seconds", type=float, default=None)
    parser.add_argument("--goal-scope", default=None, help="scope for until-clean goals (until-clean)")
    args = parser.parse_args()

    if args.max_iterations < 0 or args.iterations_completed < 0:
        fail("iteration counts must be non-negative")
    if args.max_review_calls is not None and args.max_review_calls < 0:
        fail("review counts must be non-negative")
    if args.review_calls_completed is not None and args.review_calls_completed < 0:
        fail("review counts must be non-negative")
    for label, value in (("max_seconds", args.max_seconds), ("elapsed_seconds", args.elapsed_seconds)):
        if value is not None:
            try:
                number = float(value)
            except (TypeError, ValueError):
                fail(f"{label} must be a finite number")
            if not math.isfinite(number) or number < 0:
                fail(f"{label} must be a finite non-negative number")
    validate_priorities(args.priority)
    if args.goal_scope is not None and args.goal_scope not in ("until-clean", "bounded"):
        fail("goal scope must be until-clean or bounded")

    root = repo_root()
    state_path = root / ".deslop" / "state.json"
    state = load_json(state_path, None)
    if not isinstance(state, dict):
        fail(f"missing or invalid state at {state_path}; run deslop-init.sh first")

    findings = read_findings(root / ".deslop" / "findings.jsonl")
    baseline = parse_id_list(args.baseline_verified_ids)
    verified_ids = sorted(
        str(item.get("id"))
        for item in findings
        if item.get("status") == "verified" and str(item.get("id")) not in baseline
    )

    timestamp = now()
    outcome: dict[str, Any] = {
        "at": timestamp,
        "halt_finding_id": args.halt_finding_id,
        "halt_status": args.halt_status,
        "iterations_completed": args.iterations_completed,
        "max_iterations": args.max_iterations,
        "priority": args.priority,
        "stop_reason": args.stop_reason,
        "verified_ids": verified_ids,
    }
    if args.max_review_calls is not None:
        outcome["max_review_calls"] = args.max_review_calls
    if args.review_calls_completed is not None:
        outcome["review_calls_completed"] = args.review_calls_completed
    if args.max_seconds is not None:
        outcome["max_seconds"] = float(args.max_seconds)
    if args.elapsed_seconds is not None:
        outcome["elapsed_seconds"] = float(args.elapsed_seconds)
    if args.goal_scope is not None:
        outcome["goal_scope"] = args.goal_scope
    state["updated_at"] = timestamp
    state["loop_outcome"] = outcome
    stop = state.get("stop") if isinstance(state.get("stop"), dict) else {}
    stop_file = root / ".deslop" / "stop"
    state["stop"] = {
        "path": stop.get("path", ".deslop/stop"),
        "reason": args.stop_reason,
        "requested": stop_file.exists() or args.stop_reason == "stop_file",
    }
    # Scope/settings outcome must match the stored goal; refuse stale claims.
    goal = state.get("loop_goal")
    if isinstance(goal, dict) and goal.get("scope") == "until-clean":
        stored_priority = str(goal.get("priority") or "").strip()
        if stored_priority:
            wanted = ",".join(validate_priorities(args.priority))
            if wanted != stored_priority:
                fail(
                    f"outcome priority {wanted} does not match stored goal {stored_priority}; "
                    "use --new-goal to reset scope explicitly"
                )
        if args.goal_scope is not None and args.goal_scope != goal.get("scope"):
            fail(f"outcome scope {args.goal_scope} does not match stored goal {goal.get('scope')}")
        # Keep persistent goal truthful: mirror outcome reason into goal status.
        goal["status"] = GOAL_STATUS_FOR_STOP.get(args.stop_reason, "failed")
        goal["reason"] = args.stop_reason
        goal["updated_at"] = timestamp
        state["loop_goal"] = goal
    # Atomic state write.
    tmp = state_path.with_name(state_path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    tmp.replace(state_path)
    print(f"Recorded loop outcome: {args.stop_reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
