#!/usr/bin/env python3
"""OAuth session auth and default-model resolution for harness CLIs."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any


AUTH_ENV = {
    "cursor": ["CURSOR_API_KEY", "CURSOR_AUTH_TOKEN"],
}

OAUTH_LOGIN_COMMANDS = {
    "codex": (["codex", "login", "status"], re.compile(r"logged in", re.I)),
    "cursor": (["cursor-agent", "status"], re.compile(r"logged in", re.I)),
}


def home() -> Path:
    return Path(os.environ.get("HOME", "~")).expanduser()


def run_text(command: list[str], timeout: float = 8.0) -> tuple[int, str]:
    try:
        result = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return 127, ""
    return result.returncode, result.stdout or ""


def auth_env_present(harness: str) -> list[str]:
    names = AUTH_ENV.get(harness, [])
    return [name for name in names if os.environ.get(name)]


def oauth_login_ok(harness: str) -> bool:
    spec = OAUTH_LOGIN_COMMANDS.get(harness)
    if spec is None:
        return False
    command, pattern = spec
    code, output = run_text(command)
    return code == 0 and bool(pattern.search(output))


def auth_status(harness: str, cli: str) -> dict[str, Any]:
    env_keys = auth_env_present(harness)
    if env_keys:
        return {
            "ok": True,
            "mode": "api_key",
            "message": f"Auth env present: {', '.join(env_keys)}",
        }
    if oauth_login_ok(harness):
        return {
            "ok": True,
            "mode": "oauth",
            "message": f"OAuth session active for {harness} (`{cli} login`)",
        }
    if harness in AUTH_ENV:
        keys = ", ".join(AUTH_ENV[harness])
        return {
            "ok": False,
            "mode": "missing",
            "message": (
                f"Missing auth for {harness}. Run `{cli} login` for OAuth "
                f"or set one of {keys}."
            ),
        }
    return {
        "ok": True,
        "mode": "none",
        "message": f"No auth check configured for {harness}; ensure the CLI is logged in.",
    }


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def read_toml_value(path: Path, key: str) -> str | None:
    if not path.is_file():
        return None
    pattern = re.compile(rf'^\s*{re.escape(key)}\s*=\s*"([^"]+)"\s*$', re.M)
    try:
        match = pattern.search(path.read_text())
    except OSError:
        return None
    if not match:
        return None
    value = match.group(1).strip()
    return value or None


def cursor_session_model() -> str | None:
    config = read_json(home() / ".cursor" / "cli-config.json")
    if not config:
        return None
    for key in ("selectedModel", "model"):
        block = config.get(key)
        if isinstance(block, dict):
            model_id = block.get("modelId")
            if isinstance(model_id, str) and model_id.strip():
                return model_id.strip()
    return None


def codex_session_model() -> str | None:
    return read_toml_value(home() / ".codex" / "config.toml", "model")


def opencode_session_model() -> str | None:
    for path in (
        home() / ".config" / "opencode" / "opencode.json",
        home() / ".opencode" / "opencode.json",
    ):
        config = read_json(path)
        if not config:
            continue
        model = config.get("model")
        if isinstance(model, str) and model.strip():
            return model.strip()
    return None


def claude_session_model() -> str | None:
    config = read_json(home() / ".claude" / "settings.json")
    if not config:
        return None
    model = config.get("model")
    if isinstance(model, str) and model.strip():
        return model.strip()
    return None


SESSION_MODEL_RESOLVERS = {
    "claude": claude_session_model,
    "codex": codex_session_model,
    "cursor": cursor_session_model,
    "opencode": opencode_session_model,
}


def oauth_session_model(harness: str) -> str | None:
    resolver = SESSION_MODEL_RESOLVERS.get(harness)
    if resolver is None:
        return None
    return resolver()


def resolve_model(harness: str, explicit_model: str | None = None) -> str | None:
    if explicit_model:
        return explicit_model
    env_names = [
        "DESLOP_MODEL",
        f"DESLOP_{harness.upper().replace('-', '_')}_MODEL",
    ]
    if harness == "codex":
        env_names.append("DESLOP_CODEX_MODEL")
    for env_name in env_names:
        value = os.environ.get(env_name)
        if value:
            return value
    return oauth_session_model(harness)
