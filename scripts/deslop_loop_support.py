#!/usr/bin/env python3
"""Shared helpers for ultimate-de-slop bounded loop orchestration."""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import fcntl  # POSIX flock for crash-safe locking
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

from deslop_harness import CONFIG_DEFAULTS, ensure_config_defaults


DEFAULT_LOOP_PRIORITY = "P0,P1"
DEFAULT_MAX_ITERATIONS = 5
DEFAULT_REVIEW_EVERY = 1
DEFAULT_EMPTY_REVIEW_WAVES_REQUIRED = 2

DEFAULT_UNTIL_CLEAN_PRIORITY = "P0,P1,P2"
DEFAULT_UNTIL_CLEAN_MAX_FIX_ATTEMPTS = 100
DEFAULT_UNTIL_CLEAN_MAX_REVIEW_CALLS = 200
DEFAULT_UNTIL_CLEAN_MAX_SECONDS = 28800.0
UNTIL_CLEAN_REQUIRED_EMPTY_SWEEPS = 2

VALID_PRIORITIES = frozenset({"P0", "P1", "P2"})
UNRESOLVED_STATUSES = frozenset({"fixing", "fixed_unverified", "needs_human", "blocked"})
OPEN_STATUSES = frozenset({"accepted", "fixing", "fixed_unverified", "needs_human", "blocked"})
LOOP_LOCK_NAME = "loop.lock"
GOAL_SCOPE_UNTIL_CLEAN = "until-clean"

_LOCK_FD: int | None = None


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
        raise RuntimeError("could not resolve git root; run from inside a git repository")
    return Path(result.stdout.strip()).resolve()


def load_json(path: Path, fallback: Any = None) -> Any:
    if not path.exists():
        return fallback
    return json.loads(path.read_text())


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def load_config(root: Path) -> dict[str, Any]:
    config = load_json(root / ".deslop" / "config.json", {})
    if not isinstance(config, dict):
        config = {}
    ensure_config_defaults(config)
    return config


def save_config(root: Path, config: dict[str, Any]) -> None:
    ensure_config_defaults(config)
    write_json(root / ".deslop" / "config.json", config)


def load_state(root: Path) -> dict[str, Any]:
    state = load_json(root / ".deslop" / "state.json", {})
    return state if isinstance(state, dict) else {}


def save_state(root: Path, state: dict[str, Any]) -> None:
    write_json(root / ".deslop" / "state.json", state)


def read_findings(root: Path) -> list[dict[str, Any]]:
    path = root / ".deslop" / "findings.jsonl"
    if not path.exists():
        return []
    findings: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if isinstance(item, dict):
            findings.append(item)
    return findings


def write_findings_jsonl(root: Path, items: list[dict[str, Any]]) -> None:
    path = root / ".deslop" / "findings.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("".join(json.dumps(item, sort_keys=True) + "\n" for item in items))
    tmp.replace(path)


def _run_git_bytes(root: Path, args: list[str]) -> bytes:
    """Run git and fail closed: non-zero raises OSError."""
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="ignore").strip()
        raise OSError(f"git {' '.join(args)} failed: {detail or result.returncode}")
    return result.stdout


def _run_git_text(root: Path, args: list[str]) -> str:
    return _run_git_bytes(root, args).decode("utf-8", errors="ignore")


def git_porcelain(root: Path) -> str:
    # Fail closed: propagate OSError instead of returning "".
    return _run_git_text(root, ["status", "--porcelain"]).strip()


def _is_deslop_path(path_text: str) -> bool:
    cleaned = path_text.strip().strip('"')
    if not cleaned:
        return False
    # Handle rename "old -> new".
    if " -> " in cleaned:
        cleaned = cleaned.split(" -> ")[-1].strip().strip('"')
    return cleaned == ".deslop" or cleaned.startswith(".deslop/")


def _parse_porcelain_nul(raw: bytes) -> list[tuple[str, str]]:
    """Parse `git status --porcelain=v1 -z` into (xy, path) entries.

    NUL-separated; rename entries appear as two consecutive fields.
    """
    entries: list[tuple[str, str]] = []
    if not raw:
        return entries
    parts = raw.split(b"\0")
    idx = 0
    while idx < len(parts):
        field = parts[idx].decode("utf-8", errors="ignore")
        idx += 1
        if not field:
            continue
        if len(field) < 4:
            continue
        xy = field[:2]
        path = field[3:]
        # Rename/copy: next NUL field is the target path.
        if xy[0] in ("R", "C") and idx < len(parts):
            target = parts[idx].decode("utf-8", errors="ignore")
            idx += 1
            if target:
                path = target
        entries.append((xy, path))
    return entries


def filtered_git_porcelain(root: Path) -> str:
    """Filtered porcelain, fail closed on git errors (raises OSError)."""
    raw = _run_git_bytes(root, ["status", "--porcelain=v1", "-z", "-uall"])
    entries = _parse_porcelain_nul(raw)
    kept: list[str] = []
    for xy, path in entries:
        if _is_deslop_path(path):
            continue
        kept.append(f"{xy} {path}")
    return "\n".join(kept).strip()


def _git_diff_bytes(root: Path, args: list[str]) -> bytes:
    return _run_git_bytes(root, args)


def worktree_fingerprint(root: Path) -> str:
    """Exact content fingerprint: HEAD + tracked diffs + NUL-parsed untracked.

    Fail closed: any git/read error raises OSError so callers deny dirty
    and refuse clean instead of treating unreadable trees as owned-clean.
    """
    head = _run_git_text(root, ["rev-parse", "HEAD"]).strip()
    if not head:
        raise OSError("could not resolve HEAD")
    porcelain_raw = _run_git_bytes(root, ["status", "--porcelain=v1", "-z", "-uall"])
    entries = _parse_porcelain_nul(porcelain_raw)
    # Filtered porcelain text for stability (newline-joined, sorted).
    filtered_lines: list[str] = []
    untracked_paths: list[str] = []
    for xy, path in entries:
        if _is_deslop_path(path):
            continue
        filtered_lines.append(f"{xy} {path}")
        if xy == "??":
            untracked_paths.append(path)
    porcelain_text = "\n".join(sorted(filtered_lines))
    unstaged = _git_diff_bytes(root, ["diff", "--binary", "--", ".", ":!.deslop"])
    staged = _git_diff_bytes(root, ["diff", "--cached", "--binary", "--", ".", ":!.deslop"])
    untracked_hashes: list[str] = []
    for rel in sorted(untracked_paths):
        candidate = root / rel
        # Directories are already expanded with -uall; skip stray dirs.
        if candidate.is_dir():
            raise OSError(f"unexpected untracked directory entry: {rel}")
        try:
            digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
        except OSError as exc:
            raise OSError(f"could not read untracked file {rel}: {exc}")
        untracked_hashes.append(f"{rel}:{digest}")
    base = "\n".join([head, porcelain_text, unstaged.decode("utf-8", errors="ignore"), staged.decode("utf-8", errors="ignore"), "\n".join(sorted(untracked_hashes))])
    return hashlib.sha256(base.encode("utf-8", errors="ignore")).hexdigest()


def content_fingerprint(root: Path) -> str:
    """Alias for worktree_fingerprint (includes HEAD)."""
    return worktree_fingerprint(root)


def load_dirty_baseline(state: dict[str, Any]) -> str | None:
    baseline = state.get("loop_dirty_baseline")
    if isinstance(baseline, dict):
        fingerprint = baseline.get("fingerprint")
        if isinstance(fingerprint, str) and fingerprint.strip():
            return fingerprint.strip()
    # Back-compat: goal may store baseline fingerprint.
    goal = state.get("loop_goal")
    if isinstance(goal, dict):
        fingerprint = goal.get("baseline_fingerprint")
        if isinstance(fingerprint, str) and fingerprint.strip():
            return fingerprint.strip()
    return None


def dirty_baseline_goal_created_at(state: dict[str, Any]) -> str | None:
    baseline = state.get("loop_dirty_baseline")
    if isinstance(baseline, dict):
        value = baseline.get("goal_created_at")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def store_dirty_baseline(
    state: dict[str, Any],
    fingerprint: str,
    finding_id: str | None = None,
    goal_created_at: str | None = None,
) -> None:
    timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    entry: dict[str, Any] = {
        "fingerprint": fingerprint,
        "at": timestamp,
        "finding_id": finding_id,
    }
    if goal_created_at:
        entry["goal_created_at"] = goal_created_at
    else:
        # Preserve existing binding when caller does not supply one.
        existing = state.get("loop_dirty_baseline")
        if isinstance(existing, dict) and isinstance(existing.get("goal_created_at"), str):
            entry["goal_created_at"] = existing["goal_created_at"]
    state["loop_dirty_baseline"] = entry


def clear_dirty_baseline(state: dict[str, Any]) -> None:
    state.pop("loop_dirty_baseline", None)
    goal = state.get("loop_goal")
    if isinstance(goal, dict):
        goal.pop("baseline_fingerprint", None)


def has_verified_uncommitted_work(root: Path) -> bool:
    findings = read_findings(root)
    if not any(item.get("status") == "verified" for item in findings):
        return False
    try:
        return bool(filtered_git_porcelain(root))
    except OSError:
        # Fail closed: unreadable tree counts as uncommitted work.
        return True


def should_allow_dirty(root: Path, allow_dirty: bool, goal_created_at: str | None = None) -> bool:
    if allow_dirty:
        return True
    try:
        porcelain = filtered_git_porcelain(root)
    except OSError:
        return False
    if not porcelain:
        return True
    try:
        state = load_state(root)
    except OSError:
        return False
    baseline = load_dirty_baseline(state)
    if not baseline:
        return False
    if goal_created_at is not None:
        stored_goal = dirty_baseline_goal_created_at(state)
        # Bind baseline to the creating goal; old baselines without binding
        # never satisfy a new goal.
        if stored_goal != goal_created_at:
            return False
    try:
        current = worktree_fingerprint(root)
    except OSError:
        return False
    return current == baseline


def parse_priority_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(item).strip().upper() for item in raw if str(item).strip()]
    return [part.strip().upper() for part in str(raw).split(",") if part.strip()]


def validate_priorities(priority_str: str) -> list[str]:
    parts = parse_priority_list(priority_str)
    if not parts:
        raise ValueError("priority must include at least one of P0,P1,P2")
    for part in parts:
        if part not in VALID_PRIORITIES:
            raise ValueError(f"invalid priority {part!r}; expected subset of P0,P1,P2")
    seen: list[str] = []
    for part in parts:
        if part not in seen:
            seen.append(part)
    return seen


def validate_positive_int(name: str, value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a positive integer")
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError(f"{name} must be a positive integer")
        value = int(value)
    try:
        result = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a positive integer")
    if result <= 0:
        raise ValueError(f"{name} must be positive")
    return result


def validate_positive_finite(name: str, value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a positive finite number")
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a positive finite number")
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    if result <= 0:
        raise ValueError(f"{name} must be positive")
    return result


@dataclass(frozen=True)
class LoopSettings:
    max_iterations: int
    priority: str
    review_every: int
    empty_review_waves_required: int
    agent_timeout_seconds: float | None = None
    agent_idle_timeout_seconds: float | None = None
    until_clean: bool = False
    max_review_calls: int | None = None
    max_seconds: float | None = None
    new_goal: bool = False

    @property
    def priorities(self) -> list[str]:
        return [part.strip().upper() for part in self.priority.split(",") if part.strip()]


def _until_clean_default(key: str, fallback: Any) -> Any:
    try:
        value = CONFIG_DEFAULTS.get(key, fallback)
    except AttributeError:
        value = fallback
    return value if value is not None else fallback


def resolve_settings(
    root: Path,
    *,
    max_iterations: int | None,
    priority: str | None,
    review_every: int | None,
    empty_review_waves_required: int | None,
    agent_timeout_seconds: float | None = None,
    agent_idle_timeout_seconds: float | None = None,
    persist: bool,
    until_clean: bool = False,
    max_review_calls: int | None = None,
    max_seconds: float | None = None,
    new_goal: bool = False,
) -> LoopSettings:
    config = load_config(root)
    if until_clean:
        raw_priority = (
            priority
            if priority is not None
            else str(config.get("until_clean_priority", DEFAULT_UNTIL_CLEAN_PRIORITY))
            if "until_clean_priority" in config
            else DEFAULT_UNTIL_CLEAN_PRIORITY
        )
        validated = validate_priorities(str(raw_priority))
        priority_str = ",".join(validated)
        raw_max_iter = (
            max_iterations
            if max_iterations is not None
            else config.get("until_clean_max_iterations", config.get("until_clean_max_fix_attempts", _until_clean_default("until_clean_max_iterations", DEFAULT_UNTIL_CLEAN_MAX_FIX_ATTEMPTS)))
        )
        if raw_max_iter is None:
            raw_max_iter = DEFAULT_UNTIL_CLEAN_MAX_FIX_ATTEMPTS
        max_iter = validate_positive_int("max_iterations", raw_max_iter)
        raw_max_reviews = (
            max_review_calls
            if max_review_calls is not None
            else config.get("until_clean_max_review_calls", config.get("max_review_calls", _until_clean_default("until_clean_max_review_calls", DEFAULT_UNTIL_CLEAN_MAX_REVIEW_CALLS)))
        )
        if raw_max_reviews is None:
            raw_max_reviews = DEFAULT_UNTIL_CLEAN_MAX_REVIEW_CALLS
        max_rev = validate_positive_int("max_review_calls", raw_max_reviews)
        raw_max_seconds = (
            max_seconds
            if max_seconds is not None
            else config.get("until_clean_max_seconds", config.get("max_seconds", _until_clean_default("until_clean_max_seconds", DEFAULT_UNTIL_CLEAN_MAX_SECONDS)))
        )
        if raw_max_seconds is None:
            raw_max_seconds = DEFAULT_UNTIL_CLEAN_MAX_SECONDS
        max_sec = validate_positive_finite("max_seconds", raw_max_seconds)
        raw_review_every = review_every if review_every is not None else config.get("review_every", DEFAULT_REVIEW_EVERY)
        review_every_val = validate_positive_int("review_every", raw_review_every)
        empty_waves = UNTIL_CLEAN_REQUIRED_EMPTY_SWEEPS
        if empty_review_waves_required is not None:
            candidate = validate_positive_int("empty_review_waves", empty_review_waves_required)
            # Until-clean requires at least two complete empty sweeps; honor larger
            # requests but never allow fewer than two.
            empty_waves = max(UNTIL_CLEAN_REQUIRED_EMPTY_SWEEPS, candidate)
        agent_timeout = (
            float(agent_timeout_seconds)
            if agent_timeout_seconds is not None
            else float(config.get("agent_timeout_seconds", CONFIG_DEFAULTS["agent_timeout_seconds"]))
        )
        agent_idle = (
            float(agent_idle_timeout_seconds)
            if agent_idle_timeout_seconds is not None
            else float(config.get("agent_idle_timeout_seconds", CONFIG_DEFAULTS["agent_idle_timeout_seconds"]))
        )
        settings = LoopSettings(
            max_iterations=max_iter,
            priority=priority_str,
            review_every=review_every_val,
            empty_review_waves_required=empty_waves,
            agent_timeout_seconds=agent_timeout,
            agent_idle_timeout_seconds=agent_idle,
            until_clean=True,
            max_review_calls=max_rev,
            max_seconds=float(max_sec),
            new_goal=bool(new_goal),
        )
        if persist:
            config["until_clean_max_iterations"] = settings.max_iterations
            config["until_clean_max_review_calls"] = settings.max_review_calls
            config["until_clean_max_seconds"] = settings.max_seconds
            config["until_clean_priority"] = settings.priority
            config["review_every"] = settings.review_every
            config["empty_review_waves_required"] = settings.empty_review_waves_required
            config["agent_timeout_seconds"] = settings.agent_timeout_seconds
            config["agent_idle_timeout_seconds"] = settings.agent_idle_timeout_seconds
            config["codex_timeout_seconds"] = settings.agent_timeout_seconds
            config["codex_idle_timeout_seconds"] = settings.agent_idle_timeout_seconds
            if agent_idle_timeout_seconds is not None:
                config["agent_idle_timeout_override"] = True
            save_config(root, config)
        return settings
    config_max = config.get("max_iterations", DEFAULT_MAX_ITERATIONS)
    config_priority = config.get("loop_priority", DEFAULT_LOOP_PRIORITY)
    config_review_every = config.get("review_every", DEFAULT_REVIEW_EVERY)
    config_waves = config.get("empty_review_waves_required", DEFAULT_EMPTY_REVIEW_WAVES_REQUIRED)
    raw_max = max_iterations if max_iterations is not None else config_max
    raw_priority = priority if priority is not None else config_priority
    raw_every = review_every if review_every is not None else config_review_every
    raw_waves = empty_review_waves_required if empty_review_waves_required is not None else config_waves
    max_iter = validate_positive_int("max_iterations", raw_max)
    validated = validate_priorities(str(raw_priority))
    priority_str = ",".join(validated)
    review_every_val = validate_positive_int("review_every", raw_every)
    waves_val = validate_positive_int("empty_review_waves", raw_waves)
    settings = LoopSettings(
        max_iterations=max_iter,
        priority=priority_str,
        review_every=review_every_val,
        empty_review_waves_required=waves_val,
        agent_timeout_seconds=(
            float(agent_timeout_seconds)
            if agent_timeout_seconds is not None
            else float(config.get("agent_timeout_seconds", CONFIG_DEFAULTS["agent_timeout_seconds"]))
        ),
        agent_idle_timeout_seconds=(
            float(agent_idle_timeout_seconds)
            if agent_idle_timeout_seconds is not None
            else float(config.get("agent_idle_timeout_seconds", CONFIG_DEFAULTS["agent_idle_timeout_seconds"]))
        ),
        until_clean=False,
        max_review_calls=None,
        max_seconds=None,
        new_goal=False,
    )
    if persist:
        config["max_iterations"] = settings.max_iterations
        config["loop_priority"] = settings.priority
        config["review_every"] = settings.review_every
        config["empty_review_waves_required"] = settings.empty_review_waves_required
        config["agent_timeout_seconds"] = settings.agent_timeout_seconds
        config["agent_idle_timeout_seconds"] = settings.agent_idle_timeout_seconds
        config["codex_timeout_seconds"] = settings.agent_timeout_seconds
        config["codex_idle_timeout_seconds"] = settings.agent_idle_timeout_seconds
        if agent_idle_timeout_seconds is not None:
            config["agent_idle_timeout_override"] = True
        save_config(root, config)
    return settings


def baseline_verified_ids(root: Path) -> list[str]:
    return sorted(
        str(item.get("id"))
        for item in read_findings(root)
        if item.get("status") == "verified" and item.get("id")
    )


def partition_paths(root: Path) -> list[str]:
    inventory = load_json(root / ".deslop" / "inventory.json", {})
    partitions = inventory.get("risk_partitions", []) if isinstance(inventory, dict) else []
    paths: list[str] = []
    if isinstance(partitions, list):
        for item in partitions:
            if isinstance(item, dict) and item.get("path"):
                paths.append(str(item["path"]))
    if not paths:
        paths = ["."]
    return paths


def inventory_is_truncated(root: Path) -> bool:
    """True when inventory reports truncated coverage (fail closed on corrupt)."""
    path = root / ".deslop" / "inventory.json"
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return True
    if not isinstance(payload, dict):
        return True
    return bool(payload.get("truncated"))


def loop_progress(state: dict[str, Any]) -> dict[str, Any]:
    progress = state.get("loop_progress")
    if not isinstance(progress, dict):
        progress = {}
    progress.setdefault("consecutive_empty_review_waves", 0)
    progress.setdefault("partition_index", 0)
    progress.setdefault("partitions", [])
    progress.setdefault("coverage_priority", "")
    progress.setdefault("coverage_fingerprint", "")
    progress.setdefault("coverage_content_fingerprint", "")
    return progress


def _coverage_fingerprint(partitions: list[str], priority: str | None) -> str:
    normalized = "|".join(sorted(str(item) for item in partitions))
    scope = str(priority or "").strip().upper()
    return hashlib.sha256(f"{normalized}\n{scope}".encode("utf-8")).hexdigest()


def sync_partitions(root: Path, state: dict[str, Any], priority: str | None = None) -> dict[str, Any]:
    progress = loop_progress(state)
    previous_partitions = list(progress.get("partitions") or [])
    fresh_partitions = partition_paths(root)
    partitions_changed = previous_partitions != fresh_partitions
    progress["partitions"] = fresh_partitions
    if progress["partition_index"] >= len(progress["partitions"]):
        progress["partition_index"] = 0
    if priority is not None:
        normalized_priority = ",".join(parse_priority_list(priority))
        previous_priority = str(progress.get("coverage_priority") or "")
        if not previous_priority:
            progress["coverage_priority"] = normalized_priority
        elif previous_priority != normalized_priority:
            progress["coverage_priority"] = normalized_priority
            # Scope change restarts a complete sweep, not a partial suffix.
            progress["consecutive_empty_review_waves"] = 0
            progress["partition_index"] = 0
        if partitions_changed:
            progress["consecutive_empty_review_waves"] = 0
            progress["partition_index"] = 0
        progress["coverage_fingerprint"] = _coverage_fingerprint(fresh_partitions, normalized_priority)
    else:
        if partitions_changed and previous_partitions:
            progress["consecutive_empty_review_waves"] = 0
            progress["partition_index"] = 0
    state["loop_progress"] = progress
    return progress


def note_coverage_content(state: dict[str, Any], fingerprint: str) -> None:
    progress = loop_progress(state)
    progress["coverage_content_fingerprint"] = fingerprint
    state["loop_progress"] = progress


def coverage_content_changed(state: dict[str, Any], fingerprint: str) -> bool:
    progress = loop_progress(state)
    stored = str(progress.get("coverage_content_fingerprint") or "")
    if not stored:
        return False
    return stored != fingerprint


def reset_coverage_for_new_sweep(state: dict[str, Any], fingerprint: str | None = None) -> dict[str, Any]:
    progress = loop_progress(state)
    progress["consecutive_empty_review_waves"] = 0
    progress["partition_index"] = 0
    if fingerprint is not None:
        progress["coverage_content_fingerprint"] = fingerprint
    state["loop_progress"] = progress
    return progress


def current_partition(progress: dict[str, Any]) -> str | None:
    partitions = progress.get("partitions") or []
    index = int(progress.get("partition_index", 0) or 0)
    if not partitions or index >= len(partitions):
        return None
    return str(partitions[index])


def accepted_count_from_run(run_dir: Path) -> int:
    arbiter = run_dir / "arbiter.json"
    if not arbiter.exists():
        return 0
    payload = load_json(arbiter, {})
    accepted = payload.get("accepted", []) if isinstance(payload, dict) else []
    return len(accepted) if isinstance(accepted, list) else 0


def accepted_ids_from_run(run_dir: Path) -> list[str]:
    arbiter = run_dir / "arbiter.json"
    if not arbiter.exists():
        return []
    payload = load_json(arbiter, {})
    if not isinstance(payload, dict):
        return []
    accepted = payload.get("accepted", [])
    if not isinstance(accepted, list):
        return []
    return [str(item) for item in accepted if str(item).strip()]


def eligible_accepted_count_from_run(root: Path, run_dir: Path, priorities: list[str]) -> int:
    """Strict eligible count: current accepted ∩ scope only, never raw fallback."""
    arbiter = run_dir / "arbiter.json"
    if not arbiter.exists():
        return 0
    payload = load_json(arbiter, {})
    if not isinstance(payload, dict):
        return 0
    accepted = payload.get("accepted", [])
    if not isinstance(accepted, list):
        return 0
    wanted = {str(item).strip().upper() for item in priorities if str(item).strip()}
    if not wanted:
        return 0
    by_id = {str(item.get("id")): item for item in read_findings(root) if item.get("id")}
    count = 0
    for finding_id in accepted:
        item = by_id.get(str(finding_id))
        if item is None:
            # Dangling id: cannot confirm scope; never count as eligible.
            continue
        severity = str(item.get("severity", "")).upper()
        if severity in wanted:
            count += 1
    return count


def dangling_accepted_ids(root: Path, run_dir: Path) -> list[str]:
    """Accepted ids missing from current findings (stale arbiter output)."""
    ids = accepted_ids_from_run(run_dir)
    if not ids:
        return []
    by_id = {str(item.get("id")) for item in read_findings(root) if item.get("id")}
    return [item for item in ids if item not in by_id]


def findings_fingerprint(root: Path) -> str:
    items = read_findings(root)
    normalized = sorted(
        f"{str(item.get('id'))}:{str(item.get('status'))}:{str(item.get('severity', '')).upper()}"
        for item in items
        if item.get("id")
    )
    return hashlib.sha256("\n".join(normalized).encode("utf-8")).hexdigest()


def unresolved_findings(root: Path, priorities: list[str]) -> list[dict[str, Any]]:
    wanted = {str(item).strip().upper() for item in priorities if str(item).strip()}
    results: list[dict[str, Any]] = []
    for item in read_findings(root):
        if str(item.get("status")) not in UNRESOLVED_STATUSES:
            continue
        if wanted and str(item.get("severity", "")).upper() not in wanted:
            continue
        results.append(item)
    results.sort(key=lambda entry: str(entry.get("id") or ""))
    return results


def config_max_fix_attempts(root: Path) -> int:
    try:
        config = load_config(root)
    except OSError:
        return 3
    try:
        return max(1, int(config.get("max_fix_attempts", 3) or 3))
    except (TypeError, ValueError):
        return 3


def ineligible_accepted_findings(
    root: Path, priorities: list[str], max_fix_attempts: int | None = None
) -> list[dict[str, Any]]:
    """Accepted findings in scope that cannot proceed (deps/attempts)."""
    wanted = {str(item).strip().upper() for item in priorities if str(item).strip()}
    if max_fix_attempts is None:
        max_fix_attempts = config_max_fix_attempts(root)
    items = read_findings(root)
    by_id = {str(item.get("id")): item for item in items if item.get("id")}
    terminal = {"verified", "false_positive", "rejected"}
    results: list[dict[str, Any]] = []
    for item in items:
        if str(item.get("status")) != "accepted":
            continue
        if wanted and str(item.get("severity", "")).upper() not in wanted:
            continue
        blocked = False
        deps = item.get("dependencies") or []
        if isinstance(deps, list):
            for dep in deps:
                dep_id = str(dep).strip()
                if not dep_id:
                    continue
                dep_item = by_id.get(dep_id)
                if dep_item is None:
                    blocked = True
                    break
                if str(dep_item.get("status")) not in terminal:
                    blocked = True
                    break
        try:
            attempts = int(item.get("attempts", 0) or 0)
        except (TypeError, ValueError):
            attempts = 0
        if attempts >= max_fix_attempts:
            blocked = True
        if blocked:
            results.append(item)
    results.sort(key=lambda entry: str(entry.get("id") or ""))
    return results


def clean_blockers(root: Path, priorities: list[str]) -> list[dict[str, Any]]:
    """All findings that must prevent an until-clean claim at scope."""
    blockers = list(unresolved_findings(root, priorities))
    seen = {str(item.get("id")) for item in blockers}
    for item in ineligible_accepted_findings(root, priorities):
        if str(item.get("id")) not in seen:
            blockers.append(item)
            seen.add(str(item.get("id")))
    blockers.sort(key=lambda entry: str(entry.get("id") or ""))
    return blockers


def review_wave_result(
    *,
    progress: dict[str, Any],
    accepted_count: int,
    empty_review_waves_required: int,
) -> tuple[dict[str, Any], str]:
    """Advance review-wave state and return updated progress plus action."""
    if accepted_count > 0:
        progress["consecutive_empty_review_waves"] = 0
        partitions = progress.get("partitions") or []
        index = int(progress.get("partition_index", 0) or 0) + 1
        if partitions:
            progress["partition_index"] = index % len(partitions)
        else:
            progress["partition_index"] = 0
        return progress, "continue"

    partitions = progress.get("partitions") or []
    index = int(progress.get("partition_index", 0) or 0) + 1
    progress["partition_index"] = index
    if index < len(partitions):
        return progress, "continue_partition"

    progress["consecutive_empty_review_waves"] = int(progress.get("consecutive_empty_review_waves", 0) or 0) + 1
    progress["partition_index"] = 0
    if progress["consecutive_empty_review_waves"] >= empty_review_waves_required:
        return progress, "stop_empty"
    return progress, "continue_wave"


def latest_json(root: Path, kind: str, finding_id: str) -> Path | None:
    runs = root / ".deslop" / "runs"
    if not runs.exists():
        return None
    matches = sorted(
        runs.glob(f"*-{kind}-{finding_id}/{kind}.json"),
        key=lambda path: path.as_posix(),
        reverse=True,
    )
    return matches[0] if matches else None


def choose_next_id(root: Path, priorities: list[str]) -> str | None:
    script = Path(__file__).resolve().parent / "deslop-next.py"
    result = subprocess.run(
        [str(script), "--priority", ",".join(priorities)],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "deslop-next.py failed")
    value = result.stdout.strip()
    return None if value == "NONE" else value


def stop_requested(root: Path) -> bool:
    return (root / ".deslop" / "stop").exists()


def loop_lock_path(root: Path) -> Path:
    return root / ".deslop" / LOOP_LOCK_NAME


def acquire_loop_lock(root: Path) -> None:
    """Kernel flock: held until release; crash auto-releases (no pid juggling)."""
    global _LOCK_FD
    path = loop_lock_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    if fcntl is None:  # pragma: no cover - POSIX project always has fcntl
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            fd = os.open(str(path), flags, 0o644)
        except FileExistsError:
            raise RuntimeError(f"another deslop-loop is running (lock {path}); remove {path} if stale")
        try:
            payload = json.dumps({"pid": os.getpid(), "at": time.time()}) + "\n"
            os.write(fd, payload.encode("utf-8"))
        finally:
            os.close(fd)
        _LOCK_FD = -1
        return
    flags = os.O_CREAT | os.O_RDWR
    try:
        cloexec = getattr(os, "O_CLOEXEC", 0)
        fd = os.open(str(path), flags | cloexec, 0o644)
    except OSError as exc:
        raise RuntimeError(f"another deslop-loop is running (lock {path}): {exc}")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        raise RuntimeError(f"another deslop-loop is running (lock {path}); remove {path} if stale")
    try:
        os.ftruncate(fd, 0)
        payload = json.dumps({"pid": os.getpid(), "at": time.time()}) + "\n"
        os.write(fd, payload.encode("utf-8"))
    except OSError:
        pass
    _LOCK_FD = fd


def release_loop_lock(root: Path) -> None:
    global _LOCK_FD
    if _LOCK_FD is not None:
        fd = _LOCK_FD
        _LOCK_FD = None
        if fd == -1:  # legacy O_EXCL fallback
            try:
                loop_lock_path(root).unlink()
            except OSError:
                pass
            return
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(fd)
        except OSError:
            pass
        return
    # No held fd (e.g., lock from older version): best-effort remove only if
    # we can prove nobody holds it. With flock this is automatic; if the file
    # exists but is unlocked, leave it (next acquire reuses it).
    return


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_loop_goal(state: dict[str, Any]) -> dict[str, Any] | None:
    goal = state.get("loop_goal")
    return goal if isinstance(goal, dict) else None


def create_loop_goal(
    *,
    priority: str,
    max_fix_attempts: int,
    max_review_calls: int,
    max_seconds: float,
    partitions: list[str],
) -> dict[str, Any]:
    validated = validate_priorities(priority)
    max_fix = validate_positive_int("max_iterations", max_fix_attempts)
    max_rev = validate_positive_int("max_review_calls", max_review_calls)
    max_sec = validate_positive_finite("max_seconds", max_seconds)
    timestamp = now_iso()
    return {
        "scope": GOAL_SCOPE_UNTIL_CLEAN,
        "priority": ",".join(validated),
        "partitions": list(partitions),
        "limits": {
            "max_fix_attempts": max_fix,
            "max_review_calls": max_rev,
            "max_seconds": float(max_sec),
        },
        "consumed": {
            "fix_attempts": 0,
            "review_calls": 0,
            "elapsed_seconds": 0.0,
        },
        "status": "active",
        "reason": None,
        "created_at": timestamp,
        "updated_at": timestamp,
        "empty_sweeps_required": UNTIL_CLEAN_REQUIRED_EMPTY_SWEEPS,
    }


def ensure_goal_consumed(goal: dict[str, Any]) -> dict[str, Any]:
    consumed = goal.get("consumed")
    if not isinstance(consumed, dict):
        consumed = {}
        goal["consumed"] = consumed
    consumed.setdefault("fix_attempts", 0)
    consumed.setdefault("review_calls", 0)
    consumed.setdefault("elapsed_seconds", 0.0)
    try:
        consumed["fix_attempts"] = int(consumed.get("fix_attempts", 0) or 0)
    except (TypeError, ValueError):
        consumed["fix_attempts"] = 0
    try:
        consumed["review_calls"] = int(consumed.get("review_calls", 0) or 0)
    except (TypeError, ValueError):
        consumed["review_calls"] = 0
    try:
        consumed["elapsed_seconds"] = float(consumed.get("elapsed_seconds", 0.0) or 0.0)
    except (TypeError, ValueError):
        consumed["elapsed_seconds"] = 0.0
    if not math.isfinite(consumed["elapsed_seconds"]):
        consumed["elapsed_seconds"] = 0.0
    return consumed


def goal_in_flight(goal: dict[str, Any]) -> dict[str, Any] | None:
    marker = goal.get("in_flight")
    return marker if isinstance(marker, dict) else None


def effective_consumed_elapsed(goal: dict[str, Any], *, now_wall: float | None = None) -> float:
    """Stored elapsed plus in-flight wall delta (crash checkpoint)."""
    consumed = ensure_goal_consumed(goal)
    try:
        base = float(consumed.get("elapsed_seconds", 0.0) or 0.0)
    except (TypeError, ValueError):
        base = 0.0
    marker = goal_in_flight(goal)
    if marker is not None:
        try:
            started = float(marker.get("started_at", 0) or 0)
            at_start = float(marker.get("elapsed_at_start", base) or 0)
        except (TypeError, ValueError):
            return base
        if started > 0:
            wall = time.time() if now_wall is None else now_wall
            delta = wall - started
            if delta > 0 and math.isfinite(delta):
                # In-flight wall time counts toward the wall budget even if the
                # previous process died before persisting post-child elapsed.
                return max(base, at_start + delta)
    return base


def goal_status_for_stop_reason(stop_reason: str) -> str:
    mapping = {
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
    return mapping.get(stop_reason, "failed")


def get_finding_status(root: Path, finding_id: str) -> str:
    for item in read_findings(root):
        if str(item.get("id")) == finding_id:
            return str(item.get("status") or "unknown")
    return "unknown"


def get_finding(root: Path, finding_id: str) -> dict[str, Any] | None:
    for item in read_findings(root):
        if str(item.get("id")) == finding_id:
            return item
    return None
