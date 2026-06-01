from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPT_DIR = SKILL_DIR / "scripts"


def run(
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(
        args,
        cwd=cwd,
        env=merged_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    )


def write_executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


def init_repo(root: Path) -> None:
    run(["git", "init"], cwd=root, check=True)
    run(["git", "config", "user.email", "deslop@example.invalid"], cwd=root, check=True)
    run(["git", "config", "user.name", "Deslop Test"], cwd=root, check=True)
    (root / ".deslop").mkdir()
    (root / ".deslop" / "runs").mkdir()
    (root / ".deslop" / "config.json").write_text(
        json.dumps(
            {
                "max_fix_attempts": 3,
                "max_changed_files_per_fix": 8,
                "max_changed_lines_per_fix": 400,
            }
        )
        + "\n"
    )
    (root / ".deslop" / "inventory.json").write_text('{"detected_commands": []}\n')


def write_findings(root: Path, *findings: dict[str, object]) -> None:
    (root / ".deslop" / "findings.jsonl").write_text(
        "".join(json.dumps(finding, sort_keys=True) + "\n" for finding in findings)
    )


def read_finding(root: Path, finding_id: str) -> dict[str, object]:
    for line in (root / ".deslop" / "findings.jsonl").read_text().splitlines():
        finding = json.loads(line)
        if finding.get("id") == finding_id:
            return finding
    raise AssertionError(f"finding not found: {finding_id}")


def latest_run_file(root: Path, suffix: str, finding_id: str, filename: str) -> Path:
    matches = sorted((root / ".deslop" / "runs").glob(f"*-{suffix}-{finding_id}/{filename}"))
    if not matches:
        raise AssertionError(f"no {filename} found for {suffix} {finding_id}")
    return matches[-1]


def minimal_finding(
    finding_id: str,
    *,
    expected_checks: list[str] | None = None,
    files: list[str] | None = None,
) -> dict[str, object]:
    return {
        "acceptance_criteria": ["test criterion"],
        "attempts": 0,
        "category": "test",
        "confidence": 0.99,
        "created_at": "2026-05-31T00:00:00Z",
        "dependencies": [],
        "estimated_effort": "small",
        "evidence": [{"claim": "test evidence", "file": "sample.py", "lines": "1"}],
        "expected_checks": expected_checks or [],
        "files": files or ["sample.py"],
        "id": finding_id,
        "proposed_fix": "test fix",
        "reviewer": "test",
        "risk": "low",
        "severity": "P1",
        "status": "accepted",
        "title": "Test finding",
        "updated_at": "2026-05-31T00:00:00Z",
        "why_it_matters": "test why",
    }


class HarnessTests(unittest.TestCase):
    def make_repo(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        tempdir = tempfile.TemporaryDirectory()
        root = Path(tempdir.name)
        init_repo(root)
        return tempdir, root

    def test_safe_expected_checks_run_without_inventory_allowlist(self) -> None:
        tempdir, root = self.make_repo()
        with tempdir:
            (root / "sample.py").write_text("value = 1\n")
            write_findings(
                root,
                minimal_finding(
                    "DSL-000001",
                    expected_checks=["python3 -m py_compile sample.py"],
                ),
            )

            result = run([str(SCRIPT_DIR / "deslop-run-checks.sh"), "DSL-000001"], cwd=root)

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            checks_path = sorted((root / ".deslop" / "runs").glob("*-checks-DSL-000001/checks.json"))[-1]
            checks = json.loads(checks_path.read_text())
            self.assertEqual(checks["status"], "passed")
            self.assertEqual(checks["results"][0]["status"], "passed")

    def test_unsafe_expected_checks_stay_blocked(self) -> None:
        tempdir, root = self.make_repo()
        with tempdir:
            sentinel = root / "sentinel.txt"
            sentinel.write_text("still here\n")
            write_findings(
                root,
                minimal_finding("DSL-000001", expected_checks=["rm -rf sentinel.txt"]),
            )

            result = run([str(SCRIPT_DIR / "deslop-run-checks.sh"), "--no-fail", "DSL-000001"], cwd=root)

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertEqual(sentinel.read_text(), "still here\n")
            checks_path = sorted((root / ".deslop" / "runs").glob("*-checks-DSL-000001/checks.json"))[-1]
            checks = json.loads(checks_path.read_text())
            self.assertEqual(checks["status"], "failed")
            self.assertEqual(checks["results"][0]["status"], "blocked")

    def test_fix_does_not_treat_preexisting_dirty_tree_as_success(self) -> None:
        tempdir, root = self.make_repo()
        with tempdir:
            (root / "sample.py").write_text("value = 1\n")
            run(["git", "add", "."], cwd=root, check=True)
            run(["git", "commit", "-m", "initial"], cwd=root, check=True)
            (root / "unrelated.txt").write_text("pre-existing dirty state\n")
            write_findings(root, minimal_finding("DSL-000001"))

            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            fake_codex = fake_bin / "codex"
            fake_codex.write_text(
                """#!/usr/bin/env python3
import json
import sys
from pathlib import Path

payload = {
    "finding_id": "DSL-000001",
    "summary": "reported success without edits",
    "changed_files": [],
    "checks_run": [],
    "risks": [],
    "status": "fixed",
}
text = json.dumps(payload)
if "--output-last-message" in sys.argv:
    Path(sys.argv[sys.argv.index("--output-last-message") + 1]).write_text(text + "\\n")
print(text)
"""
            )
            fake_codex.chmod(0o755)

            result = run(
                [str(SCRIPT_DIR / "deslop-fix.sh"), "--allow-dirty", "DSL-000001"],
                cwd=root,
                env={"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"},
            )

            self.assertNotEqual(result.returncode, 0)
            finding = read_finding(root, "DSL-000001")
            self.assertEqual(finding["status"], "blocked")
            self.assertIn("no changed files", str(finding.get("block_reason", "")))

    def test_codex_runner_records_timeout(self) -> None:
        tempdir, root = self.make_repo()
        with tempdir:
            prompt = root / "prompt.txt"
            prompt.write_text("Return JSON.\n")
            raw = root / "raw.txt"
            last = root / "last.txt"
            runner_json = root / "runner.json"

            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            fake_codex = fake_bin / "codex"
            fake_codex.write_text("#!/usr/bin/env bash\nsleep 5\n")
            fake_codex.chmod(0o755)

            result = run(
                [
                    sys.executable,
                    str(SCRIPT_DIR / "deslop-codex-runner.py"),
                    "--root",
                    str(root),
                    "--prompt",
                    str(prompt),
                    "--raw-output",
                    str(raw),
                    "--last-message",
                    str(last),
                    "--runner-json",
                    str(runner_json),
                    "--schema",
                    str(SKILL_DIR / "references" / "fix.schema.json"),
                    "--sandbox",
                    "read-only",
                ],
                cwd=root,
                env={
                    "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                    "DESLOP_CODEX_TIMEOUT_SECONDS": "0.2",
                    "DESLOP_CODEX_IDLE_TIMEOUT_SECONDS": "10",
                },
            )

            self.assertEqual(result.returncode, 124)
            diagnostics = json.loads(runner_json.read_text())
            self.assertEqual(diagnostics["status"], "wall_timeout")
            self.assertIs(diagnostics["timed_out"], True)

    def test_agent_runner_constructs_opencode_command(self) -> None:
        tempdir, root = self.make_repo()
        with tempdir:
            prompt = root / "prompt.txt"
            prompt.write_text("Return JSON.\n")
            raw = root / "raw.txt"
            last = root / "last.txt"
            runner_json = root / "runner.json"

            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            write_executable(
                fake_bin / "opencode",
                """#!/usr/bin/env python3
import json
import sys

payload = {"ok": True, "argv": sys.argv[1:], "stdin": sys.stdin.read()}
print(json.dumps(payload))
""",
            )

            result = run(
                [
                    sys.executable,
                    str(SCRIPT_DIR / "deslop-agent-runner.py"),
                    "--root",
                    str(root),
                    "--prompt",
                    str(prompt),
                    "--raw-output",
                    str(raw),
                    "--last-message",
                    str(last),
                    "--runner-json",
                    str(runner_json),
                    "--schema",
                    str(SKILL_DIR / "references" / "review.schema.json"),
                    "--sandbox",
                    "read-only",
                    "--kind",
                    "review",
                ],
                cwd=root,
                env={
                    "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                    "DESLOP_HARNESS": "opencode",
                    "DESLOP_MODEL": "test-model",
                },
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            diagnostics = json.loads(runner_json.read_text())
            self.assertEqual(diagnostics["harness"], "opencode")
            self.assertEqual(diagnostics["status"], "completed")
            self.assertEqual(diagnostics["model"], "test-model")
            command = diagnostics["command"]
            self.assertEqual(command[:2], ["opencode", "run"])
            self.assertIn("--dir", command)
            self.assertIn(str(root.resolve()), command)
            self.assertIn("--agent", command)
            self.assertIn("deslop_reviewer", command)
            self.assertEqual(last.read_text(), raw.read_text())

    def test_agent_runner_records_missing_cli(self) -> None:
        tempdir, root = self.make_repo()
        with tempdir:
            prompt = root / "prompt.txt"
            prompt.write_text("Return JSON.\n")
            raw = root / "raw.txt"
            last = root / "last.txt"
            runner_json = root / "runner.json"
            fake_bin = root / "fake-bin"
            fake_bin.mkdir()

            result = run(
                [
                    sys.executable,
                    str(SCRIPT_DIR / "deslop-agent-runner.py"),
                    "--root",
                    str(root),
                    "--prompt",
                    str(prompt),
                    "--raw-output",
                    str(raw),
                    "--last-message",
                    str(last),
                    "--runner-json",
                    str(runner_json),
                    "--schema",
                    str(SKILL_DIR / "references" / "review.schema.json"),
                    "--sandbox",
                    "read-only",
                    "--kind",
                    "review",
                ],
                cwd=root,
                env={"PATH": str(fake_bin), "DESLOP_HARNESS": "claude"},
            )

            self.assertEqual(result.returncode, 127)
            diagnostics = json.loads(runner_json.read_text())
            self.assertEqual(diagnostics["status"], "claude_not_found")
            self.assertIs(diagnostics["missing_cli"], True)
            self.assertIn("claude CLI not found", raw.read_text())
            self.assertEqual(last.read_text(), raw.read_text())

    def test_agent_runner_records_idle_timeout(self) -> None:
        tempdir, root = self.make_repo()
        with tempdir:
            prompt = root / "prompt.txt"
            prompt.write_text("Return JSON.\n")
            raw = root / "raw.txt"
            last = root / "last.txt"
            runner_json = root / "runner.json"

            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            write_executable(
                fake_bin / "codex",
                """#!/usr/bin/env python3
import sys
import time

print("starting", flush=True)
time.sleep(5)
""",
            )

            result = run(
                [
                    sys.executable,
                    str(SCRIPT_DIR / "deslop-agent-runner.py"),
                    "--root",
                    str(root),
                    "--prompt",
                    str(prompt),
                    "--raw-output",
                    str(raw),
                    "--last-message",
                    str(last),
                    "--runner-json",
                    str(runner_json),
                    "--schema",
                    str(SKILL_DIR / "references" / "fix.schema.json"),
                    "--sandbox",
                    "read-only",
                    "--kind",
                    "review",
                ],
                cwd=root,
                env={
                    "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                    "DESLOP_HARNESS": "codex",
                    "DESLOP_TIMEOUT_SECONDS": "10",
                    "DESLOP_IDLE_TIMEOUT_SECONDS": "0.2",
                },
            )

            self.assertEqual(result.returncode, 125)
            diagnostics = json.loads(runner_json.read_text())
            self.assertEqual(diagnostics["status"], "idle_timeout")
            self.assertIs(diagnostics["idle_timed_out"], True)
            self.assertIn("idle timeout", raw.read_text())

    def test_review_extracts_json_from_raw_when_last_message_is_not_json(self) -> None:
        tempdir, root = self.make_repo()
        with tempdir:
            (root / "sample.py").write_text("value = 1\n")
            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            write_executable(
                fake_bin / "codex",
                """#!/usr/bin/env python3
import json
import sys
from pathlib import Path

payload = {
    "repo_summary": "empty test review",
    "review_wave_id": "wave-test",
    "partitions_reviewed": ["root"],
    "findings": [],
}
if "--output-last-message" in sys.argv:
    Path(sys.argv[sys.argv.index("--output-last-message") + 1]).write_text("not json\\n")
print("prefix")
print(json.dumps(payload))
print("suffix")
""",
            )

            result = run(
                [str(SCRIPT_DIR / "deslop-review.sh")],
                cwd=root,
                env={"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"},
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            review_paths = sorted((root / ".deslop" / "runs").glob("*-review/review.json"))
            self.assertTrue(review_paths)
            review = json.loads(review_paths[-1].read_text())
            self.assertEqual(review["review_wave_id"], "wave-test")

    def test_codex_runner_shim_keeps_codex_exec_contract(self) -> None:
        tempdir, root = self.make_repo()
        with tempdir:
            prompt = root / "prompt.txt"
            prompt.write_text("Return JSON.\n")
            raw = root / "raw.txt"
            last = root / "last.txt"
            runner_json = root / "runner.json"
            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            write_executable(
                fake_bin / "codex",
                """#!/usr/bin/env python3
import json
import sys
from pathlib import Path

if "--output-last-message" in sys.argv:
    Path(sys.argv[sys.argv.index("--output-last-message") + 1]).write_text("{}\\n")
print(json.dumps({"argv": sys.argv[1:]}))
""",
            )

            result = run(
                [
                    sys.executable,
                    str(SCRIPT_DIR / "deslop-codex-runner.py"),
                    "--root",
                    str(root),
                    "--prompt",
                    str(prompt),
                    "--raw-output",
                    str(raw),
                    "--last-message",
                    str(last),
                    "--runner-json",
                    str(runner_json),
                    "--schema",
                    str(SKILL_DIR / "references" / "fix.schema.json"),
                    "--sandbox",
                    "workspace-write",
                    "--kind",
                    "fix",
                    "--add-dir",
                    str(root / ".deslop"),
                ],
                cwd=root,
                env={
                    "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                    "DESLOP_HARNESS": "claude",
                    "DESLOP_CODEX_MODEL": "legacy-model",
                },
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            diagnostics = json.loads(runner_json.read_text())
            command = diagnostics["command"]
            self.assertEqual(diagnostics["harness"], "codex")
            self.assertEqual(command[:4], ["codex", "exec", "-m", "legacy-model"])
            self.assertIn("--output-schema", command)
            self.assertIn("--output-last-message", command)
            self.assertIn("--add-dir", command)

    def test_openclaw_adapter_fails_with_tested_actionable_mode_when_cli_exists(self) -> None:
        tempdir, root = self.make_repo()
        with tempdir:
            prompt = root / "prompt.txt"
            prompt.write_text("Return JSON.\n")
            raw = root / "raw.txt"
            last = root / "last.txt"
            runner_json = root / "runner.json"
            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            write_executable(fake_bin / "openclaw", "#!/usr/bin/env bash\nexit 99\n")

            result = run(
                [
                    sys.executable,
                    str(SCRIPT_DIR / "deslop-agent-runner.py"),
                    "--root",
                    str(root),
                    "--prompt",
                    str(prompt),
                    "--raw-output",
                    str(raw),
                    "--last-message",
                    str(last),
                    "--runner-json",
                    str(runner_json),
                    "--schema",
                    str(SKILL_DIR / "references" / "review.schema.json"),
                    "--sandbox",
                    "read-only",
                    "--kind",
                    "review",
                ],
                cwd=root,
                env={"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}", "DESLOP_HARNESS": "openclaw"},
            )

            self.assertEqual(result.returncode, 126)
            diagnostics = json.loads(runner_json.read_text())
            self.assertEqual(diagnostics["status"], "openclaw_unsupported")
            self.assertIn("not confirmed", diagnostics["unsupported_reason"])
            self.assertIn("intentionally fails", raw.read_text())

    def test_installer_local_copy_excludes_runtime_and_caches(self) -> None:
        tempdir, root = self.make_repo()
        with tempdir:
            result = run(
                [
                    str(SCRIPT_DIR / "install" / "install-claude.sh"),
                    "--scope",
                    "local",
                    "--project-dir",
                    str(root),
                    "--home",
                    str(root / "home"),
                ],
                cwd=root,
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            target = root / ".claude" / "skills" / "ultimate-de-slop"
            self.assertTrue((target / "SKILL.md").exists())
            self.assertTrue((target / "scripts" / "deslop-agent-runner.py").exists())
            self.assertFalse((target / ".deslop").exists())
            self.assertFalse((target / "tests" / "__pycache__").exists())
            self.assertIn("DESLOP_HARNESS=claude", result.stdout)

    def test_installer_dry_run_does_not_write_home(self) -> None:
        tempdir, root = self.make_repo()
        with tempdir:
            home = root / "home"
            result = run(
                [
                    str(SCRIPT_DIR / "install" / "install-cursor.sh"),
                    "--dry-run",
                    "--home",
                    str(home),
                ],
                cwd=root,
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertIn("Dry run", result.stdout)
            self.assertFalse((home / ".cursor").exists())

    def test_runner_output_schemas_are_strict_compatible(self) -> None:
        schema_names = [
            "arbiter.schema.json",
            "fix.schema.json",
            "review.schema.json",
            "verify.schema.json",
        ]

        def visit_schema_objects(schema: dict[str, object]):
            stack = [schema]
            while stack:
                current = stack.pop()
                if not isinstance(current, dict):
                    continue
                if current.get("type") == "object" and "properties" in current:
                    yield current
                for key in ("properties", "$defs"):
                    children = current.get(key)
                    if isinstance(children, dict):
                        stack.extend(item for item in children.values() if isinstance(item, dict))
                items = current.get("items")
                if isinstance(items, dict):
                    stack.append(items)

        for schema_name in schema_names:
            with self.subTest(schema=schema_name):
                schema = json.loads((SKILL_DIR / "references" / schema_name).read_text())
                for obj in visit_schema_objects(schema):
                    properties = obj.get("properties")
                    self.assertIsInstance(properties, dict)
                    self.assertIs(obj.get("additionalProperties"), False)
                    self.assertEqual(sorted(obj.get("required", [])), sorted(properties.keys()))

    def test_fix_records_per_attempt_diff_snapshots(self) -> None:
        tempdir, root = self.make_repo()
        with tempdir:
            (root / "baseline.py").write_text("value = 'clean'\n")
            (root / "sample.py").write_text("value = 1\n")
            run(["git", "add", "."], cwd=root, check=True)
            run(["git", "commit", "-m", "initial"], cwd=root, check=True)
            (root / "baseline.py").write_text("value = 'preexisting dirty change'\n")
            write_findings(root, minimal_finding("DSL-000001", files=["sample.py"]))

            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            fake_codex = fake_bin / "codex"
            fake_codex.write_text(
                """#!/usr/bin/env python3
import json
import sys
from pathlib import Path

Path("sample.py").write_text("value = 2\\n")
payload = {
    "finding_id": "DSL-000001",
    "summary": "updated sample",
    "changed_files": ["sample.py"],
    "checks_run": [],
    "risks": [],
    "status": "fixed",
}
text = json.dumps(payload)
if "--output-last-message" in sys.argv:
    Path(sys.argv[sys.argv.index("--output-last-message") + 1]).write_text(text + "\\n")
print(text)
"""
            )
            fake_codex.chmod(0o755)

            result = run(
                [str(SCRIPT_DIR / "deslop-fix.sh"), "--allow-dirty", "DSL-000001"],
                cwd=root,
                env={"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"},
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            finding = read_finding(root, "DSL-000001")
            last_fix = finding["last_fix"]
            self.assertIsInstance(last_fix, dict)
            snapshots = last_fix["snapshot_paths"]
            self.assertIsInstance(snapshots, dict)
            for key in ("status_before", "diff_before", "status_after", "diff_after", "attempt_delta"):
                self.assertTrue(Path(snapshots[key]).exists(), key)
            self.assertIn("baseline.py", Path(snapshots["diff_before"]).read_text())
            self.assertIn("sample.py", Path(snapshots["diff_after"]).read_text())
            self.assertIn("sample.py", Path(snapshots["attempt_delta"]).read_text())

    def test_verify_prompt_uses_per_finding_attempt_snapshot(self) -> None:
        tempdir, root = self.make_repo()
        with tempdir:
            fix_dir = root / ".deslop" / "runs" / "20260531T000000Z-fix-DSL-000001"
            fix_dir.mkdir(parents=True)
            status_before = fix_dir / "git-status-before.txt"
            diff_before = fix_dir / "git-diff-before.patch"
            status_after = fix_dir / "git-status-after.txt"
            diff_after = fix_dir / "git-diff-after.patch"
            attempt_delta = fix_dir / "git-diff-attempt.patch"
            status_before.write_text(" M baseline.py\n")
            diff_before.write_text("diff --git a/baseline.py b/baseline.py\n")
            status_after.write_text(" M baseline.py\n M sample.py\n")
            diff_after.write_text("diff --git a/sample.py b/sample.py\n+value = 2\n")
            attempt_delta.write_text("+diff --git a/sample.py b/sample.py\n++value = 2\n")
            finding = minimal_finding("DSL-000001", files=["sample.py"])
            finding["status"] = "fixed_unverified"
            finding["last_fix"] = {
                "snapshot_paths": {
                    "status_before": str(status_before),
                    "diff_before": str(diff_before),
                    "status_after": str(status_after),
                    "diff_after": str(diff_after),
                    "attempt_delta": str(attempt_delta),
                }
            }
            write_findings(root, finding)

            checks_dir = root / ".deslop" / "runs" / "20260531T000100Z-checks-DSL-000001"
            checks_dir.mkdir()
            checks_path = checks_dir / "checks.json"
            checks_path.write_text(
                json.dumps({"finding_id": "DSL-000001", "status": "passed", "results": []}) + "\n"
            )

            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            fake_codex = fake_bin / "codex"
            fake_codex.write_text(
                """#!/usr/bin/env python3
import json
import sys
from pathlib import Path

payload = {
    "finding_id": "DSL-000001",
    "verdict": "PASS",
    "confidence": 0.9,
    "evidence": ["checked attempt delta"],
    "concerns": [],
    "required_follow_up": [],
}
text = json.dumps(payload)
if "--output-last-message" in sys.argv:
    Path(sys.argv[sys.argv.index("--output-last-message") + 1]).write_text(text + "\\n")
print(text)
"""
            )
            fake_codex.chmod(0o755)

            result = run(
                [str(SCRIPT_DIR / "deslop-verify.sh"), "--checks-json", str(checks_path), "DSL-000001"],
                cwd=root,
                env={"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"},
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            prompt = latest_run_file(root, "verify", "DSL-000001", "prompt.txt").read_text()
            self.assertIn("Per-finding fix attempt context", prompt)
            self.assertIn("Patch-of-patches for changes introduced during this fix attempt", prompt)
            self.assertIn("do not fail solely because unrelated baseline changes are present", prompt)
            self.assertIn("sample.py", prompt)


if __name__ == "__main__":
    unittest.main()
