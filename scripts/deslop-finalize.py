#!/usr/bin/env python3
"""Finalize an ultimate-de-slop finding after checks and verification."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


NON_OPEN = {"verified", "rejected", "false_positive"}


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def fail(message: str) -> None:
    print(f"deslop-finalize: error: {message}", file=sys.stderr)
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


def run_git(root: Path, args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode != 0:
        fail(f"git {' '.join(args)} failed:\n{result.stderr.strip()}")
    return result


def load_json(path: Path, fallback: Any = None) -> Any:
    if not path or not path.exists():
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


def write_findings(path: Path, findings: list[dict[str, Any]]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("".join(json.dumps(item, sort_keys=True) + "\n" for item in findings))
    tmp.replace(path)


def load_config(root: Path) -> dict[str, Any]:
    data = load_json(root / ".deslop" / "config.json", {})
    return data if isinstance(data, dict) else {}


def find_latest(root: Path, suffix: str, finding_id: str) -> Path | None:
    runs = root / ".deslop" / "runs"
    if not runs.exists():
        return None
    matches = sorted(runs.glob(f"*-{suffix}-{finding_id}/{suffix}.json"), key=lambda p: p.as_posix(), reverse=True)
    return matches[0] if matches else None


from deslop_verification_policy import checks_failed, is_docs_only_exception, is_docs_only_path, proof_matches


def summarize(findings: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "total": len(findings),
        "by_status": dict(sorted(Counter(str(item.get("status", "unknown")) for item in findings).items())),
        "open_by_severity": dict(
            sorted(
                Counter(
                    str(item.get("severity", "unknown")).upper()
                    for item in findings
                    if str(item.get("status", "")) not in NON_OPEN
                ).items()
            )
        ),
    }


def update_state(root: Path, config: dict[str, Any], findings: list[dict[str, Any]], last_run: dict[str, Any]) -> None:
    path = root / ".deslop" / "state.json"
    timestamp = now()
    state = load_json(path, None)
    if not isinstance(state, dict):
        state = {
            "version": 1,
            "repo_root": str(root),
            "created_at": timestamp,
            "updated_at": timestamp,
            "config": config,
            "counters": {},
            "current_iteration": 0,
            "stop": {"requested": False, "reason": None, "path": ".deslop/stop"},
            "last_run": None,
            "open_findings_summary": {},
        }
    state["updated_at"] = timestamp
    state["repo_root"] = str(root)
    state["config"] = config
    state["counters"] = summarize(findings)
    state["open_findings_summary"] = state["counters"].get("open_by_severity", {})
    state["stop"] = {
        "requested": (root / ".deslop" / "stop").exists(),
        "reason": "stop file exists" if (root / ".deslop" / "stop").exists() else None,
        "path": ".deslop/stop",
    }
    state["last_run"] = last_run
    temporary_state = path.with_suffix(".json.tmp")
    temporary_state.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    temporary_state.replace(path)


def mutation_paths(root: Path, finding: dict[str, Any]) -> list[str]:
    from deslop_verification_policy import finding_changed_files
    paths = finding_changed_files(finding)
    before = (finding.get("last_fix") or {}).get("snapshot_paths", {}).get("status_before")
    if not before or not Path(before).is_file():
        fail("commit/revert requires the original clean-worktree snapshot")
    baseline = Path(before).read_text().splitlines()
    if any(line and not line[3:].strip('"').startswith(".deslop/") for line in baseline):
        fail("commit/revert refused: this attempt started with pre-existing changes")
    if not paths or any(Path(p).is_absolute() or ".." in Path(p).parts or p.startswith(".deslop/") for p in paths):
        fail("commit/revert requires bounded actual changed paths")
    return paths


def tracked_restore(root: Path, finding: dict[str, Any]) -> None:
    paths = mutation_paths(root, finding)
    tracked = [p for p in paths if run_git(root, ["ls-files", "--error-unmatch", "--", p], check=False).returncode == 0]
    if tracked:
        run_git(root, ["restore", "--source=HEAD", "--staged", "--worktree", "--", *tracked])
    if len(tracked) != len(paths):
        fail("tracked changes restored; newly created files retained for explicit recovery")


def commit_fix(root: Path, finding: dict[str, Any], verify: dict[str, Any] | None, checks: dict[str, Any] | None) -> None:
    status = run_git(root, ["status", "--porcelain"], check=True).stdout.strip()
    if not status:
        print("No git changes to commit.")
        return
    paths = mutation_paths(root, finding)
    run_git(root, ["add", "--", *paths], check=True)
    title = str(finding.get("title", "")).strip()
    short_title = title[:80] if title else "verified finding"
    body = [
        f"Finding: {finding.get('id')}",
        "",
        str(finding.get("why_it_matters", "")).strip(),
        "",
        "Verification:",
        json.dumps(verify or {}, indent=2, sort_keys=True),
        "",
        "Checks:",
        json.dumps(checks or {}, indent=2, sort_keys=True),
    ]
    run_git(root, ["commit", "-m", f"deslop: fix {finding.get('id')} {short_title}", "-m", "\n".join(body)], check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Finalize an ultimate-de-slop finding.")
    parser.add_argument("finding_id", help="finding ID such as DSL-000001")
    parser.add_argument("--verify-json", type=Path, help="path to verify.json")
    parser.add_argument("--checks-json", type=Path, help="path to checks.json")
    parser.add_argument("--commit", action="store_true", help="commit verified changes")
    parser.add_argument("--auto-revert", action="store_true", help="revert tracked changes after failed attempts")
    args = parser.parse_args()

    root = repo_root()
    config = load_config(root)
    findings_path = root / ".deslop" / "findings.jsonl"
    findings = read_findings(findings_path)
    finding = next((item for item in findings if item.get("id") == args.finding_id), None)
    if not finding:
        fail(f"finding not found: {args.finding_id}")

    verify_path = args.verify_json or find_latest(root, "verify", args.finding_id)
    checks_path = args.checks_json or find_latest(root, "checks", args.finding_id)
    verify = load_json(verify_path, None) if verify_path else None
    checks = load_json(checks_path, None) if checks_path else None
    if not isinstance(verify, dict) or verify.get("finding_id") != args.finding_id:
        fail("verification finding_id mismatch or missing verification")
    if not isinstance(verify.get("evidence"), list) or not any(str(v).strip() for v in verify["evidence"]):
        fail("verification evidence is missing")
    if not proof_matches(verify, finding, root) or not proof_matches(checks, finding, root):
        fail("stale or mismatched attempt evidence; rerun checks and verification")
    bad_checks = checks_failed(checks, finding_id=args.finding_id, finding=finding, verify=verify)
    verdict = str((verify or {}).get("verdict", "")).upper() if verify else ""
    timestamp = now()
    attempts = int(finding.get("attempts", 0) or 0)
    max_attempts = int(config.get("max_fix_attempts", 3) or 3)
    exit_code = 0

    if verdict == "NEEDS_HUMAN":
        finding["status"] = "needs_human"
        finding["updated_at"] = timestamp
        finding["verification"] = verify
        exit_code = 1
    elif verdict == "FALSE_POSITIVE":
        changed = bool((finding.get("last_fix") or {}).get("changed_during_attempt"))
        finding["status"] = "needs_human" if changed else "false_positive"
        finding["updated_at"] = timestamp
        finding["verification"] = verify
        if changed:
            finding["block_reason"] = "false-positive verdict left an unverified patch; inspect before recovery"
            exit_code = 1
    elif bad_checks or verdict == "FAIL":
        attempts += 1
        finding["attempts"] = attempts
        finding["updated_at"] = timestamp
        finding["last_failure"] = {
            "at": timestamp,
            "checks_failed": bad_checks,
            "verdict": verdict or None,
            "verify_json": str(verify_path) if verify_path else None,
            "checks_json": str(checks_path) if checks_path else None,
        }
        finding["status"] = "blocked" if attempts >= max_attempts else "accepted"
        if args.auto_revert:
            tracked_restore(root, finding)
        exit_code = 1
    elif verdict == "PASS":
        if finding.get("status") != "fixed_unverified":
            fail("PASS requires a fixed_unverified finding")
        finding["status"] = "verified"
        finding["updated_at"] = timestamp
        finding["verified_at"] = timestamp
        finding["verification"] = verify
        if args.commit:
            commit_fix(root, finding, verify, checks)
    else:
        fail("verification verdict missing; pass --verify-json or run deslop-verify.sh first")

    write_findings(findings_path, findings)
    update_state(
        root,
        config,
        findings,
        {
            "kind": "finalize",
            "finding_id": args.finding_id,
            "at": timestamp,
            "verdict": verdict or None,
            "checks_failed": bad_checks,
            "status": finding.get("status"),
        },
    )
    print(f"Finding {args.finding_id}: {finding.get('status')}")
    if bad_checks:
        print(f"Checks failed: {checks_path}")
    if verdict:
        print(f"Verifier verdict: {verdict}")
    if isinstance(verify, dict):
        for label, key in (
            ("Evidence", "evidence"),
            ("Concerns", "concerns"),
            ("Required follow-up", "required_follow_up"),
        ):
            values = [str(item).strip() for item in (verify.get(key) or []) if str(item).strip()]
            if not values:
                continue
            print(f"{label}:")
            for value in values:
                print(f"  - {value}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
