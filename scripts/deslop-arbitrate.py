#!/usr/bin/env python3
"""Conservatively arbitrate ultimate-de-slop review findings."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_CONFIG = {
    "version": 1,
    "max_iterations": 5,
    "max_fix_attempts": 3,
    "max_findings_per_reviewer": 5,
    "max_active_findings": 10,
    "max_changed_files_per_fix": 8,
    "max_changed_lines_per_fix": 400,
    "confidence_thresholds": {"P0": 0.70, "P1": 0.75, "P2": 0.85},
    "ignored_paths": ["node_modules", ".git", "dist", "build", "coverage", "vendor", ".next", ".turbo", ".venv", "__pycache__"],
    "commit_by_default": False,
    "auto_revert_by_default": False,
}

FINAL_STATES = {"verified", "blocked", "false_positive", "needs_human"}
NON_OPEN_STATES = {"verified", "rejected", "false_positive"}
STYLE_CATEGORIES = {"style", "format", "formatting", "whitespace", "naming", "comment", "comments", "cosmetic", "nit"}


@dataclass(frozen=True)
class Rejection:
    title: str
    reasons: list[str]
    severity: str | None
    confidence: float | None


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def fail(message: str) -> None:
    print(f"deslop-arbitrate: error: {message}", file=sys.stderr)
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


def load_json(path: Path, fallback: Any = None) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        fail(f"invalid JSON in {path}: {exc}")


def load_config(root: Path) -> dict[str, Any]:
    config = DEFAULT_CONFIG.copy()
    config["confidence_thresholds"] = DEFAULT_CONFIG["confidence_thresholds"].copy()
    path = root / ".deslop" / "config.json"
    data = load_json(path, {})
    if isinstance(data, dict):
        for key, value in data.items():
            if key == "confidence_thresholds" and isinstance(value, dict):
                config["confidence_thresholds"].update(value)
            else:
                config[key] = value
    return config


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
    path.parent.mkdir(exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("".join(json.dumps(item, sort_keys=True) + "\n" for item in findings))
    tmp.replace(path)


def normalize(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def finding_key(finding: dict[str, Any]) -> str:
    category = normalize(finding.get("category"))
    title = normalize(finding.get("title"))
    files = "|".join(sorted(str(item) for item in finding.get("files", []) if item))
    claims = []
    for evidence in finding.get("evidence", []) or []:
        if isinstance(evidence, dict):
            claims.append(normalize(evidence.get("claim", "")))
        else:
            claims.append(normalize(evidence))
    return "||".join([category, title, files, "|".join(sorted(claims))])


def next_id(findings: list[dict[str, Any]]) -> str:
    max_seen = 0
    for finding in findings:
        match = re.match(r"^DSL-(\d{6})$", str(finding.get("id", "")))
        if match:
            max_seen = max(max_seen, int(match.group(1)))
    return f"DSL-{max_seen + 1:06d}"


def active_count(findings: list[dict[str, Any]]) -> int:
    return sum(1 for item in findings if item.get("status") in {"accepted", "fixing", "fixed_unverified"})


def validate_candidate(candidate: dict[str, Any], config: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    severity = str(candidate.get("severity", "")).upper()
    try:
        confidence = float(candidate.get("confidence", -1))
    except (TypeError, ValueError):
        confidence = -1
    thresholds = config.get("confidence_thresholds", {})
    if severity == "P3":
        reasons.append("P3 is not loop fuel")
    if severity not in {"P0", "P1", "P2"}:
        reasons.append("severity must be P0, P1, or P2")
    elif confidence < float(thresholds.get(severity, 1.0)):
        reasons.append(f"confidence below {severity} threshold")

    category_words = set(normalize(candidate.get("category")).split())
    if category_words & STYLE_CATEGORIES:
        reasons.append("style or nit category")
    evidence = candidate.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        reasons.append("missing evidence")
    else:
        useful_evidence = False
        for item in evidence:
            if isinstance(item, dict) and item.get("file") and item.get("claim"):
                useful_evidence = True
                break
        if not useful_evidence:
            reasons.append("evidence lacks file and claim")
    if not candidate.get("files"):
        reasons.append("missing files")
    if not str(candidate.get("why_it_matters", "")).strip():
        reasons.append("missing why_it_matters")
    proposed = normalize(candidate.get("proposed_fix"))
    if len(proposed) < 8 or proposed in {"clean up", "refactor", "make cleaner", "improve code"}:
        reasons.append("vague or missing proposed_fix")
    criteria = candidate.get("acceptance_criteria")
    if not isinstance(criteria, list) or not any(str(item).strip() for item in criteria):
        reasons.append("missing acceptance_criteria")
    checks = candidate.get("expected_checks")
    if checks is None or not isinstance(checks, list):
        reasons.append("expected_checks must be a list")
    elif not checks and not any(
        str(candidate.get(key, "")).strip()
        for key in ("expected_checks_explanation", "no_expected_checks_reason", "checks_explanation")
    ):
        reasons.append("empty expected_checks needs an explanation")
    if severity == "P2" and str(candidate.get("risk", "")).lower() == "high":
        reasons.append("P2 must be bounded with low or regression-controlled risk")
    if str(candidate.get("estimated_effort", "")).lower() not in {"small", "medium", "large"}:
        reasons.append("estimated_effort must be small, medium, or large")
    if str(candidate.get("risk", "")).lower() not in {"low", "medium", "high"}:
        reasons.append("risk must be low, medium, or high")
    return reasons


def summarize(findings: list[dict[str, Any]]) -> dict[str, Any]:
    by_status = Counter(str(item.get("status", "unknown")) for item in findings)
    by_severity = Counter(str(item.get("severity", "unknown")) for item in findings)
    open_by_severity = Counter(
        str(item.get("severity", "unknown")) for item in findings if str(item.get("status")) not in NON_OPEN_STATES
    )
    return {
        "total": len(findings),
        "by_status": dict(sorted(by_status.items())),
        "by_severity": dict(sorted(by_severity.items())),
        "open_by_severity": dict(sorted(open_by_severity.items())),
    }


def choose_next(findings: list[dict[str, Any]], priorities: tuple[str, ...] = ("P0", "P1", "P2")) -> str | None:
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


def load_state(root: Path, config: dict[str, Any], findings: list[dict[str, Any]]) -> dict[str, Any]:
    state_path = root / ".deslop" / "state.json"
    timestamp = now()
    state = load_json(state_path, None)
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
    state["repo_root"] = str(root)
    state["updated_at"] = timestamp
    state["config"] = config
    state["counters"] = summarize(findings)
    state["open_findings_summary"] = summarize(findings).get("open_by_severity", {})
    state["stop"] = {
        "requested": (root / ".deslop" / "stop").exists(),
        "reason": "stop file exists" if (root / ".deslop" / "stop").exists() else None,
        "path": ".deslop/stop",
    }
    return state


def write_state(root: Path, state: dict[str, Any]) -> None:
    path = root / ".deslop" / "state.json"
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def arbitrate(root: Path, review_path: Path, run_dir: Path | None, json_output: bool) -> dict[str, Any]:
    config = load_config(root)
    review = load_json(review_path)
    if not isinstance(review, dict):
        fail(f"{review_path} must contain a JSON object")
    required_review_keys = {"repo_summary", "review_wave_id", "partitions_reviewed", "findings"}
    missing_review_keys = sorted(required_review_keys - set(review))
    if missing_review_keys:
        fail(f"{review_path} is not a review output; missing keys: {', '.join(missing_review_keys)}")
    candidates = review.get("findings", [])
    if not isinstance(candidates, list):
        fail("review JSON field 'findings' must be a list")

    findings_path = root / ".deslop" / "findings.jsonl"
    findings = read_findings(findings_path)
    by_key = {finding_key(item): item for item in findings if finding_key(item)}
    accepted: list[str] = []
    rejected: list[dict[str, Any]] = []
    merged: list[dict[str, Any]] = []
    timestamp = now()

    for raw in candidates:
        if not isinstance(raw, dict):
            rejected.append({"title": "<non-object>", "reasons": ["finding is not an object"]})
            continue
        candidate = dict(raw)
        candidate["severity"] = str(candidate.get("severity", "")).upper()
        key = finding_key(candidate)
        existing = by_key.get(key)
        if existing:
            merged.append({"into": existing.get("id"), "title": candidate.get("title"), "reason": "duplicate dedupe key"})
            if existing.get("status") not in FINAL_STATES:
                existing["confidence"] = max(float(existing.get("confidence", 0) or 0), float(candidate.get("confidence", 0) or 0))
                existing["updated_at"] = timestamp
            continue

        reasons = validate_candidate(candidate, config)
        if active_count(findings) >= int(config.get("max_active_findings", 10)) and not reasons:
            reasons.append("max_active_findings reached")
        new_id = next_id(findings)
        candidate["id"] = new_id
        candidate.setdefault("dependencies", [])
        candidate.setdefault("reviewer", "deslop-reviewer")
        candidate.setdefault("attempts", 0)
        candidate["created_at"] = candidate.get("created_at") or timestamp
        candidate["updated_at"] = timestamp
        if reasons:
            candidate["status"] = "rejected"
            candidate["rejection_reasons"] = reasons
            findings.append(candidate)
            rejected.append(
                {
                    "id": new_id,
                    "title": candidate.get("title"),
                    "severity": candidate.get("severity"),
                    "confidence": candidate.get("confidence"),
                    "reasons": reasons,
                }
            )
            by_key[key] = candidate
            continue
        candidate["status"] = "accepted"
        findings.append(candidate)
        by_key[key] = candidate
        accepted.append(new_id)

    next_finding = choose_next(findings)
    open_summary = summarize(findings).get("open_by_severity", {})
    stop_recommendation = None
    if next_finding is None:
        if not (open_summary.get("P0") or open_summary.get("P1") or open_summary.get("P2")):
            stop_recommendation = "no accepted findings remain"
        else:
            stop_recommendation = "no eligible accepted finding"

    result = {
        "accepted": accepted,
        "rejected": rejected,
        "merged": merged,
        "next": next_finding,
        "stop_recommendation": stop_recommendation,
    }

    root.joinpath(".deslop").mkdir(exist_ok=True)
    write_findings(findings_path, findings)
    state = load_state(root, config, findings)
    state["last_run"] = {"kind": "arbitrate", "at": timestamp, "review_path": str(review_path), "run_dir": str(run_dir) if run_dir else None}
    write_state(root, state)
    if run_dir:
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "arbiter.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    if json_output:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"Accepted: {len(accepted)}")
        print(f"Rejected: {len(rejected)}")
        print(f"Merged: {len(merged)}")
        print(f"Next: {next_finding or 'NONE'}")
        if stop_recommendation:
            print(f"Stop recommendation: {stop_recommendation}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Arbitrate ultimate-de-slop review findings.")
    parser.add_argument("review_json", type=Path, help="path to extracted review JSON")
    parser.add_argument("--run-dir", type=Path, help="run directory for arbiter.json")
    parser.add_argument("--json", action="store_true", help="print machine-readable arbiter output")
    args = parser.parse_args()
    root = repo_root()
    review_path = args.review_json if args.review_json.is_absolute() else Path.cwd() / args.review_json
    run_dir = None
    if args.run_dir:
        run_dir = args.run_dir if args.run_dir.is_absolute() else Path.cwd() / args.run_dir
    arbitrate(root, review_path, run_dir, args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
