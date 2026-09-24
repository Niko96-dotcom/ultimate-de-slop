#!/usr/bin/env python3
"""Run a de-slop agent through a selectable AI harness adapter.

The deterministic de-slop harness owns prompts, schemas, output capture,
timeouts, and state transitions. This runner keeps harness-specific CLI
invocation in one small adapter layer.
"""

from __future__ import annotations

import argparse
import json
import os
import selectors
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from deslop_harness import load_deslop_config, resolve_agent_timeouts
from deslop_harness import resolve_harness
from deslop_oauth import resolve_model


SUPPORTED_HARNESSES = {
    "native",
    "claude",
    "codex",
    "commandcode",
    "cursor",
    "hermes",
    "opencode",
    "openclaw",
    "pi",
}

ROLE_AGENTS = {
    "arbiter": "deslop_arbiter",
    "arbitrate": "deslop_arbiter",
    "fix": "deslop_fixer",
    "fixer": "deslop_fixer",
    "review": "deslop_reviewer",
    "reviewer": "deslop_reviewer",
    "scribe": "deslop_scribe",
    "verify": "deslop_verifier",
    "verifier": "deslop_verifier",
}


@dataclass(frozen=True)
class AdapterCommand:
    harness: str
    cli: str
    command: list[str]
    native_last_message: bool = False
    permission_mode: str | None = None
    unsupported_reason: str | None = None


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_config(root: Path) -> dict[str, Any]:
    return load_deslop_config(root)


def terminate_process_group(process: subprocess.Popen[bytes], grace_seconds: float) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=grace_seconds)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    process.wait(timeout=grace_seconds)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def is_read_only(sandbox: str, kind: str) -> bool:
    if sandbox == "read-only":
        return True
    return kind in {"arbiter", "arbitrate", "review", "reviewer", "verify", "verifier"}


def role_agent(kind: str) -> str:
    return ROLE_AGENTS.get(kind, f"deslop_{kind}")


def skill_root() -> Path:
    return Path(__file__).resolve().parents[1]


def prompt_file_message(prompt: Path) -> str:
    return f"Read and follow the prompt file at {prompt}. Return only the requested JSON."


def add_model(command: list[str], model: str | None) -> None:
    if model:
        command.extend(["--model", model])


def codex_adapter(args: argparse.Namespace, model: str | None) -> AdapterCommand:
    command = ["codex", "exec"]
    if model:
        command.extend(["-m", model])
    effort = os.environ.get("DESLOP_CODEX_REASONING_EFFORT", "").strip().lower()
    if effort:
        if effort not in {"low", "medium", "high", "xhigh", "max", "ultra"}:
            raise ValueError("DESLOP_CODEX_REASONING_EFFORT must be low, medium, high, xhigh, max, or ultra")
        command.extend(["-c", f'model_reasoning_effort="{effort}"'])
    command.extend(
        [
            "--cd",
            str(args.root),
            "--sandbox",
            "read-only" if is_read_only(args.sandbox, args.kind) else args.sandbox,
            "--output-schema",
            str(args.schema),
            "--output-last-message",
            str(args.last_message),
        ]
    )
    for directory in args.add_dir:
        command.extend(["--add-dir", directory])
    command.append("-")
    return AdapterCommand("codex", "codex", command, native_last_message=True)


def claude_adapter(args: argparse.Namespace, model: str | None) -> AdapterCommand:
    permission = "plan" if is_read_only(args.sandbox, args.kind) else (args.permission_mode or "acceptEdits")
    schema_text = args.schema.read_text()
    command = [
        "claude",
        "-p",
        "--input-format",
        "text",
        "--output-format",
        "json",
        "--json-schema",
        schema_text,
    ]
    add_model(command, model)
    command.extend(["--permission-mode", permission])
    if is_read_only(args.sandbox, args.kind):
        command.extend(["--tools", "Read,Glob,Grep"])
    for directory in args.add_dir:
        command.extend(["--add-dir", directory])
    return AdapterCommand("claude", "claude", command, permission_mode=permission)


def child_environment(args: argparse.Namespace, adapter: AdapterCommand) -> dict[str, str]:
    env = os.environ.copy()
    if adapter.harness == "opencode" and is_read_only(args.sandbox, args.kind):
        # Supply the actual role policy at invocation time: merely checking a
        # bundled template says nothing about the user's installed agent.
        raw = env.get("OPENCODE_CONFIG_CONTENT", "{}")
        config = json.loads(raw)
        if not isinstance(config, dict):
            raise ValueError("OPENCODE_CONFIG_CONTENT must be an object")
        # A trailing wildcard deny can override the explicit read grants when
        # OpenCode merges global and agent rules. Deny concrete side-effecting
        # tools instead so reviewers can actually inspect the repository.
        permissions = {"read": "allow", "glob": "allow", "grep": "allow", "list": "allow",
                       "edit": "deny", "bash": "deny", "task": "deny", "skill": "deny",
                       "webfetch": "deny", "websearch": "deny", "lsp": "deny",
                       "todowrite": "deny", "question": "deny"}
        config["permission"] = permissions
        agents = config.setdefault("agent", {})
        role = agents.setdefault(role_agent(args.kind), {})
        role["permission"] = permissions.copy()
        role["mode"] = "primary"
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(config)
        env.pop("OPENCODE_PERMISSION", None)
    return env


def opencode_adapter(args: argparse.Namespace, model: str | None) -> AdapterCommand:
    agent = role_agent(args.kind)
    permission_mode = "runtime-deny-mutations" if is_read_only(args.sandbox, args.kind) else f"agent:{agent}"
    command = [
        "opencode",
        "run",
        "--dir",
        str(args.root),
        "--agent",
        agent,
        "--format",
        "json",
        "--file",
        str(args.prompt),
    ]
    add_model(command, model)
    command.append(prompt_file_message(args.prompt))
    return AdapterCommand("opencode", "opencode", command, permission_mode=permission_mode)


def cursor_adapter(args: argparse.Namespace, model: str | None) -> AdapterCommand:
    mode = "ask" if is_read_only(args.sandbox, args.kind) else args.permission_mode
    command = [
        "cursor-agent",
        "--print",
        "--trust",
        "--workspace",
        str(args.root),
        "--output-format",
        "json",
    ]
    if mode:
        command.extend(["--mode", mode])
    # Read-only roles never get --force, even under danger-full-access.
    if not is_read_only(args.sandbox, args.kind):
        command.append("--force")
    add_model(command, model)
    command.append(prompt_file_message(args.prompt))
    return AdapterCommand("cursor", "cursor-agent", command, permission_mode=mode)


def pi_adapter(args: argparse.Namespace, model: str | None) -> AdapterCommand:
    if is_read_only(args.sandbox, args.kind):
        tools = "read,grep,find,ls"
    else:
        tools = "read,grep,find,ls,write,edit,bash"
    command = [
        "pi",
        "--print",
        "--mode",
        "json",
        "--thinking",
        "off",
        "--skill",
        str(skill_root()),
        "--tools",
        tools,
        f"@{args.prompt}",
        prompt_file_message(args.prompt),
    ]
    add_model(command, model)
    return AdapterCommand("pi", "pi", command, permission_mode=tools)


def commandcode_adapter(args: argparse.Namespace, model: str | None) -> AdapterCommand:
    command = ["commandcode", "--print", "--trust", "--skip-onboarding"]
    permission = args.permission_mode
    if is_read_only(args.sandbox, args.kind):
        command.append("--plan")
        permission = permission or "plan"
    else:
        permission = permission or ("auto-accept" if args.sandbox == "workspace-write" else "standard")
        command.extend(["--permission-mode", permission])
        if args.sandbox in {"workspace-write", "danger-full-access"} or os.environ.get("DESLOP_COMMANDCODE_YOLO") == "1":
            command.append("--yolo")
    for directory in args.add_dir:
        command.extend(["--add-dir", directory])
    add_model(command, model)
    command.append(prompt_file_message(args.prompt))
    return AdapterCommand("commandcode", "commandcode", command, permission_mode=permission)


def hermes_adapter(args: argparse.Namespace, model: str | None) -> AdapterCommand:
    read_only = is_read_only(args.sandbox, args.kind)
    if read_only:
        toolsets = "read,grep,find,ls"
    else:
        toolsets = "read,grep,find,ls,write,edit,bash"
    command = ["hermes", "--skills", "ultimate-de-slop", "--toolsets", toolsets]
    # Read-only roles never get --yolo, even under danger-full-access.
    if not read_only and args.sandbox == "danger-full-access" and os.environ.get("DESLOP_HERMES_YOLO") == "1":
        command.append("--yolo")
    add_model(command, model)
    command.extend(["-z", prompt_file_message(args.prompt)])
    return AdapterCommand("hermes", "hermes", command, permission_mode=toolsets)


def openclaw_adapter(args: argparse.Namespace, model: str | None) -> AdapterCommand:
    command = ["openclaw", "agent", "--workspace", str(args.root), "--skill", "ultimate-de-slop"]
    add_model(command, model)
    return AdapterCommand(
        "openclaw",
        "openclaw",
        command,
        unsupported_reason=(
            "The OpenClaw noninteractive schema-output invocation is not confirmed. "
            "This adapter intentionally fails instead of pretending a safe local agent run succeeded. "
            "Settle the exact OpenClaw CLI contract, then update scripts/deslop-agent-runner.py."
        ),
    )


def native_adapter(args: argparse.Namespace, model: str | None) -> AdapterCommand:
    command = [sys.executable, str(skill_root() / "scripts/deslop-cloud.py"), "request",
               "--prompt", str(args.prompt), "--schema", str(args.schema), "--kind", args.kind,
               "--timeout", str(args.timeout_seconds or 5400)]
    if is_read_only(args.sandbox, args.kind):
        command.append("--readonly")
    return AdapterCommand("native", sys.executable, command, permission_mode="host-native-subagent")


ADAPTERS = {
    "native": native_adapter,
    "claude": claude_adapter,
    "codex": codex_adapter,
    "commandcode": commandcode_adapter,
    "cursor": cursor_adapter,
    "hermes": hermes_adapter,
    "opencode": opencode_adapter,
    "openclaw": openclaw_adapter,
    "pi": pi_adapter,
}


def build_adapter(args: argparse.Namespace) -> AdapterCommand:
    harness = args.harness.lower()
    if harness not in SUPPORTED_HARNESSES:
        supported = ", ".join(sorted(SUPPORTED_HARNESSES))
        raise SystemExit(f"unsupported DESLOP_HARNESS={args.harness!r}; expected one of: {supported}")
    model = resolve_model(harness, args.model)
    return ADAPTERS[harness](args, model)


def extract_event_text(raw_output: Path) -> str:
    if not raw_output.exists():
        return ""
    delta_parts: list[str] = []
    complete_messages: list[str] = []
    for line in raw_output.read_text(errors="ignore").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        part = event.get("part")
        if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
            delta_parts.append(part["text"])
        elif event.get("type") == "text" and isinstance(event.get("text"), str):
            delta_parts.append(event["text"])

        result = event.get("result")
        if isinstance(result, str) and event.get("type") == "result":
            complete_messages.append(result)

        message = event.get("message")
        if isinstance(message, dict) and message.get("role") == "assistant":
            text = "".join(
                item.get("text", "")
                for item in message.get("content", [])
                if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str)
            )
            if text and event.get("type") in {"message_end", "turn_end"}:
                complete_messages.append(text)

        assistant_event = event.get("assistantMessageEvent")
        if isinstance(assistant_event, dict) and assistant_event.get("type") == "text_delta":
            delta = assistant_event.get("delta")
            if isinstance(delta, str):
                delta_parts.append(delta)

    if complete_messages:
        return complete_messages[-1]
    return "".join(delta_parts)


def copy_raw_to_last_message(raw_output: Path, last_message: Path, harness: str) -> None:
    if last_message.exists() and last_message.stat().st_size > 0:
        return
    if harness in {"opencode", "pi", "commandcode", "cursor", "claude"}:
        text = extract_event_text(raw_output)
        if text:
            write_text(last_message, text)
            return
    text = raw_output.read_text(errors="ignore") if raw_output.exists() else ""
    write_text(last_message, text)


def opencode_review_source_reads(raw_output: Path, root: Path, partition: str,
                                 prompt: Path) -> int:
    """Count completed source-file reads in the requested review scope."""
    scope = (root / partition).resolve()
    count = 0
    for line in raw_output.read_text(errors="ignore").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "tool_use":
            continue
        part = event.get("part")
        if not isinstance(part, dict) or part.get("tool") != "read":
            continue
        state = part.get("state")
        if not isinstance(state, dict) or state.get("status") != "completed":
            continue
        tool_input = state.get("input")
        path_text = tool_input.get("filePath") if isinstance(tool_input, dict) else None
        if not isinstance(path_text, str) or not path_text:
            continue
        path = Path(path_text).resolve()
        if (path == prompt.resolve() or not path.is_relative_to(root)
                or not path.is_relative_to(scope)):
            continue
        relative = path.relative_to(root)
        if relative.parts and relative.parts[0] in {".deslop", ".opencode"}:
            continue
        if state.get("output"):
            count += 1
    return count


def base_diagnostic(args: argparse.Namespace, adapter: AdapterCommand) -> dict[str, Any]:
    return {
        "add_dirs": args.add_dir,
        "command": adapter.command,
        "ended_at": None,
        "exit_code": None,
        "harness": adapter.harness,
        "idle_timed_out": False,
        "kind": args.kind,
        "last_message": str(args.last_message),
        "missing_cli": False,
        "model": resolve_model(adapter.harness, args.model),
        "native_last_message": adapter.native_last_message,
        "permission_mode": adapter.permission_mode,
        "prompt": str(args.prompt),
        "raw_output": str(args.raw_output),
        "root": str(args.root),
        "sandbox": args.sandbox,
        "schema": str(args.schema),
        "started_at": now(),
        "status": "starting",
        "timed_out": False,
        "timeout_seconds": args.timeout_seconds,
        "idle_timeout_seconds": args.idle_timeout_seconds,
        "idle_timeout_source": getattr(args, "idle_timeout_source", "config"),
        "wall_timed_out": False,
    }


def run_process(args: argparse.Namespace, adapter: AdapterCommand, diagnostic: dict[str, Any]) -> int:
    args.raw_output.parent.mkdir(parents=True, exist_ok=True)
    args.last_message.parent.mkdir(parents=True, exist_ok=True)
    args.runner_json.parent.mkdir(parents=True, exist_ok=True)

    from deslop_snapshot import snapshot
    artifacts = {str(p.resolve().relative_to(args.root)) for p in
                 (args.raw_output, args.last_message, args.runner_json) if p.resolve().is_relative_to(args.root)}
    if adapter.harness == "opencode":
        # OpenCode's local goals extension writes this provider-owned state on
        # read-only calls. Keep tracked copies subject to the mutation check.
        provider_state = ".opencode/goals/state.json"
        tracked = subprocess.run(
            ["git", "ls-files", "--cached", "-z", "--", provider_state],
            cwd=args.root, check=True, stdout=subprocess.PIPE,
        ).stdout
        if not tracked:
            artifacts.add(provider_state)
    def content_snapshot():
        return {p: digest for p, digest in snapshot(args.root).items() if p not in artifacts}
    before_content = content_snapshot() if is_read_only(args.sandbox, args.kind) else None
    control_paths = [args.root / ".deslop" / name for name in
                     ("config.json", "state.json", "findings.jsonl", "inventory.json", "index.md")]
    before_control = {p: p.read_bytes() if p.exists() else None for p in control_paths}
    started = time.monotonic()
    last_output = started
    with args.prompt.open("rb") as prompt_handle, args.raw_output.open("wb") as raw_handle:
        process = subprocess.Popen(
            adapter.command,
            cwd=args.root,
            stdin=prompt_handle,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=child_environment(args, adapter),
        )
        previous_handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
        def forward_stop(signum, _frame):
            terminate_process_group(process, args.grace_seconds)
        for sig in previous_handlers:
            signal.signal(sig, forward_stop)
        diagnostic["status"] = "running"
        selector = selectors.DefaultSelector()
        assert process.stdout is not None
        selector.register(process.stdout, selectors.EVENT_READ)

        status = "completed"
        exit_code: int | None = None
        try:
            while True:
                for key, _ in selector.select(timeout=0.2):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if chunk:
                        raw_handle.write(chunk)
                        raw_handle.flush()
                        last_output = time.monotonic()
                    else:
                        selector.unregister(key.fileobj)

                exit_code = process.poll()
                if exit_code is not None:
                    remaining = process.stdout.read() if process.stdout else b""
                    if remaining:
                        raw_handle.write(remaining)
                        raw_handle.flush()
                    break

                elapsed = time.monotonic() - started
                idle = time.monotonic() - last_output
                if args.timeout_seconds and elapsed > args.timeout_seconds:
                    status = "wall_timeout"
                    diagnostic["timed_out"] = True
                    diagnostic["wall_timed_out"] = True
                    raw_handle.write(
                        f"\n[deslop-agent-runner] wall timeout after {elapsed:.1f}s\n".encode()
                    )
                    raw_handle.flush()
                    terminate_process_group(process, args.grace_seconds)
                    exit_code = 124
                    break
                if args.idle_timeout_seconds and idle > args.idle_timeout_seconds:
                    status = "idle_timeout"
                    diagnostic["timed_out"] = True
                    diagnostic["idle_timed_out"] = True
                    idle_source = getattr(args, "idle_timeout_source", "config")
                    raw_handle.write(
                        (
                            f"\n[deslop-agent-runner] idle timeout after {idle:.1f}s without stdout "
                            f"(policy={idle_source}; child still running). "
                            "For buffering harnesses, idle is disabled by default; "
                            "raise DESLOP_IDLE_TIMEOUT_SECONDS or agent_idle_timeout_seconds to override.\n"
                        ).encode()
                    )
                    raw_handle.flush()
                    terminate_process_group(process, args.grace_seconds)
                    exit_code = 125
                    break
        finally:
            selector.close()
            for sig, handler in previous_handlers.items():
                signal.signal(sig, handler)

    if exit_code is None:
        exit_code = process.returncode
    if status == "completed" and exit_code:
        status = "failed"
    changed_control = [p for p, old in before_control.items()
                       if (p.read_bytes() if p.exists() else None) != old]
    if changed_control:
        for p in changed_control:
            if p.exists():
                (args.runner_json.parent / ("unauthorized-" + p.name)).write_bytes(p.read_bytes())
            if before_control[p] is None:
                p.unlink(missing_ok=True)
            else:
                p.write_bytes(before_control[p])
        status, exit_code = "control_state_mutation", 1
        diagnostic["unauthorized_control_paths"] = [str(p) for p in changed_control]
    if before_content is not None and content_snapshot() != before_content:
        status, exit_code = "read_only_mutation", 1
    if (adapter.harness == "opencode" and args.kind in {"review", "reviewer"}
            and status == "completed" and not exit_code):
        reads = opencode_review_source_reads(args.raw_output, args.root,
                                             args.review_partition or ".", args.prompt)
        diagnostic["review_source_reads"] = reads
        if reads == 0:
            status, exit_code = "review_uninspected", 1
    copy_raw_to_last_message(args.raw_output, args.last_message, adapter.harness)
    diagnostic.update(
        {
            "duration_seconds": round(time.monotonic() - started, 3),
            "ended_at": now(),
            "exit_code": exit_code,
            "last_message_exists": args.last_message.exists(),
            "raw_output_bytes": args.raw_output.stat().st_size if args.raw_output.exists() else 0,
            "status": status,
        }
    )
    return int(exit_code or 0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a de-slop agent through a harness adapter.")
    parser.add_argument("--root", "--cwd", dest="root", type=Path, required=True)
    parser.add_argument("--prompt", type=Path, required=True)
    parser.add_argument("--raw-output", type=Path, required=True)
    parser.add_argument("--last-message", type=Path, required=True)
    parser.add_argument("--runner-json", type=Path, required=True)
    parser.add_argument("--schema", type=Path, required=True)
    parser.add_argument("--sandbox", required=True, choices=["read-only", "workspace-write", "danger-full-access"])
    parser.add_argument("--kind", default="agent")
    parser.add_argument("--review-partition", default="")
    parser.add_argument("--harness")
    parser.add_argument("--permission-mode")
    parser.add_argument("--model")
    parser.add_argument("--add-dir", action="append", default=[])
    args = parser.parse_args()
    args.root = args.root.resolve()
    args.harness = resolve_harness(
        explicit=args.harness,
        script_dir=Path(__file__).resolve().parent,
    )
    timeouts = resolve_agent_timeouts(
        args.root,
        harness=args.harness,
        kind=args.kind,
        script_dir=Path(__file__).resolve().parent,
    )
    args.timeout_seconds = float(timeouts["timeout_seconds"])
    args.idle_timeout_seconds = float(timeouts["idle_timeout_seconds"])
    args.grace_seconds = float(timeouts["grace_seconds"])
    args.idle_timeout_source = str(timeouts.get("idle_timeout_source", "config"))
    return args


def main() -> int:
    args = parse_args()
    adapter = build_adapter(args)
    diagnostic = base_diagnostic(args, adapter)

    if shutil.which(adapter.cli) is None:
        message = (
            f"deslop-agent-runner: {adapter.cli} CLI not found on PATH for "
            f"DESLOP_HARNESS={adapter.harness}\n"
        )
        diagnostic.update(
            {
                "ended_at": now(),
                "exit_code": 127,
                "missing_cli": True,
                "status": f"{adapter.harness}_not_found",
            }
        )
        write_text(args.raw_output, message)
        write_text(args.last_message, message)
        write_json(args.runner_json, diagnostic)
        return 127

    if adapter.unsupported_reason:
        message = f"deslop-agent-runner: {adapter.harness} adapter unsupported: {adapter.unsupported_reason}\n"
        diagnostic.update(
            {
                "ended_at": now(),
                "exit_code": 126,
                "status": f"{adapter.harness}_unsupported",
                "unsupported_reason": adapter.unsupported_reason,
            }
        )
        write_text(args.raw_output, message)
        write_text(args.last_message, message)
        write_json(args.runner_json, diagnostic)
        return 126

    code = run_process(args, adapter, diagnostic)
    write_json(args.runner_json, diagnostic)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
