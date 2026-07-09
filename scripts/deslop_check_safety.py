"""Shared allowlist safety checks for deslop-run-checks.sh."""

from __future__ import annotations

import shlex

SAFE_PREFIXES = (
    ("python", "-m", "pytest"),
    ("python3", "-m", "pytest"),
    ("python", "-m", "unittest"),
    ("python3", "-m", "unittest"),
    ("python", "-m", "py_compile"),
    ("python3", "-m", "py_compile"),
    ("pytest",),
    ("ruff", "check"),
    ("mypy",),
    ("lint-imports",),
    ("import-linter",),
    ("uv", "run", "pytest"),
    ("uv", "run", "ruff"),
    ("uv", "run", "mypy"),
    ("uv", "run", "lint-imports"),
    ("uv", "run", "import-linter"),
    ("poetry", "run", "pytest"),
    ("poetry", "run", "ruff"),
    ("poetry", "run", "mypy"),
    ("pipenv", "run", "pytest"),
    ("npm", "--prefix"),
    ("npm", "test"),
    ("npm", "run"),
    ("pnpm", "test"),
    ("pnpm", "run"),
    ("yarn", "test"),
    ("yarn", "run"),
    ("bun", "test"),
    ("cargo", "test"),
    ("go", "test"),
    ("swift", "test"),
    ("git", "diff", "--check"),
    ("git", "status"),
    ("test", "-d"),
    ("test", "-f"),
)
SAFE_ENV = {"CI", "NODE_ENV", "PYTHONPATH", "UV_CACHE_DIR"}


def has_unsafe_shell_syntax(command_text: str) -> bool:
    if "\n" in command_text or "\r" in command_text:
        return True
    for pattern in ("$(", "${", "`"):
        if pattern in command_text:
            return True
    try:
        tokens = shlex.split(command_text)
    except ValueError:
        return True
    for token in tokens:
        if token in {";", "|", "||", "&", "&&", ">", ">>", "<", "<<", "|&"}:
            return True
    return False


def strip_env(tokens: list[str]) -> list[str]:
    rest = list(tokens)
    while rest:
        head = rest[0]
        if "=" not in head or head.startswith("-"):
            break
        name, _value = head.split("=", 1)
        if not name.replace("_", "").isalnum() or name not in SAFE_ENV:
            break
        rest.pop(0)
    return rest


def is_safe_check_command(command_text: str) -> bool:
    value = command_text.strip()
    if not value or has_unsafe_shell_syntax(value):
        return False
    tokens = strip_env(shlex.split(value))
    if not tokens:
        return False
    if tokens[:2] in (["npm", "test"], ["pnpm", "test"], ["yarn", "test"]):
        return len(tokens) == 2
    if tokens[:2] in (["npm", "run"], ["pnpm", "run"], ["yarn", "run"]):
        return len(tokens) == 3 and bool(tokens[2].strip())
    if tokens[:2] in (["npm", "--prefix"], ["pnpm", "--dir"]):
        return len(tokens) == 5 and tokens[3] == "run" and bool(tokens[2].strip()) and bool(tokens[4].strip())
    for prefix in SAFE_PREFIXES:
        if tuple(tokens[: len(prefix)]) == prefix:
            return True
    return False


def inventory_command(item: object) -> str | None:
    if isinstance(item, str):
        value = item.strip()
        return value or None
    if isinstance(item, dict):
        command = item.get("command")
        if isinstance(command, str):
            value = command.strip()
            return value or None
    return None
