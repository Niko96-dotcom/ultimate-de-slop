#!/usr/bin/env python3
"""Resolve the Ultimate De-Slop child-agent harness for a skill install."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


INSTALL_MARKER = ".ultimate-de-slop-install.json"
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
