from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

try:
    from test_harness import (
        SCRIPT_DIR,
        init_repo,
        latest_run_file,
        minimal_finding,
        prepare_verify_fixture,
        read_finding,
        run,
        write_executable,
        write_fake_codex_verify,
        write_findings,
    )
except ImportError:  # pragma: no cover - fallback for alternate top-level layout
    from tests.test_harness import (  # type: ignore[no-redef]
        SCRIPT_DIR,
        init_repo,
        latest_run_file,
        minimal_finding,
        prepare_verify_fixture,
        read_finding,
        run,
        write_executable,
        write_fake_codex_verify,
        write_findings,
    )


def _path_env(fake_bin: Path) -> dict[str, str]:
    return {"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"}


def _write_codex_fix(fake_bin: Path, payload_source: str) -> None:
    fake_bin.mkdir(parents=True, exist_ok=True)
    write_executable(fake_bin / "codex", payload_source)


def _valid_review_finding(**overrides) -> dict[str, object]:
    base: dict[str, object] = {
        "title": "Request validation is duplicated across handlers",
        "severity": "P1",
        "confidence": 0.91,
        "category": "correctness",
        "files": ["sample.py"],
        "evidence": [
            {"file": "sample.py", "lines": "1-4", "symbol": "create", "claim": "create() repeats `if not payload.get(\"name\")` from update() at line 2"}
        ],
        "why_it_matters": "Divergent validation can produce inconsistent writes",
        "proposed_fix": "Extract shared validate_payload() and call it from both handlers",
        "acceptance_criteria": ["create and update call one shared validator"],
        "expected_checks": ["python3 -m py_compile sample.py"],
        "risk": "low",
        "estimated_effort": "small",
    }
    base.update(overrides)
    return base


class GateReliabilityTests(unittest.TestCase):
    def make_repo(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        tempdir = tempfile.TemporaryDirectory()
        root = Path(tempdir.name)
        init_repo(root)
        return tempdir, root

    def test_fix_rejects_wrong_finding_id(self) -> None:
        tempdir, root = self.make_repo()
        with tempdir:
            (root / "sample.py").write_text("value = 1\n")
            run(["git", "add", "."], cwd=root, check=True)
            run(["git", "commit", "-m", "base"], cwd=root, check=True)
            write_findings(root, minimal_finding("DSL-000001"))
            fake_bin = root / "fake-bin"
            _write_codex_fix(
                fake_bin,
                """#!/usr/bin/env python3
import json
import sys
from pathlib import Path
payload = {
    "finding_id": "DSL-000002",
    "summary": "wrong id",
    "changed_files": ["sample.py"],
    "checks_run": [],
    "risks": [],
    "status": "fixed",
}
text = json.dumps(payload)
if "--output-last-message" in sys.argv:
    Path(sys.argv[sys.argv.index("--output-last-message") + 1]).write_text(text + "\\n")
print(text)
""",
            )
            result = run(
                [str(SCRIPT_DIR / "deslop-fix.sh"), "--allow-dirty", "DSL-000001"],
                cwd=root,
                env=_path_env(fake_bin),
            )
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("mismatch", (result.stderr + result.stdout).lower())
            self.assertNotEqual(read_finding(root, "DSL-000001")["status"], "fixed_unverified")

    def test_verify_rejects_wrong_finding_id(self) -> None:
        tempdir, root = self.make_repo()
        with tempdir:
            checks_path = prepare_verify_fixture(root)
            fake_bin = root / "fake-bin"
            write_fake_codex_verify(
                fake_bin,
                {
                    "finding_id": "DSL-000002",
                    "verdict": "PASS",
                    "confidence": 0.9,
                    "evidence": ["checked sample.py change in detail"],
                    "concerns": [],
                    "required_follow_up": [],
                },
            )
            result = run(
                [str(SCRIPT_DIR / "deslop-verify.sh"), "--checks-json", str(checks_path), "DSL-000001"],
                cwd=root,
                env=_path_env(fake_bin),
            )
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("mismatch", (result.stderr + result.stdout).lower())

    def test_fix_missing_finding_id_normalizes_to_expected(self) -> None:
        tempdir, root = self.make_repo()
        with tempdir:
            (root / "sample.py").write_text("value = 1\n")
            run(["git", "add", "."], cwd=root, check=True)
            run(["git", "commit", "-m", "base"], cwd=root, check=True)
            write_findings(root, minimal_finding("DSL-000001", files=["sample.py"]))
            fake_bin = root / "fake-bin"
            _write_codex_fix(
                fake_bin,
                """#!/usr/bin/env python3
import json
import sys
from pathlib import Path
Path("sample.py").write_text("value = 2\\n")
payload = {
    "summary": "legacy fixer omits id",
    "changed_files": ["sample.py"],
    "checks_run": [],
    "risks": [],
    "status": "fixed",
}
text = json.dumps(payload)
if "--output-last-message" in sys.argv:
    Path(sys.argv[sys.argv.index("--output-last-message") + 1]).write_text(text + "\\n")
print(text)
""",
            )
            result = run(
                [str(SCRIPT_DIR / "deslop-fix.sh"), "--allow-dirty", "DSL-000001"],
                cwd=root,
                env=_path_env(fake_bin),
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            fix = json.loads(latest_run_file(root, "fix", "DSL-000001", "fix.json").read_text())
            self.assertEqual(fix["finding_id"], "DSL-000001")
            self.assertEqual(read_finding(root, "DSL-000001")["status"], "fixed_unverified")

    def test_verify_missing_finding_id_normalizes_to_expected(self) -> None:
        tempdir, root = self.make_repo()
        with tempdir:
            checks_path = prepare_verify_fixture(root)
            fake_bin = root / "fake-bin"
            write_fake_codex_verify(
                fake_bin,
                {
                    "verdict": "PASS",
                    "confidence": 0.9,
                    "evidence": ["checked sample.py change in detail"],
                    "concerns": [],
                    "required_follow_up": [],
                },
            )
            result = run(
                [str(SCRIPT_DIR / "deslop-verify.sh"), "--checks-json", str(checks_path), "DSL-000001"],
                cwd=root,
                env=_path_env(fake_bin),
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            verify = json.loads(latest_run_file(root, "verify", "DSL-000001", "verify.json").read_text())
            self.assertEqual(verify["finding_id"], "DSL-000001")

    def test_finalize_refuses_bad_checks(self) -> None:
        cases = [
            ("skipped", {"finding_id": "DSL-000001", "status": "skipped", "results": []}),
            ("failed", {"finding_id": "DSL-000001", "status": "failed", "results": []}),
            ("binding_mismatch", {"finding_id": "DSL-000002", "status": "passed", "results": []}),
            (
                "result_failed",
                {
                    "finding_id": "DSL-000001",
                    "status": "passed",
                    "results": [{"command": "python3 -m py_compile sample.py", "status": "failed", "exit_code": 1}],
                },
            ),
        ]
        for name, checks in cases:
            with self.subTest(checks=name):
                tempdir, root = self.make_repo()
                with tempdir:
                    finding = minimal_finding("DSL-000001")
                    finding["status"] = "fixed_unverified"
                    write_findings(root, finding)
                    verify_dir = root / ".deslop" / "runs" / "20260531T000200Z-verify-DSL-000001"
                    verify_dir.mkdir(parents=True)
                    verify_path = verify_dir / "verify.json"
                    verify_path.write_text(
                        json.dumps(
                            {
                                "finding_id": "DSL-000001",
                                "verdict": "PASS",
                                "confidence": 0.9,
                                "evidence": ["verified sample.py fix in detail"],
                                "concerns": [],
                                "required_follow_up": [],
                            }
                        )
                        + "\n"
                    )
                    checks_dir = root / ".deslop" / "runs" / "20260531T000100Z-checks-DSL-000001"
                    checks_dir.mkdir(parents=True)
                    checks_path = checks_dir / "checks.json"
                    checks_path.write_text(json.dumps(checks) + "\n")
                    result = run(
                        [
                            "python3",
                            str(SCRIPT_DIR / "deslop-finalize.py"),
                            "DSL-000001",
                            "--verify-json",
                            str(verify_path),
                            "--checks-json",
                            str(checks_path),
                        ],
                        cwd=root,
                    )
                    self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertNotEqual(read_finding(root, "DSL-000001")["status"], "verified")

    def test_finalize_refuses_missing_checks_file(self) -> None:
        tempdir, root = self.make_repo()
        with tempdir:
            finding = minimal_finding("DSL-000001")
            finding["status"] = "fixed_unverified"
            write_findings(root, finding)
            verify_dir = root / ".deslop" / "runs" / "20260531T000200Z-verify-DSL-000001"
            verify_dir.mkdir(parents=True)
            verify_path = verify_dir / "verify.json"
            verify_path.write_text(
                json.dumps(
                    {
                        "finding_id": "DSL-000001",
                        "verdict": "PASS",
                        "confidence": 0.9,
                        "evidence": ["verified sample.py fix in detail"],
                        "concerns": [],
                        "required_follow_up": [],
                    }
                )
                + "\n"
            )
            result = run(
                [
                    "python3",
                    str(SCRIPT_DIR / "deslop-finalize.py"),
                    "DSL-000001",
                    "--verify-json",
                    str(verify_path),
                    "--checks-json",
                    str(root / ".deslop" / "runs" / "missing-checks.json"),
                ],
                cwd=root,
            )
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotEqual(read_finding(root, "DSL-000001")["status"], "verified")

    def test_docs_only_exception_needs_paths_explanation_and_evidence(self) -> None:
        tempdir, root = self.make_repo()
        with tempdir:
            # Positive control: docs path + explanation + docs evidence passes with skipped checks.
            finding = minimal_finding("DSL-000001")
            finding["status"] = "fixed_unverified"
            finding["last_fix"] = {"changed_files": ["docs/guide.md"]}
            finding["no_expected_checks_reason"] = "docs-only change, no code checks apply"
            write_findings(root, finding)
            verify_dir = root / ".deslop" / "runs" / "20260531T000200Z-verify-DSL-000001"
            verify_dir.mkdir(parents=True)
            verify_path = verify_dir / "verify.json"
            verify_path.write_text(
                json.dumps(
                    {
                        "finding_id": "DSL-000001",
                        "verdict": "PASS",
                        "confidence": 0.9,
                        "evidence": ["Updated docs guide with new usage section"],
                        "concerns": [],
                        "required_follow_up": [],
                    }
                )
                + "\n"
            )
            checks_dir = root / ".deslop" / "runs" / "20260531T000100Z-checks-DSL-000001"
            checks_dir.mkdir(parents=True)
            checks_path = checks_dir / "checks.json"
            checks_path.write_text(
                json.dumps({"finding_id": "DSL-000001", "status": "skipped", "results": []}) + "\n"
            )
            result = run(
                [
                    "python3",
                    str(SCRIPT_DIR / "deslop-finalize.py"),
                    "DSL-000001",
                    "--verify-json",
                    str(verify_path),
                    "--checks-json",
                    str(checks_path),
                ],
                cwd=root,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertEqual(read_finding(root, "DSL-000001")["status"], "verified")

        negatives = [
            ("non_doc_path", ["src/app.py"], "docs-only change, no code checks apply", ["Updated docs guide with new usage"]),
            ("missing_explanation", ["docs/guide.md"], "", ["Updated docs guide with new usage"]),
            ("missing_docs_evidence", ["docs/guide.md"], "docs-only change, no code checks apply", ["Checked sample.py fix in detail"]),
        ]
        for name, changed, explanation, evidence in negatives:
            with self.subTest(case=name):
                tempdir2, root2 = self.make_repo()
                with tempdir2:
                    finding = minimal_finding("DSL-000001")
                    finding["status"] = "fixed_unverified"
                    finding["last_fix"] = {"changed_files": changed}
                    if explanation:
                        finding["no_expected_checks_reason"] = explanation
                    else:
                        finding.pop("no_expected_checks_reason", None)
                        finding.pop("expected_checks_explanation", None)
                        finding.pop("checks_explanation", None)
                    write_findings(root2, finding)
                    verify_dir = root2 / ".deslop" / "runs" / "20260531T000200Z-verify-DSL-000001"
                    verify_dir.mkdir(parents=True)
                    verify_path = verify_dir / "verify.json"
                    verify_path.write_text(
                        json.dumps(
                            {
                                "finding_id": "DSL-000001",
                                "verdict": "PASS",
                                "confidence": 0.9,
                                "evidence": evidence,
                                "concerns": [],
                                "required_follow_up": [],
                            }
                        )
                        + "\n"
                    )
                    checks_dir = root2 / ".deslop" / "runs" / "20260531T000100Z-checks-DSL-000001"
                    checks_dir.mkdir(parents=True)
                    checks_path = checks_dir / "checks.json"
                    checks_path.write_text(
                        json.dumps({"finding_id": "DSL-000001", "status": "skipped", "results": []}) + "\n"
                    )
                    result = run(
                        [
                            "python3",
                            str(SCRIPT_DIR / "deslop-finalize.py"),
                            "DSL-000001",
                            "--verify-json",
                            str(verify_path),
                            "--checks-json",
                            str(checks_path),
                        ],
                        cwd=root2,
                    )
                    self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertNotEqual(read_finding(root2, "DSL-000001")["status"], "verified")

    def test_make_only_repo_allows_test_lint_but_rejects_deploy_build(self) -> None:
        tempdir, root = self.make_repo()
        with tempdir:
            (root / "sample.py").write_text("value = 1\n")
            run(["git", "add", "."], cwd=root, check=True)
            run(["git", "commit", "-m", "base"], cwd=root, check=True)
            review = {
                "repo_summary": "make repo",
                "review_wave_id": "wave-make",
                "partitions_reviewed": ["root"],
                "findings": [
                    _valid_review_finding(
                        title="Make targets need scoping",
                        expected_checks=["make test", "make lint", "make deploy", "make build", "make check", "make typecheck"],
                    )
                ],
            }
            review_path = root / "review.json"
            review_path.write_text(json.dumps(review) + "\n")
            result = run([str(SCRIPT_DIR / "deslop-arbitrate.py"), str(review_path), "--json"], cwd=root)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            outcome = json.loads(result.stdout)
            self.assertEqual(len(outcome["accepted"]), 1)
            finding = read_finding(root, outcome["accepted"][0])
            kept = finding["expected_checks"]
            for allowed in ("make test", "make lint", "make check", "make typecheck"):
                self.assertIn(allowed, kept)
            self.assertNotIn("make deploy", kept)
            self.assertNotIn("make build", kept)

    def test_vague_punctuation_finding_rejected(self) -> None:
        tempdir, root = self.make_repo()
        with tempdir:
            (root / "sample.py").write_text("value = 1\n")
            run(["git", "add", "."], cwd=root, check=True)
            run(["git", "commit", "-m", "base"], cwd=root, check=True)
            review = {
                "repo_summary": "punctuation review",
                "review_wave_id": "wave-vague",
                "partitions_reviewed": ["root"],
                "findings": [
                    _valid_review_finding(
                        title="Punctuation only evidence",
                        evidence=[{"file": "sample.py", "lines": "1", "symbol": "value", "claim": "!!! ??? ..."}],
                    )
                ],
            }
            review_path = root / "review.json"
            review_path.write_text(json.dumps(review) + "\n")
            result = run([str(SCRIPT_DIR / "deslop-arbitrate.py"), str(review_path), "--json"], cwd=root)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            outcome = json.loads(result.stdout)
            self.assertEqual(outcome["accepted"], [])
            self.assertTrue(outcome["rejected"])
            reasons = " ".join(outcome["rejected"][0]["reasons"]).lower()
            self.assertTrue("speculative" in reasons or "concrete" in reasons or "vague" in reasons, reasons)

    def test_verify_rejects_punctuation_evidence(self) -> None:
        tempdir, root = self.make_repo()
        with tempdir:
            checks_path = prepare_verify_fixture(root)
            fake_bin = root / "fake-bin"
            write_fake_codex_verify(
                fake_bin,
                {
                    "finding_id": "DSL-000001",
                    "verdict": "PASS",
                    "confidence": 0.9,
                    "evidence": ["!!! ??? ..."],
                    "concerns": [],
                    "required_follow_up": [],
                },
            )
            result = run(
                [str(SCRIPT_DIR / "deslop-verify.sh"), "--checks-json", str(checks_path), "DSL-000001"],
                cwd=root,
                env=_path_env(fake_bin),
            )
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("vague", result.stderr.lower())

    def test_verify_rejects_binding_mismatched_checks(self) -> None:
        tempdir, root = self.make_repo()
        with tempdir:
            checks_path = prepare_verify_fixture(root)
            checks = json.loads(checks_path.read_text())
            checks["finding_id"] = "DSL-000002"
            checks_path.write_text(json.dumps(checks) + "\n")
            fake_bin = root / "fake-bin"
            write_fake_codex_verify(
                fake_bin,
                {
                    "finding_id": "DSL-000001",
                    "verdict": "PASS",
                    "confidence": 0.9,
                    "evidence": ["checked sample.py change in detail"],
                    "concerns": [],
                    "required_follow_up": [],
                },
            )
            result = run(
                [str(SCRIPT_DIR / "deslop-verify.sh"), "--checks-json", str(checks_path), "DSL-000001"],
                cwd=root,
                env=_path_env(fake_bin),
            )
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("mismatch", (result.stderr + result.stdout).lower())

    def test_read_only_runner_never_uses_force_or_yolo(self) -> None:
        harnesses = {
            "cursor": ("cursor-agent", "cursor"),
            "commandcode": ("commandcode", "commandcode"),
            "hermes": ("hermes", "hermes"),
        }
        for harness, (cli, _label) in harnesses.items():
            with self.subTest(harness=harness):
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
                        fake_bin / cli,
                        """#!/usr/bin/env python3
import json
import sys
print(json.dumps({"argv": sys.argv[1:]}))
""",
                    )
                    # danger-full-access with a read-only kind must stay read-only.
                    result = run(
                        [
                            "python3",
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
                            str(SCRIPT_DIR.parent / "references" / "review.schema.json"),
                            "--sandbox",
                            "danger-full-access",
                            "--kind",
                            "review",
                        ],
                        cwd=root,
                        env={
                            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                            "DESLOP_HARNESS": harness,
                            "DESLOP_HERMES_YOLO": "1",
                            "DESLOP_COMMANDCODE_YOLO": "1",
                        },
                    )
                    self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                    diagnostics = json.loads(runner_json.read_text())
                    command = diagnostics["command"]
                    self.assertNotIn("--force", command)
                    self.assertNotIn("--yolo", command)

    def test_manual_recovery_of_fixing_and_fixed_unverified(self) -> None:
        for start_status in ("fixing", "fixed_unverified"):
            with self.subTest(start=start_status):
                tempdir, root = self.make_repo()
                with tempdir:
                    finding = minimal_finding("DSL-000001")
                    finding["status"] = start_status
                    if start_status == "fixing":
                        finding["interrupted_fix"] = {"at": "2026-05-31T00:00:00Z", "reason": "manual interrupt"}
                    write_findings(root, finding)
                    before = run(["python3", str(SCRIPT_DIR / "deslop-next.py")], cwd=root)
                    self.assertEqual(before.returncode, 0, before.stderr + before.stdout)
                    self.assertEqual(before.stdout.strip(), "NONE")
                    resumed = run(
                        ["python3", str(SCRIPT_DIR / "deslop-resume.py"), "DSL-000001", "--as", "accepted", "--reason", "manual retry"],
                        cwd=root,
                    )
                    self.assertEqual(resumed.returncode, 0, resumed.stderr + resumed.stdout)
                    updated = read_finding(root, "DSL-000001")
                    self.assertEqual(updated["status"], "accepted")
                    self.assertNotIn("interrupted_fix", updated)
                    nxt = run(["python3", str(SCRIPT_DIR / "deslop-next.py")], cwd=root)
                    self.assertEqual(nxt.returncode, 0, nxt.stderr + nxt.stdout)
                    self.assertEqual(nxt.stdout.strip(), "DSL-000001")

    def test_inventory_reports_all_partitions_beyond_thirty(self) -> None:
        tempdir, root = self.make_repo()
        with tempdir:
            for index in range(35):
                part = root / f"mod{index:02d}"
                part.mkdir()
                (part / "mod.py").write_text(f"value{index} = 1\n")
            run(["git", "add", "."], cwd=root, check=True)
            run(["git", "commit", "-m", "many partitions"], cwd=root, check=True)
            result = run([str(SCRIPT_DIR / "deslop-inventory.py"), "--write"], cwd=root)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            inventory = json.loads((root / ".deslop" / "inventory.json").read_text())
            self.assertEqual(inventory["risk_partition_count"], 35)
            self.assertFalse(inventory["risk_partitions_truncated"])
            paths = {item["path"] for item in inventory["risk_partitions"]}
            for index in range(35):
                self.assertIn(f"mod{index:02d}", paths)


if __name__ == "__main__":
    unittest.main()
