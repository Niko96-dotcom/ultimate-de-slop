#!/usr/bin/env python3
"""Shared helpers for ultimate-de-slop bounded loop orchestration."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from deslop_harness import CONFIG_DEFAULTS, ensure_config_defaults


DEFAULT_LOOP_PRIORITY = "P0,P1"
DEFAULT_MAX_ITERATIONS = 5
DEFAULT_REVIEW_EVERY = 1
DEFAULT_EMPTY_REVIEW_WAVES_REQUIRED = 2


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
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


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


def git_porcelain(root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return result.stdout.strip()


def has_verified_uncommitted_work(root: Path) -> bool:
    findings = read_findings(root)
    if not any(item.get("status") == "verified" for item in findings):
        return False
    return bool(git_porcelain(root))


def should_allow_dirty(root: Path, allow_dirty: bool) -> bool:
    if allow_dirty:
        return True
    if not git_porcelain(root):
        return True
    return has_verified_uncommitted_work(root)


@dataclass(frozen=True)
class LoopSettings:
    max_iterations: int
    priority: str
    review_every: int
    empty_review_waves_required: int
    agent_timeout_seconds: float | None = None
    agent_idle_timeout_seconds: float | None = None

    @property
    def priorities(self) -> list[str]:
        return [part.strip().upper() for part in self.priority.split(",") if part.strip()]


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
) -> LoopSettings:
    config = load_config(root)
    settings = LoopSettings(
        max_iterations=int(max_iterations if max_iterations is not None else config.get("max_iterations", DEFAULT_MAX_ITERATIONS)),
        priority=str(priority if priority is not None else config.get("loop_priority", DEFAULT_LOOP_PRIORITY)),
        review_every=int(review_every if review_every is not None else config.get("review_every", DEFAULT_REVIEW_EVERY)),
        empty_review_waves_required=int(
            empty_review_waves_required
            if empty_review_waves_required is not None
            else config.get("empty_review_waves_required", DEFAULT_EMPTY_REVIEW_WAVES_REQUIRED)
        ),
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


def loop_progress(state: dict[str, Any]) -> dict[str, Any]:
    progress = state.get("loop_progress")
    if not isinstance(progress, dict):
        progress = {}
    progress.setdefault("consecutive_empty_review_waves", 0)
    progress.setdefault("partition_index", 0)
    progress.setdefault("partitions", [])
    return progress


def sync_partitions(root: Path, state: dict[str, Any]) -> dict[str, Any]:
    progress = loop_progress(state)
    progress["partitions"] = partition_paths(root)
    if progress["partition_index"] >= len(progress["partitions"]):
        progress["partition_index"] = 0
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


def review_wave_result(
    *,
    progress: dict[str, Any],
    accepted_count: int,
    empty_review_waves_required: int,
) -> tuple[dict[str, Any], str]:
    """Advance review-wave state and return updated progress plus action."""
    if accepted_count > 0:
        progress["consecutive_empty_review_waves"] = 0
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
