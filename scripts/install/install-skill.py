#!/usr/bin/env python3
"""Install ultimate-de-slop into harness-specific skill directories."""

from __future__ import annotations

import argparse
import filecmp
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


SKILL_NAME = "ultimate-de-slop"
INSTALLER_VERSION = 3

LAYOUTS = {
    "agents": {
        "global": Path(".agents/skills") / SKILL_NAME,
        "local": Path(".agents/skills") / SKILL_NAME,
        "runner_harness": None,
        "label": "shared portable fallback",
    },
    "claude": {
        "global": Path(".claude/skills") / SKILL_NAME,
        "local": Path(".claude/skills") / SKILL_NAME,
        "runner_harness": "claude",
        "label": "Claude",
    },
    "codex": {
        "global": Path(".codex/skills") / SKILL_NAME,
        "local": Path(".codex/skills") / SKILL_NAME,
        "runner_harness": "codex",
        "label": "Codex",
    },
    "commandcode": {
        "global": Path(".commandcode/skills") / SKILL_NAME,
        "local": Path(".commandcode/skills") / SKILL_NAME,
        "runner_harness": "commandcode",
        "label": "Command Code",
    },
    "cursor": {
        "global": Path(".cursor/skills") / SKILL_NAME,
        "local": Path(".cursor/skills") / SKILL_NAME,
        "runner_harness": "cursor",
        "label": "Cursor",
    },
    "hermes": {
        "global": Path(".hermes/skills/software-development") / SKILL_NAME,
        "local": Path(".hermes/skills/software-development") / SKILL_NAME,
        "runner_harness": "hermes",
        "label": "Hermes",
    },
    "opencode": {
        "global": Path(".config/opencode/skills") / SKILL_NAME,
        "local": Path(".opencode/skills") / SKILL_NAME,
        "runner_harness": "opencode",
        "label": "OpenCode",
    },
    "openclaw": {
        "global": Path(".openclaw/skills") / SKILL_NAME,
        "local": Path(".openclaw/skills") / SKILL_NAME,
        "runner_harness": "openclaw",
        "label": "OpenClaw",
    },
    "pi": {
        "global": Path(".pi/skills") / SKILL_NAME,
        "local": Path(".pi/skills") / SKILL_NAME,
        "runner_harness": "pi",
        "label": "Pi",
    },
}

SKIP_DIRS = {".git", ".deslop", "__pycache__", ".cursor", ".codex", ".agents",
             ".claude", ".opencode", ".pi", ".hermes", ".openclaw", ".commandcode",
             ".venv", "node_modules"}
SKIP_NAMES = {".DS_Store"}
MARKER = ".ultimate-de-slop-install.json"


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def skill_root() -> Path:
    return Path(__file__).resolve().parents[2]


def should_skip(path: Path) -> bool:
    if path.name in SKIP_NAMES or path.name == MARKER or path.suffix == ".pyc":
        return True
    return any(part in SKIP_DIRS for part in path.parts)


def target_path(harness: str, scope: str, home: Path, project_dir: Path) -> Path:
    base = home if scope == "global" else project_dir
    return base / LAYOUTS[harness][scope]


def same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except FileNotFoundError:
        return False


def read_marker(target: Path) -> dict[str, object] | None:
    marker = target / MARKER
    if not marker.exists():
        return None
    try:
        data = json.loads(marker.read_text())
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) and data.get("installer") == SKILL_NAME else None


def file_digest(path: Path) -> str:
    data = os.readlink(path).encode() if path.is_symlink() else path.read_bytes()
    return hashlib.sha256(data).hexdigest()


def source_files(source: Path) -> dict[str, str]:
    """Record the files this installer owns so updates can remove stale copies."""
    return {
        item.relative_to(source).as_posix(): file_digest(item)
        for item in source.rglob("*")
        if not should_skip(item.relative_to(source))
        and (item.is_file() or item.is_symlink())
    }


def copy_tree(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for item in source.rglob("*"):
        rel = item.relative_to(source)
        if should_skip(rel):
            continue
        destination = target / rel
        if item.is_symlink():
            if destination.exists() or destination.is_symlink():
                destination.unlink()
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.symlink_to(os.readlink(item))
            continue
        if item.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and filecmp.cmp(item, destination, shallow=False):
            continue
        shutil.copy2(item, destination)


def backup_root(target: Path) -> Path:
    skills_dir = next(parent for parent in target.parents if parent.name == "skills")
    return skills_dir.parent / "skill-backups"


def backup_existing(target: Path, dry_run: bool) -> Path | None:
    if not target.exists() or read_marker(target):
        return None
    # A backup under skills/ would itself be discovered as another skill.
    backup_dir = backup_root(target)
    backup = backup_dir / f"{target.name}.{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    if dry_run:
        return backup
    suffix = 1
    candidate = backup
    while candidate.exists():
        suffix += 1
        candidate = backup_dir / f"{backup.name}.{suffix}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(target, candidate, symlinks=True)
    return candidate


def prune_stale_files(source: Path, target: Path, marker: dict[str, object] | None) -> list[str]:
    if not marker:
        return []
    current = source_files(source)
    previous = marker.get("files")
    stale: dict[str, str] = {}
    if isinstance(previous, dict):
        stale = {name: digest for name, digest in previous.items()
                 if isinstance(name, str) and isinstance(digest, str) and name not in current}
    elif marker.get("installer_version") == 2:
        # Version 2 had no manifest; preserve any obsolete files outside discovery.
        stale = {
            item.relative_to(target).as_posix(): ""
            for item in target.rglob("*")
            if (item.is_file() or item.is_symlink())
            and item.name != MARKER
            and item.relative_to(target).as_posix() not in current
        }
    removed: list[str] = []
    for name, digest in stale.items():
        relative = Path(name)
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            continue
        path = target / name
        if not path.parent.resolve().is_relative_to(target.resolve()):
            continue
        if path.is_file() or path.is_symlink():
            if digest and file_digest(path) == digest:
                path.unlink()
            else:
                # Preserve edits while moving obsolete files out of skill discovery.
                backup_dir = backup_root(target)
                backup = backup_dir / f"{target.name}.stale.{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}" / relative
                suffix = 1
                while backup.exists():
                    suffix += 1
                    backup = backup_dir / f"{target.name}.stale.{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.{suffix}" / relative
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(backup))
            removed.append(name)
            parent = path.parent
            while parent != target:
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent
    return removed


def write_marker(target: Path, harness: str, scope: str, source: Path) -> None:
    payload = {
        "installer": "ultimate-de-slop",
        "installer_version": INSTALLER_VERSION,
        "harness": harness,
        "scope": scope,
        "source": str(source),
        "installed_at": now(),
        "files": source_files(source),
    }
    (target / MARKER).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def install_codex_profiles(source: Path, scope: str, home: Path, project_dir: Path, dry_run: bool) -> list[str]:
    base = home / ".codex" if scope == "global" else project_dir / ".codex"
    template_root = source / "templates" / "codex"
    actions: list[str] = []
    agents_source = template_root / "agents"
    agents_target = base / "agents"
    if agents_source.exists():
        for item in sorted(agents_source.glob("*.toml")):
            destination = agents_target / item.name
            actions.append(f"agent profile: {destination}")
            if not dry_run:
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists() and not filecmp.cmp(item, destination, shallow=False):
                    shutil.copy2(destination, destination.with_suffix(destination.suffix + f".backup.{now()}"))
                shutil.copy2(item, destination)
    config_source = template_root / "config.toml"
    config_target = base / "config.toml"
    if config_source.exists() and not config_target.exists():
        actions.append(f"codex config: {config_target}")
        if not dry_run:
            config_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(config_source, config_target)
    return actions


def copy_template_files(source_dir: Path, target_dir: Path, dry_run: bool, label: str) -> list[str]:
    actions: list[str] = []
    if not source_dir.exists():
        return actions
    for item in sorted(source_dir.glob("*")):
        if not item.is_file():
            continue
        destination = target_dir / item.name
        actions.append(f"{label}: {destination}")
        if dry_run:
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and not filecmp.cmp(item, destination, shallow=False):
            shutil.copy2(destination, destination.with_suffix(destination.suffix + f".backup.{now()}"))
        shutil.copy2(item, destination)
    return actions


def opencode_config_root(scope: str, home: Path, project_dir: Path) -> Path:
    if scope == "global":
        return home / ".config" / "opencode"
    return project_dir / ".opencode"


def install_opencode_assets(source: Path, scope: str, home: Path, project_dir: Path, dry_run: bool) -> list[str]:
    config_root = opencode_config_root(scope, home, project_dir)
    template_root = source / "templates" / "opencode"
    actions: list[str] = []
    actions.extend(copy_template_files(template_root / "agents", config_root / "agents", dry_run, "opencode agent"))
    actions.extend(copy_template_files(template_root / "command", config_root / "command", dry_run, "opencode command"))
    return actions


def cursor_config_root(scope: str, home: Path, project_dir: Path) -> Path:
    if scope == "global":
        return home / ".cursor"
    return project_dir / ".cursor"


def copy_template_tree(source: Path, target: Path, dry_run: bool, label: str) -> list[str]:
    actions = [f"{label}: {target}"]
    if dry_run:
        return actions
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target, symlinks=True)
    return actions


def claude_config_root(scope: str, home: Path, project_dir: Path) -> Path:
    if scope == "global":
        return home / ".claude"
    return project_dir / ".claude"


def install_claude_assets(source: Path, scope: str, home: Path, project_dir: Path, dry_run: bool) -> list[str]:
    config_root = claude_config_root(scope, home, project_dir)
    template_root = source / "templates" / "claude"
    actions: list[str] = []
    actions.extend(copy_template_files(template_root / "commands", config_root / "commands", dry_run, "claude command"))
    return actions


def codex_marketplace_root(home: Path) -> Path:
    return home / ".codex" / "marketplaces" / SKILL_NAME


def install_codex_command_plugin(source: Path, home: Path, dry_run: bool) -> tuple[list[str], str]:
    template_root = source / "templates" / "codex"
    marketplace_target = codex_marketplace_root(home)
    actions: list[str] = []
    actions.extend(
        copy_template_tree(
            template_root / "marketplace",
            marketplace_target,
            dry_run,
            "codex plugin marketplace",
        )
    )
    # Keep the catalog template outside .agents/ in the source: the repository
    # ignores harness-specific .agents/ directories.
    catalog_source = template_root / "marketplace-catalog.json"
    catalog_target = marketplace_target / ".agents" / "plugins" / "marketplace.json"
    actions.append(f"codex plugin marketplace catalog: {catalog_target}")
    if not dry_run:
        catalog_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(catalog_source, catalog_target)
    command_source = template_root / "commands" / "ultimate-de-slop.md"
    command_target = marketplace_target / "plugins" / SKILL_NAME / "commands" / "ultimate-de-slop.md"
    actions.append(f"codex plugin command: {command_target}")
    if not dry_run:
        command_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(command_source, command_target)
    if dry_run:
        actions.append(f"codex plugin marketplace add: {marketplace_target}")
        actions.append("codex plugin add ultimate-de-slop@ultimate-de-slop")
        return actions, "preview"
    codex = shutil.which("codex")
    if codex is None:
        actions.append("codex plugin skipped: codex CLI is not on PATH")
        return actions, "skipped"
    codex_env = {**os.environ, "CODEX_HOME": str(home / ".codex")}
    marketplace_result = subprocess.run(
        [codex, "plugin", "marketplace", "add", str(marketplace_target)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=codex_env,
        check=False,
    )
    if marketplace_result.returncode == 0:
        actions.append(f"codex plugin marketplace registered: {marketplace_target}")
    elif "already registered" in marketplace_result.stdout.lower():
        actions.append(f"codex plugin marketplace already registered: {marketplace_target}")
    else:
        actions.append(
            "codex plugin marketplace registration failed: "
            + marketplace_result.stdout.strip().replace("\n", " ")
        )
        return actions, "failed"
    plugin_result = subprocess.run(
        [codex, "plugin", "add", "ultimate-de-slop@ultimate-de-slop"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=codex_env,
        check=False,
    )
    if plugin_result.returncode == 0:
        actions.append("codex plugin installed: ultimate-de-slop@ultimate-de-slop")
    elif "already installed" in plugin_result.stdout.lower():
        actions.append("codex plugin already installed: ultimate-de-slop@ultimate-de-slop")
    else:
        actions.append(
            "codex plugin install failed: " + plugin_result.stdout.strip().replace("\n", " ")
        )
        return actions, "failed"
    return actions, "installed"


def install_cursor_assets(source: Path, scope: str, home: Path, project_dir: Path, dry_run: bool) -> list[str]:
    config_root = cursor_config_root(scope, home, project_dir)
    template_root = source / "templates" / "cursor"
    actions: list[str] = []
    actions.extend(copy_template_files(template_root / "commands", config_root / "commands", dry_run, "cursor command"))
    return actions


def install(args: argparse.Namespace) -> int:
    source = skill_root()
    home = Path(args.home or os.environ.get("HOME", "~")).expanduser().resolve()
    project_dir = Path(args.project_dir).expanduser().resolve()
    target = target_path(args.harness, args.scope, home, project_dir)
    layout = LAYOUTS[args.harness]

    print(f"Installing {SKILL_NAME} for {layout['label']} ({args.scope})")
    print(f"Source: {source}")
    print(f"Target: {target}")

    if target.is_symlink():
        raise ValueError(f"Refusing to install into a symlink: {target}")
    if same_path(source, target):
        print("Target already is the canonical installed skill; no copy needed.")
    else:
        old_marker = read_marker(target)
        backup = backup_existing(target, args.dry_run)
        if backup:
            print(f"Backup: {backup}")
        if args.dry_run:
            print("Dry run: would copy skill files, excluding .deslop, .git, caches, and .DS_Store.")
        else:
            if backup:
                shutil.rmtree(target)
            copy_tree(source, target)
            removed = prune_stale_files(source, target, old_marker)
            write_marker(target, args.harness, args.scope, source)
            print("Copied skill files.")
            if removed:
                print(f"Removed {len(removed)} obsolete installed file(s).")

    extra_actions: list[str] = []
    plugin_status = "not_requested"
    if args.harness == "codex":
        extra_actions = install_codex_profiles(source, args.scope, home, project_dir, args.dry_run)
        for action in extra_actions:
            print(f"Installed {action}" if not args.dry_run else f"Dry run: would install {action}")
        if args.scope == "global":
            plugin_actions, plugin_status = install_codex_command_plugin(source, home, args.dry_run)
            for action in plugin_actions:
                print(action if not args.dry_run else f"Dry run: would install {action}")
            if plugin_status == "installed":
                print("Codex slash command: /ultimate-de-slop (restart Codex or open a new chat if it does not appear).")
    if args.harness == "claude":
        extra_actions = install_claude_assets(source, args.scope, home, project_dir, args.dry_run)
        for action in extra_actions:
            print(f"Installed {action}" if not args.dry_run else f"Dry run: would install {action}")
        if extra_actions:
            print("Claude Code slash command: /ultimate-de-slop")
    if args.harness == "opencode":
        extra_actions = install_opencode_assets(source, args.scope, home, project_dir, args.dry_run)
        for action in extra_actions:
            print(f"Installed {action}" if not args.dry_run else f"Dry run: would install {action}")
        if extra_actions:
            print("OpenCode loads agent, command, and skill files at startup; restart OpenCode if it is already running.")
    if args.harness == "cursor":
        extra_actions = install_cursor_assets(source, args.scope, home, project_dir, args.dry_run)
        for action in extra_actions:
            print(f"Installed {action}" if not args.dry_run else f"Dry run: would install {action}")
        if extra_actions:
            print("Cursor slash command: /ultimate-de-slop (restart or reload Cursor rules if needed).")

    scripts_dir = target / "scripts"
    runner_harness = layout["runner_harness"]
    print("Invocation:")
    if runner_harness:
        print(f"  {scripts_dir / 'deslop-review.sh'}")
        print(f"  {scripts_dir / 'deslop-loop.sh'} --max-iterations 5 --priority P0,P1")
        print(f"  Harness auto-detected as {runner_harness} from the install marker (override with DESLOP_HARNESS).")
    else:
        print(f"  Shared fallback installed at {target}; run with a concrete DESLOP_HARNESS adapter.")
    return 1 if plugin_status == "failed" else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install ultimate-de-slop for a harness.")
    parser.add_argument("--harness", required=True, choices=sorted(LAYOUTS))
    parser.add_argument("--scope", choices=["global", "local"], default="global")
    parser.add_argument("--project-dir", default=os.getcwd())
    parser.add_argument("--home", help="Override HOME, useful for tests.")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(install(parse_args()))
