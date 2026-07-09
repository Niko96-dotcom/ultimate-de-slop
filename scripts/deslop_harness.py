#!/usr/bin/env python3
"""Resolve the Ultimate De-Slop child-agent harness for a skill install."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


INSTALL_MARKER = ".ultimate-de-slop-install.json"
CONFIG_DEFAULTS: dict[str, Any] = {
    "version": 1,
    "max_iterations": 5,
    "max_fix_attempts": 3,
    "max_findings_per_reviewer": 5,
    "max_active_findings": 10,
    "max_changed_files_per_fix": 8,
    "max_changed_lines_per_fix": 400,
    "agent_timeout_seconds": 5400,
    "agent_idle_timeout_seconds": 1200,
    "agent_idle_timeout_buffering_seconds": 0,
    "agent_idle_timeout_fix_seconds": 3600,
    "agent_idle_timeout_override": False,
    "agent_terminate_grace_seconds": 10,
    "codex_timeout_seconds": 5400,
    "codex_idle_timeout_seconds": 1200,
    "codex_terminate_grace_seconds": 10,
    "confidence_thresholds": {"P0": 0.70, "P1": 0.75, "P2": 0.85},
    "loop_priority": "P0,P1",
    "review_every": 1,
    "empty_review_waves_required": 2,
    "ignored_paths": [
        "node_modules",
        ".git",
        "dist",
        "build",
        "coverage",
        "vendor",
        ".next",
        ".turbo",
        ".venv",
        "__pycache__",
    ],
    "commit_by_default": False,
    "auto_revert_by_default": False,
}
DEFAULT_HARNESS = "codex"
RUNNER_HARNESSES = frozenset(
    {
        "claude",
        "codex",
        "commandcode",
        "cursor",
        "hermes",
        "opencode",
        "openclaw",
        "pi",
    }
)
# Harness CLIs that often run long tool sessions without streaming stdout.
BUFFERING_HARNESSES = frozenset(
    {
        "claude",
        "commandcode",
        "cursor",
        "hermes",
        "openclaw",
        "opencode",
        "pi",
    }
)
FIX_KINDS = frozenset({"fix", "fixer"})
IDLE_ENV_NAMES = ["DESLOP_IDLE_TIMEOUT_SECONDS", "DESLOP_CODEX_IDLE_TIMEOUT_SECONDS"]
IDLE_CONFIG_NAMES = [
    "agent_idle_timeout_seconds",
    "idle_timeout_seconds",
    "codex_idle_timeout_seconds",
]


def skill_root_from_script_dir(script_dir: Path) -> Path:
    return script_dir.resolve().parent


def harness_from_install_marker(skill_root: Path) -> str | None:
    marker_path = skill_root / INSTALL_MARKER
    if not marker_path.is_file():
        return None
    try:
        payload = json.loads(marker_path.read_text())
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    harness = payload.get("harness")
    if not isinstance(harness, str):
        return None
    harness = harness.strip().lower()
    if harness not in RUNNER_HARNESSES:
        return None
    return harness


def resolve_harness(
    *,
    explicit: str | None = None,
    script_dir: Path | None = None,
) -> str:
    if explicit:
        return explicit.strip().lower()
    env_value = os.environ.get("DESLOP_HARNESS")
    if env_value:
        return env_value.strip().lower()
    if script_dir is not None:
        marker_harness = harness_from_install_marker(skill_root_from_script_dir(script_dir))
        if marker_harness:
            return marker_harness
    return DEFAULT_HARNESS


def load_deslop_config(root: Path) -> dict[str, Any]:
    path = root / ".deslop" / "config.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def ensure_config_defaults(config: dict[str, Any]) -> bool:
    changed = False
    for key, value in CONFIG_DEFAULTS.items():
        if key not in config:
            config[key] = value
            changed = True
    return changed


def seconds_setting(
    env_names: list[str],
    config: dict[str, Any],
    config_names: list[str],
    default: float,
) -> float:
    raw: Any = None
    for env_name in env_names:
        raw = os.environ.get(env_name)
        if raw is not None:
            break
    if raw is None:
        for config_name in config_names:
            raw = config.get(config_name)
            if raw is not None:
                break
    if raw is None:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return max(value, 0.0)


def idle_timeout_explicitly_configured() -> bool:
    return any(os.environ.get(name) for name in IDLE_ENV_NAMES)


def resolve_agent_timeouts(
    root: Path,
    *,
    harness: str | None = None,
    kind: str | None = None,
    script_dir: Path | None = None,
) -> dict[str, float | str]:
    config = load_deslop_config(root)
    resolved_harness = (harness or resolve_harness(script_dir=script_dir)).strip().lower()
    resolved_kind = (kind or "agent").strip().lower()

    timeout_seconds = seconds_setting(
        ["DESLOP_TIMEOUT_SECONDS", "DESLOP_CODEX_TIMEOUT_SECONDS"],
        config,
        ["agent_timeout_seconds", "timeout_seconds", "codex_timeout_seconds"],
        float(CONFIG_DEFAULTS["agent_timeout_seconds"]),
    )
    grace_seconds = seconds_setting(
        ["DESLOP_TERMINATE_GRACE_SECONDS", "DESLOP_CODEX_TERMINATE_GRACE_SECONDS"],
        config,
        ["agent_terminate_grace_seconds", "terminate_grace_seconds", "codex_terminate_grace_seconds"],
        float(CONFIG_DEFAULTS["agent_terminate_grace_seconds"]),
    )

    if idle_timeout_explicitly_configured():
        idle_timeout_seconds = seconds_setting(
            IDLE_ENV_NAMES,
            config,
            IDLE_CONFIG_NAMES,
            float(CONFIG_DEFAULTS["agent_idle_timeout_seconds"]),
        )
        idle_timeout_source = "env"
    elif config.get("agent_idle_timeout_override"):
        idle_timeout_seconds = seconds_setting(
            [],
            config,
            ["agent_idle_timeout_seconds"],
            float(CONFIG_DEFAULTS["agent_idle_timeout_seconds"]),
        )
        idle_timeout_source = "config_override"
    elif resolved_harness in BUFFERING_HARNESSES:
        idle_timeout_seconds = seconds_setting(
            [],
            config,
            ["agent_idle_timeout_buffering_seconds"],
            float(CONFIG_DEFAULTS["agent_idle_timeout_buffering_seconds"]),
        )
        idle_timeout_source = f"harness:{resolved_harness}"
    elif resolved_kind in FIX_KINDS:
        idle_timeout_seconds = seconds_setting(
            ["DESLOP_FIX_IDLE_TIMEOUT_SECONDS"],
            config,
            ["agent_idle_timeout_fix_seconds", *IDLE_CONFIG_NAMES],
            float(CONFIG_DEFAULTS["agent_idle_timeout_fix_seconds"]),
        )
        idle_timeout_source = f"kind:{resolved_kind}"
    else:
        idle_timeout_seconds = seconds_setting(
            IDLE_ENV_NAMES,
            config,
            IDLE_CONFIG_NAMES,
            float(CONFIG_DEFAULTS["agent_idle_timeout_seconds"]),
        )
        idle_timeout_source = "config"

    return {
        "timeout_seconds": timeout_seconds,
        "idle_timeout_seconds": idle_timeout_seconds,
        "grace_seconds": grace_seconds,
        "idle_timeout_source": idle_timeout_source,
        "harness": resolved_harness,
        "kind": resolved_kind,
    }


def format_agent_timeouts(root: Path, *, harness: str | None = None, kind: str | None = None) -> str:
    script_dir = Path(__file__).resolve().parent
    timeouts = resolve_agent_timeouts(root, harness=harness, kind=kind, script_dir=script_dir)
    wall = int(timeouts["timeout_seconds"])
    idle = int(timeouts["idle_timeout_seconds"])
    idle_note = "disabled (wall only)" if idle == 0 else f"{idle}s no-stdout idle cap"
    source = timeouts.get("idle_timeout_source", "config")
    harness_name = timeouts.get("harness", harness or "default")
    kind_name = timeouts.get("kind", kind or "agent")
    return (
        f"Agent timeouts ({harness_name}/{kind_name}): wall={wall}s, idle={idle_note} "
        f"[idle source: {source}]"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resolve the Ultimate De-Slop harness.")
    parser.add_argument("--print", action="store_true", help="print the resolved harness")
    parser.add_argument("--script-dir", type=Path, help="skill scripts directory for install-marker lookup")
    parser.add_argument("--harness", help="explicit harness override")
    args = parser.parse_args(argv)
    script_dir = args.script_dir
    if script_dir is None:
        script_dir = Path(__file__).resolve().parent
    harness = resolve_harness(explicit=args.harness, script_dir=script_dir)
    if args.print:
        print(harness)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
