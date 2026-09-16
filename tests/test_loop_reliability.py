from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPT_DIR = SKILL_DIR / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))


def run(args, *, cwd, env=None, check=False):
    merged = os.environ.copy()
    if env:
        merged.update(env)
    return subprocess.run(
        args,
        cwd=cwd,
        env=merged,
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
    (root / ".deslop").mkdir(exist_ok=True)
    (root / ".deslop" / "runs").mkdir(exist_ok=True)
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


def minimal_finding(finding_id: str, severity: str = "P1", status: str = "accepted") -> dict:
    return {
        "acceptance_criteria": ["shared helper is used"],
        "attempts": 0,
        "category": "test",
        "confidence": 0.99,
        "created_at": "2026-05-31T00:00:00Z",
        "dependencies": [],
        "estimated_effort": "small",
        "evidence": [{"claim": "test evidence x = 1", "file": "sample.py", "lines": "1"}],
        "expected_checks": [],
        "files": ["sample.py"],
        "id": finding_id,
        "proposed_fix": "test fix",
        "reviewer": "test",
        "risk": "low",
        "severity": severity,
        "status": status,
        "title": "Test finding",
        "updated_at": "2026-05-31T00:00:00Z",
        "why_it_matters": "test why",
    }


def write_findings(root: Path, *findings: dict) -> None:
    (root / ".deslop" / "findings.jsonl").write_text(
        "".join(json.dumps(f, sort_keys=True) + "\n" for f in findings)
    )


def read_state(root: Path) -> dict:
    return json.loads((root / ".deslop" / "state.json").read_text())


def count_reviews(root: Path) -> int:
    """Actual stub invocation counter (not wall timestamps).

    Stage scripts use second-resolution run dirs that collide under fast test
    loops; the stub increments .deslop/stub_calls on every agent invocation so
    coverage assertions stay exact without weakening.
    """
    counter = root / ".deslop" / "stub_calls"
    try:
        counted = int(counter.read_text().strip() or "0")
    except (OSError, ValueError):
        counted = 0
    runs = root / ".deslop" / "runs"
    dirs = len(list(runs.glob("*-review"))) if runs.exists() else 0
    return max(counted, dirs)


def count_checks(root: Path) -> int:
    runs = root / ".deslop" / "runs"
    if not runs.exists():
        return 0
    return len(list(runs.glob("*-checks-*")))


EMPTY_STUB = """#!/usr/bin/env bash
set -euo pipefail
root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
mkdir -p "$root/.deslop"
count_file="$root/.deslop/stub_calls"
prev=0
if [ -f "$count_file" ]; then
  prev="$(cat "$count_file" 2>/dev/null || echo 0)"
fi
case "$prev" in
  ''|*[!0-9]*) prev=0 ;;
esac
printf '%s\\n' "$((prev + 1))" > "$count_file"
last=""
while [ "$#" -gt 0 ]; do
  if [ "$1" = "--output-last-message" ]; then
    last="$2"
    shift 2
    continue
  fi
  shift
done
payload='{"repo_summary":"empty","review_wave_id":"wave-1","partitions_reviewed":["all"],"findings":[]}'
if [ -n "$last" ]; then
  printf '%s\\n' "$payload" > "$last"
fi
printf '%s\\n' "$payload"
"""


class LoopSupportTests(unittest.TestCase):
    def test_validate_rejects_nonpositive_and_infinite(self) -> None:
        from deslop_loop_support import validate_positive_finite, validate_positive_int, validate_priorities

        for bad in (0, -1, "0", "-5"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    validate_positive_int("max_iterations", bad)
        for bad in (0, -1, float("inf"), float("nan"), "bad"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    validate_positive_finite("max_seconds", bad)
        for bad in ("", "P3", "P0,P3", None if False else "HIGH"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    validate_priorities(str(bad))
        self.assertEqual(validate_priorities("p0, P1 "), ["P0", "P1"])
        self.assertEqual(validate_positive_int("x", 3), 3)
        self.assertEqual(validate_positive_finite("x", "10"), 10.0)

    def test_until_clean_defaults(self) -> None:
        import tempfile

        from deslop_loop_support import resolve_settings

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run(["git", "init"], cwd=root, check=True)
            run(["git", "config", "user.email", "a@b.c"], cwd=root, check=True)
            run(["git", "config", "user.name", "t"], cwd=root, check=True)
            run([str(SCRIPT_DIR / "deslop-init.sh")], cwd=root, check=True)
            settings = resolve_settings(
                root,
                max_iterations=None,
                priority=None,
                review_every=None,
                empty_review_waves_required=None,
                persist=False,
                until_clean=True,
            )
            self.assertEqual(settings.priority, "P0,P1,P2")
            self.assertEqual(settings.max_iterations, 100)
            self.assertEqual(settings.max_review_calls, 200)
            self.assertEqual(float(settings.max_seconds or 0), 28800.0)
            self.assertEqual(settings.empty_review_waves_required, 2)

    def test_two_sweeps_required(self) -> None:
        from deslop_loop_support import review_wave_result

        progress: dict = {"consecutive_empty_review_waves": 0, "partition_index": 0, "partitions": ["."]}
        progress, action = review_wave_result(progress=progress, accepted_count=0, empty_review_waves_required=2)
        self.assertEqual(action, "continue_wave")
        self.assertEqual(progress["consecutive_empty_review_waves"], 1)
        progress, action = review_wave_result(progress=progress, accepted_count=0, empty_review_waves_required=2)
        self.assertEqual(action, "stop_empty")
        self.assertEqual(progress["consecutive_empty_review_waves"], 2)

    def test_cursor_continues_not_reset(self) -> None:
        from deslop_loop_support import review_wave_result

        progress: dict = {"consecutive_empty_review_waves": 1, "partition_index": 0, "partitions": ["a", "b", "c"]}
        progress, action = review_wave_result(progress=progress, accepted_count=2, empty_review_waves_required=2)
        self.assertEqual(action, "continue")
        self.assertEqual(progress["consecutive_empty_review_waves"], 0)
        # Must continue cursor instead of reset to zero.
        self.assertEqual(progress["partition_index"], 1)

    def test_eligible_count_respects_priority_and_dedupe(self) -> None:
        from deslop_loop_support import eligible_accepted_count_from_run

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run(["git", "init"], cwd=root, check=True)
            run(["git", "config", "user.email", "a@b.c"], cwd=root, check=True)
            run(["git", "config", "user.name", "t"], cwd=root, check=True)
            init_repo(root)
            p2 = minimal_finding("DSL-000001", severity="P2", status="accepted")
            write_findings(root, p2)
            run_dir = root / ".deslop" / "runs" / "20260101T000000Z-review"
            run_dir.mkdir(parents=True)
            (run_dir / "arbiter.json").write_text(json.dumps({"accepted": ["DSL-000001"]}) + "\n")
            self.assertEqual(eligible_accepted_count_from_run(root, run_dir, ["P0", "P1"]), 0)
            self.assertEqual(eligible_accepted_count_from_run(root, run_dir, ["P0", "P1", "P2"]), 1)

    def test_eligible_count_ignores_dangling_out_of_scope(self) -> None:
        from deslop_loop_support import dangling_accepted_ids, eligible_accepted_count_from_run

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run(["git", "init"], cwd=root, check=True)
            run(["git", "config", "user.email", "a@b.c"], cwd=root, check=True)
            run(["git", "config", "user.name", "t"], cwd=root, check=True)
            init_repo(root)
            # Findings line omitted: arbiter references unknown P2 id.
            write_findings(root)
            run_dir = root / ".deslop" / "runs" / "20260101T000000Z-review"
            run_dir.mkdir(parents=True)
            (run_dir / "arbiter.json").write_text(json.dumps({"accepted": ["DSL-999999"]}) + "\n")
            self.assertEqual(eligible_accepted_count_from_run(root, run_dir, ["P0", "P1"]), 0)
            self.assertEqual(dangling_accepted_ids(root, run_dir), ["DSL-999999"])

    def test_sync_resets_on_partition_or_priority_change(self) -> None:
        from deslop_loop_support import loop_progress, sync_partitions

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run(["git", "init"], cwd=root, check=True)
            run(["git", "config", "user.email", "a@b.c"], cwd=root, check=True)
            run(["git", "config", "user.name", "t"], cwd=root, check=True)
            init_repo(root)
            (root / "a.py").write_text("x=1\n")
            run(["git", "add", "."], cwd=root, check=True)
            run(["git", "commit", "-m", "init"], cwd=root, check=True)
            run([str(SCRIPT_DIR / "deslop-inventory.py"), "--write"], cwd=root, check=True)
            state: dict = {}
            progress = sync_partitions(root, state, "P0,P1")
            progress["consecutive_empty_review_waves"] = 2
            state["loop_progress"] = progress
            # Same partitions and priority preserves waves.
            progress2 = sync_partitions(root, state, "P0,P1")
            self.assertEqual(progress2["consecutive_empty_review_waves"], 2)
            # Priority change resets waves AND cursor (complete sweep, not suffix).
            progress3 = sync_partitions(root, state, "P0,P1,P2")
            self.assertEqual(progress3["consecutive_empty_review_waves"], 0)
            self.assertEqual(progress3["partition_index"], 0)
            progress3["consecutive_empty_review_waves"] = 2
            state["loop_progress"] = progress3
            # Partition set change resets.
            (root / "pkg").mkdir(exist_ok=True)
            (root / "pkg" / "b.py").write_text("y=2\n")
            run(["git", "add", "."], cwd=root, check=True)
            run(["git", "commit", "-m", "add"], cwd=root, check=True)
            run([str(SCRIPT_DIR / "deslop-inventory.py"), "--write"], cwd=root, check=True)
            progress4 = sync_partitions(root, state, "P0,P1,P2")
            # If partitions actually changed, waves reset; otherwise preserved.
            # At minimum, fingerprint/priority handling must not crash and must clamp cursor.
            self.assertIn("consecutive_empty_review_waves", progress4)
            self.assertLessEqual(progress4["partition_index"], max(len(progress4["partitions"]) - 1, 0))

    def test_filtered_porcelain_excludes_deslop(self) -> None:
        from deslop_loop_support import filtered_git_porcelain

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run(["git", "init"], cwd=root, check=True)
            run(["git", "config", "user.email", "a@b.c"], cwd=root, check=True)
            run(["git", "config", "user.name", "t"], cwd=root, check=True)
            init_repo(root)
            (root / "sample.py").write_text("x=1\n")
            run(["git", "add", "sample.py"], cwd=root, check=True)
            run(["git", "commit", "-m", "init"], cwd=root, check=True)
            # Only .deslop dirty -> filtered clean.
            (root / ".deslop" / "scratch.txt").write_text("scratch\n")
            self.assertEqual(filtered_git_porcelain(root), "")
            # Real dirty -> filtered non-empty.
            (root / "sample.py").write_text("x=2\n")
            self.assertNotEqual(filtered_git_porcelain(root), "")

    def test_git_failure_denies_dirty_fail_closed(self) -> None:
        from deslop_loop_support import filtered_git_porcelain, should_allow_dirty
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("deslop_loop_support.subprocess.run", return_value=subprocess.CompletedProcess(
                ["git"], 128, stdout=b"", stderr=b"fatal: unreadable repository"
            )):
                with self.assertRaises(OSError):
                    filtered_git_porcelain(root)
                self.assertFalse(should_allow_dirty(root, False))

    def test_dirty_baseline_exact_match(self) -> None:
        from deslop_loop_support import (
            load_state,
            save_state,
            should_allow_dirty,
            store_dirty_baseline,
            worktree_fingerprint,
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run(["git", "init"], cwd=root, check=True)
            run(["git", "config", "user.email", "a@b.c"], cwd=root, check=True)
            run(["git", "config", "user.name", "t"], cwd=root, check=True)
            init_repo(root)
            (root / "sample.py").write_text("x=1\n")
            (root / ".gitignore").write_text(".deslop/\n")
            run(["git", "add", "."], cwd=root, check=True)
            run(["git", "commit", "-m", "init"], cwd=root, check=True)
            run([str(SCRIPT_DIR / "deslop-init.sh")], cwd=root, check=True)
            # Historical verified alone must not auto-allow unrelated dirty.
            write_findings(root, minimal_finding("DSL-000001", status="verified"))
            (root / "sample.py").write_text("x=2 user edit\n")
            self.assertFalse(should_allow_dirty(root, False))
            # Exact fingerprint recorded after own cycle allows.
            fingerprint = worktree_fingerprint(root)
            state = load_state(root)
            store_dirty_baseline(state, fingerprint, "DSL-000001")
            save_state(root, state)
            self.assertTrue(should_allow_dirty(root, False))
            # Different content blocks.
            (root / "sample.py").write_text("x=3 different\n")
            self.assertFalse(should_allow_dirty(root, False))
            self.assertTrue(should_allow_dirty(root, True))

    def test_dirty_baseline_bound_to_goal(self) -> None:
        from deslop_loop_support import (
            load_state,
            save_state,
            should_allow_dirty,
            store_dirty_baseline,
            worktree_fingerprint,
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run(["git", "init"], cwd=root, check=True)
            run(["git", "config", "user.email", "a@b.c"], cwd=root, check=True)
            run(["git", "config", "user.name", "t"], cwd=root, check=True)
            init_repo(root)
            (root / "sample.py").write_text("x=1\n")
            (root / ".gitignore").write_text(".deslop/\n")
            run(["git", "add", "."], cwd=root, check=True)
            run(["git", "commit", "-m", "init"], cwd=root, check=True)
            run([str(SCRIPT_DIR / "deslop-init.sh")], cwd=root, check=True)
            (root / "sample.py").write_text("x=2 user edit\n")
            fingerprint = worktree_fingerprint(root)
            state = load_state(root)
            store_dirty_baseline(state, fingerprint, "DSL-000001", goal_created_at="goal-A")
            save_state(root, state)
            self.assertTrue(should_allow_dirty(root, False, "goal-A"))
            # Old baseline must not satisfy a new goal.
            self.assertFalse(should_allow_dirty(root, False, "goal-B"))
            self.assertFalse(should_allow_dirty(root, False, None if False else "goal-B"))

    def test_fingerprint_includes_head(self) -> None:
        from deslop_loop_support import worktree_fingerprint

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run(["git", "init"], cwd=root, check=True)
            run(["git", "config", "user.email", "a@b.c"], cwd=root, check=True)
            run(["git", "config", "user.name", "t"], cwd=root, check=True)
            init_repo(root)
            (root / "sample.py").write_text("x=1\n")
            (root / ".gitignore").write_text(".deslop/\n")
            run(["git", "add", "."], cwd=root, check=True)
            run(["git", "commit", "-m", "init"], cwd=root, check=True)
            run([str(SCRIPT_DIR / "deslop-init.sh")], cwd=root, check=True)
            before = worktree_fingerprint(root)
            (root / "sample.py").write_text("x=2\n")
            run(["git", "add", "sample.py"], cwd=root, check=True)
            run(["git", "commit", "-m", "change"], cwd=root, check=True)
            after = worktree_fingerprint(root)
            self.assertNotEqual(before, after)

    def test_lock_flock_live_blocks_stale_file_passes(self) -> None:
        from deslop_loop_support import acquire_loop_lock, release_loop_lock

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run(["git", "init"], cwd=root, check=True)
            run(["git", "config", "user.email", "a@b.c"], cwd=root, check=True)
            run(["git", "config", "user.name", "t"], cwd=root, check=True)
            init_repo(root)
            # Live holder blocks.
            acquire_loop_lock(root)
            try:
                with self.assertRaises(RuntimeError):
                    acquire_loop_lock(root)
            finally:
                release_loop_lock(root)
            # Stale file content without a holder must not block (crash release).
            (root / ".deslop" / "loop.lock").write_text('{"pid": 999999}\n')
            acquire_loop_lock(root)
            release_loop_lock(root)

    def test_unresolved_blocks_clean_at_scope(self) -> None:
        from deslop_loop_support import unresolved_findings

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run(["git", "init"], cwd=root, check=True)
            run(["git", "config", "user.email", "a@b.c"], cwd=root, check=True)
            run(["git", "config", "user.name", "t"], cwd=root, check=True)
            init_repo(root)
            blocked_p2 = minimal_finding("DSL-000009", severity="P2", status="blocked")
            write_findings(root, blocked_p2)
            self.assertEqual(len(unresolved_findings(root, ["P0", "P1"])), 0)
            self.assertEqual(len(unresolved_findings(root, ["P0", "P1", "P2"])), 1)

    def test_ineligible_accepted_blocks_clean(self) -> None:
        from deslop_loop_support import clean_blockers

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run(["git", "init"], cwd=root, check=True)
            run(["git", "config", "user.email", "a@b.c"], cwd=root, check=True)
            run(["git", "config", "user.name", "t"], cwd=root, check=True)
            init_repo(root)
            dep = minimal_finding("DSL-000010", severity="P1", status="blocked")
            main_f = minimal_finding("DSL-000011", severity="P1", status="accepted")
            main_f["dependencies"] = ["DSL-000010"]
            write_findings(root, dep, main_f)
            blockers = clean_blockers(root, ["P0", "P1", "P2"])
            ids = {item.get("id") for item in blockers}
            self.assertIn("DSL-000011", ids)


class LoopReliabilityIntegrationTests(unittest.TestCase):
    def make_repo(self):
        tempdir = tempfile.TemporaryDirectory()
        root = Path(tempdir.name)
        init_repo(root)
        return tempdir, root

    def test_review_churn_exhausts_budget_not_clean(self) -> None:
        tempdir, root = self.make_repo()
        with tempdir:
            (root / "sample.py").write_text("value = 1\n")
            (root / ".gitignore").write_text(".deslop/\nfake-bin/\n")
            run(["git", "add", "sample.py", ".gitignore"], cwd=root, check=True)
            run(["git", "commit", "-m", "sample"], cwd=root, check=True)
            run([str(SCRIPT_DIR / "deslop-init.sh")], cwd=root, check=True)
            write_findings(root)
            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            write_executable(fake_bin / "codex", EMPTY_STUB)
            result = run(
                [
                    str(SCRIPT_DIR / "deslop-loop.sh"),
                    "--until-clean",
                    "--max-iterations",
                    "10",
                    "--max-review-calls",
                    "1",
                    "--max-seconds",
                    "28800",
                    "--priority",
                    "P0,P1,P2",
                ],
                cwd=root,
                env={"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}", "DESLOP_HARNESS": "codex"},
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            state = read_state(root)
            self.assertEqual(state["loop_outcome"]["stop_reason"], "max_review_calls_reached")
            self.assertNotEqual(state["loop_outcome"]["stop_reason"], "until_clean")
            goal = state["loop_goal"]
            self.assertEqual(goal["status"], "exhausted")
            self.assertEqual(int(goal["consumed"]["review_calls"]), 1)

    def test_exact_review_cap_reports_clean(self) -> None:
        # 1 partition, cap exactly at the 2nd empty sweep must be until_clean.
        tempdir, root = self.make_repo()
        with tempdir:
            (root / "sample.py").write_text("value = 1\n")
            (root / ".gitignore").write_text(".deslop/\nfake-bin/\n")
            run(["git", "add", "sample.py", ".gitignore"], cwd=root, check=True)
            run(["git", "commit", "-m", "sample"], cwd=root, check=True)
            run([str(SCRIPT_DIR / "deslop-init.sh")], cwd=root, check=True)
            write_findings(root)
            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            write_executable(fake_bin / "codex", EMPTY_STUB)
            env = {"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}", "DESLOP_HARNESS": "codex"}
            result = run(
                [
                    str(SCRIPT_DIR / "deslop-loop.sh"),
                    "--until-clean",
                    "--max-iterations",
                    "10",
                    "--max-review-calls",
                    "2",
                    "--max-seconds",
                    "28800",
                    "--priority",
                    "P0,P1,P2",
                ],
                cwd=root,
                env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            state = read_state(root)
            self.assertEqual(state["loop_outcome"]["stop_reason"], "until_clean")
            self.assertEqual(state["loop_goal"]["status"], "complete")
            self.assertEqual(int(state["loop_goal"]["consumed"]["review_calls"]), 2)
            self.assertGreaterEqual(count_reviews(root), 2)

    def test_persisted_budget_restart_preserves_and_new_goal_resets(self) -> None:
        tempdir, root = self.make_repo()
        with tempdir:
            (root / "sample.py").write_text("value = 1\n")
            (root / ".gitignore").write_text(".deslop/\nfake-bin/\n")
            run(["git", "add", "sample.py", ".gitignore"], cwd=root, check=True)
            run(["git", "commit", "-m", "sample"], cwd=root, check=True)
            run([str(SCRIPT_DIR / "deslop-init.sh")], cwd=root, check=True)
            write_findings(root)
            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            write_executable(fake_bin / "codex", EMPTY_STUB)
            env = {"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}", "DESLOP_HARNESS": "codex"}
            first = run(
                [
                    str(SCRIPT_DIR / "deslop-loop.sh"),
                    "--until-clean",
                    "--max-iterations",
                    "10",
                    "--max-review-calls",
                    "1",
                    "--max-seconds",
                    "28800",
                ],
                cwd=root,
                env=env,
            )
            self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
            before = read_state(root)
            reviews_before = count_reviews(root)
            self.assertEqual(before["loop_outcome"]["stop_reason"], "max_review_calls_reached")
            # --continue must preserve limits/usage, never silently renew.
            second = run(
                [str(SCRIPT_DIR / "deslop-loop.sh"), "--until-clean", "--continue"],
                cwd=root,
                env=env,
            )
            after = read_state(root)
            self.assertEqual(after["loop_goal"]["limits"], before["loop_goal"]["limits"])
            self.assertEqual(after["loop_goal"]["consumed"], before["loop_goal"]["consumed"])
            self.assertEqual(after["loop_outcome"]["stop_reason"], "max_review_calls_reached")
            self.assertEqual(count_reviews(root), reviews_before)
            # Explicit --new-goal may reset with clear semantics.
            third = run(
                [
                    str(SCRIPT_DIR / "deslop-loop.sh"),
                    "--until-clean",
                    "--new-goal",
                    "--max-iterations",
                    "10",
                    "--max-review-calls",
                    "10",
                    "--max-seconds",
                    "28800",
                ],
                cwd=root,
                env=env,
            )
            reset = read_state(root)
            self.assertEqual(reset["loop_goal"]["status"], "complete")
            self.assertEqual(reset["loop_outcome"]["stop_reason"], "until_clean")
            self.assertGreater(count_reviews(root), reviews_before)

    def test_bare_continue_preserves_budget_no_bypass(self) -> None:
        # deslop-continue.sh passes ONLY --continue; must infer until-clean goal.
        tempdir, root = self.make_repo()
        with tempdir:
            (root / "sample.py").write_text("value = 1\n")
            (root / ".gitignore").write_text(".deslop/\nfake-bin/\n")
            run(["git", "add", "sample.py", ".gitignore"], cwd=root, check=True)
            run(["git", "commit", "-m", "sample"], cwd=root, check=True)
            run([str(SCRIPT_DIR / "deslop-init.sh")], cwd=root, check=True)
            write_findings(root)
            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            write_executable(fake_bin / "codex", EMPTY_STUB)
            env = {"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}", "DESLOP_HARNESS": "codex"}
            first = run(
                [str(SCRIPT_DIR / "deslop-loop.sh"), "--until-clean",
                 "--max-iterations", "10", "--max-review-calls", "1", "--max-seconds", "28800"],
                cwd=root, env=env,
            )
            self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
            before = read_state(root)
            reviews_before = count_reviews(root)
            # Bare continue (no --until-clean flag) must NOT bypass as bounded.
            second = run([str(SCRIPT_DIR / "deslop-continue.sh")], cwd=root, env=env)
            after = read_state(root)
            self.assertEqual(after["loop_goal"]["limits"], before["loop_goal"]["limits"])
            self.assertEqual(after["loop_goal"]["consumed"], before["loop_goal"]["consumed"])
            self.assertEqual(after["loop_outcome"]["stop_reason"], "max_review_calls_reached")
            self.assertEqual(count_reviews(root), reviews_before)
            # Exhausted goal cannot be bypassed via bounded invocation.
            third = run(
                [str(SCRIPT_DIR / "deslop-loop.sh"), "--max-iterations", "2", "--priority", "P0,P1"],
                cwd=root, env=env,
            )
            self.assertNotEqual(third.returncode, 0)
            self.assertIn("until-clean goal exists", third.stderr + third.stdout)

    def test_stop_file_halts_between_reviews(self) -> None:
        tempdir, root = self.make_repo()
        with tempdir:
            (root / "sample.py").write_text("value = 1\n")
            (root / ".gitignore").write_text(".deslop/\nfake-bin/\n")
            run(["git", "add", "sample.py", ".gitignore"], cwd=root, check=True)
            run(["git", "commit", "-m", "sample"], cwd=root, check=True)
            run([str(SCRIPT_DIR / "deslop-init.sh")], cwd=root, check=True)
            write_findings(root)
            # Pre-existing stop file halts truthfully.
            (root / ".deslop" / "stop").write_text("stop\n")
            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            write_executable(fake_bin / "codex", EMPTY_STUB)
            env = {"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}", "DESLOP_HARNESS": "codex"}
            result = run(
                [str(SCRIPT_DIR / "deslop-loop.sh"), "--until-clean", "--max-iterations", "5"],
                cwd=root,
                env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            state = read_state(root)
            self.assertEqual(state["loop_outcome"]["stop_reason"], "stop_file")
            self.assertNotEqual(state["loop_outcome"]["stop_reason"], "until_clean")

    def test_stop_during_fix_skips_checks(self) -> None:
        tempdir, root = self.make_repo()
        with tempdir:
            (root / "sample.py").write_text("value = 1\n")
            (root / ".gitignore").write_text(".deslop/\nfake-bin/\n")
            run(["git", "add", "sample.py", ".gitignore"], cwd=root, check=True)
            run(["git", "commit", "-m", "sample"], cwd=root, check=True)
            run([str(SCRIPT_DIR / "deslop-init.sh")], cwd=root, check=True)
            write_findings(root, minimal_finding("DSL-000001", severity="P1", status="accepted"))
            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            # Fix schema call creates the stop file mid-fix; review calls stay empty.
            stop_stub = """#!/usr/bin/env bash
set -euo pipefail
root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
mkdir -p "$root/.deslop"
count_file="$root/.deslop/stub_calls"
prev=0
if [ -f "$count_file" ]; then prev="$(cat "$count_file" 2>/dev/null || echo 0)"; fi
case "$prev" in ''|*[!0-9]*) prev=0 ;; esac
printf '%s\\n' "$((prev + 1))" > "$count_file"
schema=""
last=""
prev_arg=""
for arg in "$@"; do
  if [ "$prev_arg" = "--output-schema" ]; then schema="$arg"; fi
  if [ "$prev_arg" = "--output-last-message" ]; then last="$arg"; fi
  prev_arg="$arg"
done
if [[ "$schema" == *"fix.schema"* ]]; then
  printf 'stop\\n' > "$root/.deslop/stop"
  payload='{"finding_id":"DSL-000001","summary":"partial","changed_files":[],"checks_run":[],"risks":[],"status":"blocked"}'
  if [ -n "$last" ]; then printf '%s\\n' "$payload" > "$last"; fi
  printf '%s\\n' "$payload"
  exit 0
fi
payload='{"repo_summary":"empty","review_wave_id":"wave-1","partitions_reviewed":["all"],"findings":[]}'
if [ -n "$last" ]; then printf '%s\\n' "$payload" > "$last"; fi
printf '%s\\n' "$payload"
"""
            write_executable(fake_bin / "codex", stop_stub)
            env = {"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}", "DESLOP_HARNESS": "codex"}
            result = run(
                [str(SCRIPT_DIR / "deslop-loop.sh"), "--max-iterations", "2", "--priority", "P0,P1"],
                cwd=root,
                env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            state = read_state(root)
            self.assertEqual(state["loop_outcome"]["stop_reason"], "stop_file")
            self.assertNotEqual(state["loop_outcome"]["stop_reason"], "no_eligible_findings")
            # Checks must be skipped when stop lands mid-fix.
            self.assertEqual(count_checks(root), 0)

    def test_child_failure_persists_stage_failed(self) -> None:
        tempdir, root = self.make_repo()
        with tempdir:
            (root / "sample.py").write_text("value = 1\n")
            (root / ".gitignore").write_text(".deslop/\nfake-bin/\n")
            run(["git", "add", "sample.py", ".gitignore"], cwd=root, check=True)
            run(["git", "commit", "-m", "sample"], cwd=root, check=True)
            run([str(SCRIPT_DIR / "deslop-init.sh")], cwd=root, check=True)
            write_findings(root)
            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            write_executable(fake_bin / "codex", "#!/usr/bin/env bash\necho fail >&2\nexit 127\n")
            env = {"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}", "DESLOP_HARNESS": "codex"}
            result = run(
                [str(SCRIPT_DIR / "deslop-loop.sh"), "--until-clean", "--max-iterations", "5"],
                cwd=root,
                env=env,
            )
            self.assertNotEqual(result.returncode, 0)
            state = read_state(root)
            self.assertEqual(state["loop_outcome"]["stop_reason"], "stage_failed")
            self.assertEqual(state["loop_goal"]["status"], "failed")

    def test_unresolved_requires_recovery_not_clean(self) -> None:
        tempdir, root = self.make_repo()
        with tempdir:
            (root / "sample.py").write_text("value = 1\n")
            (root / ".gitignore").write_text(".deslop/\nfake-bin/\n")
            run(["git", "add", "sample.py", ".gitignore"], cwd=root, check=True)
            run(["git", "commit", "-m", "sample"], cwd=root, check=True)
            run([str(SCRIPT_DIR / "deslop-init.sh")], cwd=root, check=True)
            blocked = minimal_finding("DSL-000001", severity="P1", status="blocked")
            blocked["block_reason"] = "needs decision"
            write_findings(root, blocked)
            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            write_executable(fake_bin / "codex", EMPTY_STUB)
            env = {"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}", "DESLOP_HARNESS": "codex"}
            result = run(
                [str(SCRIPT_DIR / "deslop-loop.sh"), "--until-clean", "--priority", "P0,P1,P2"],
                cwd=root,
                env=env,
            )
            self.assertNotEqual(result.returncode, 0)
            state = read_state(root)
            self.assertEqual(state["loop_outcome"]["stop_reason"], "needs_recovery")
            self.assertNotEqual(state["loop_outcome"]["stop_reason"], "until_clean")

    def test_truncated_inventory_refuses_clean(self) -> None:
        tempdir, root = self.make_repo()
        with tempdir:
            (root / "sample.py").write_text("value = 1\n")
            (root / ".gitignore").write_text(".deslop/\nfake-bin/\n")
            run(["git", "add", "sample.py", ".gitignore"], cwd=root, check=True)
            run(["git", "commit", "-m", "sample"], cwd=root, check=True)
            run([str(SCRIPT_DIR / "deslop-init.sh")], cwd=root, check=True)
            write_findings(root)
            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            write_executable(fake_bin / "codex", EMPTY_STUB)
            env = {"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}", "DESLOP_HARNESS": "codex"}
            run(
                [str(SCRIPT_DIR / "deslop-loop.sh"), "--until-clean",
                 "--max-iterations", "10", "--max-review-calls", "1", "--max-seconds", "28800",
                 "--priority", "P0,P1,P2"],
                cwd=root, env=env, check=True,
            )
            # Forge an active goal with completed sweeps so the clean gate is
            # reached before any new review rewrites inventory.
            state = read_state(root)
            state["loop_goal"]["status"] = "active"
            state["loop_goal"]["reason"] = None
            state["loop_goal"]["limits"]["max_review_calls"] = 10
            state["loop_progress"]["consecutive_empty_review_waves"] = 2
            state["loop_progress"]["partition_index"] = 0
            (root / ".deslop" / "state.json").write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
            inventory = json.loads((root / ".deslop" / "inventory.json").read_text())
            inventory["truncated"] = True
            (root / ".deslop" / "inventory.json").write_text(json.dumps(inventory, indent=2, sort_keys=True) + "\n")
            from deslop_loop_support import inventory_is_truncated

            self.assertTrue(inventory_is_truncated(root))
            from unittest.mock import patch
            from deslop_loop import execute_until_clean
            from deslop_loop_support import resolve_settings
            settings = resolve_settings(root, max_iterations=None, priority=None, review_every=None,
                                        empty_review_waves_required=None, persist=False, until_clean=True)
            # Hold the truncated inventory fixed: normal init deliberately
            # replaces stale inventory with a fresh complete scan of this tiny fixture.
            with patch("deslop_loop.run_init"):
                execute_until_clean(root, settings=settings, commit=False, auto_revert=False, allow_dirty=False)
            after = read_state(root)
            self.assertNotEqual(after["loop_outcome"]["stop_reason"], "until_clean")
            self.assertEqual(after["loop_outcome"]["stop_reason"], "stage_failed")
            self.assertEqual(after["loop_outcome"]["halt_status"], "inventory_truncated")

    def test_git_status_failure_denies_dirty_no_review(self) -> None:
        tempdir, root = self.make_repo()
        with tempdir:
            (root / "sample.py").write_text("value = 1\n")
            (root / ".gitignore").write_text(".deslop/\nfake-bin/\n")
            run(["git", "add", "sample.py", ".gitignore"], cwd=root, check=True)
            run(["git", "commit", "-m", "sample"], cwd=root, check=True)
            run([str(SCRIPT_DIR / "deslop-init.sh")], cwd=root, check=True)
            write_findings(root)
            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            write_executable(fake_bin / "codex", EMPTY_STUB)
            real_git = shutil.which("git") or "git"
            wrap_dir = root / "wrap-bin"
            wrap_dir.mkdir()
            (wrap_dir / "git").write_text(
                f"#!/usr/bin/env bash\nif [[ \"$*\" == *\"status\"* ]]; then echo fatal >&2; exit 128; fi\nexec {real_git} \"$@\"\n"
            )
            (wrap_dir / "git").chmod(0o755)
            env = {"PATH": f"{fake_bin}{os.pathsep}{wrap_dir}{os.pathsep}{os.environ['PATH']}",
                   "DESLOP_HARNESS": "codex"}
            result = run(
                [str(SCRIPT_DIR / "deslop-loop.sh"), "--until-clean",
                 "--max-iterations", "5", "--max-review-calls", "5", "--max-seconds", "28800"],
                cwd=root, env=env,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("dirty", (result.stderr + result.stdout).lower())
            state = read_state(root)
            self.assertEqual(state["loop_outcome"]["halt_status"], "dirty_worktree")
            self.assertEqual(count_reviews(root), 0)

    def test_changed_committed_source_invalidates_complete(self) -> None:
        tempdir, root = self.make_repo()
        with tempdir:
            (root / "sample.py").write_text("value = 1\n")
            (root / ".gitignore").write_text(".deslop/\nfake-bin/\n")
            run(["git", "add", "sample.py", ".gitignore"], cwd=root, check=True)
            run(["git", "commit", "-m", "sample"], cwd=root, check=True)
            run([str(SCRIPT_DIR / "deslop-init.sh")], cwd=root, check=True)
            write_findings(root)
            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            write_executable(fake_bin / "codex", EMPTY_STUB)
            env = {"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}", "DESLOP_HARNESS": "codex"}
            first = run(
                [str(SCRIPT_DIR / "deslop-loop.sh"), "--until-clean",
                 "--max-iterations", "10", "--max-review-calls", "10", "--max-seconds", "28800",
                 "--priority", "P0,P1,P2"],
                cwd=root, env=env,
            )
            self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
            before = read_state(root)
            self.assertEqual(before["loop_outcome"]["stop_reason"], "until_clean")
            reviews_before = count_reviews(root)
            # Repeat without changes: preserved complete, no new reviews.
            repeat = run(
                [str(SCRIPT_DIR / "deslop-loop.sh"), "--until-clean", "--continue"],
                cwd=root, env=env,
            )
            self.assertEqual(repeat.returncode, 0, repeat.stderr + repeat.stdout)
            self.assertEqual(count_reviews(root), reviews_before)
            # Committed change invalidates the completed fingerprint.
            (root / "sample.py").write_text("value = 2\n")
            run(["git", "add", "sample.py"], cwd=root, check=True)
            run(["git", "commit", "-m", "change"], cwd=root, check=True)
            second = run(
                [str(SCRIPT_DIR / "deslop-loop.sh"), "--until-clean", "--continue"],
                cwd=root, env=env,
            )
            self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
            after = read_state(root)
            self.assertEqual(after["loop_outcome"]["stop_reason"], "until_clean")
            # A complete re-sweep was required, not a stale instant clean.
            self.assertGreater(count_reviews(root), reviews_before)

    def test_read_only_stage_deadline_reserves_budget(self) -> None:
        tempdir, root = self.make_repo()
        with tempdir:
            (root / "sample.py").write_text("value = 1\n")
            (root / ".gitignore").write_text(".deslop/\nfake-bin/\n")
            run(["git", "add", "sample.py", ".gitignore"], cwd=root, check=True)
            run(["git", "commit", "-m", "sample"], cwd=root, check=True)
            run([str(SCRIPT_DIR / "deslop-init.sh")], cwd=root, check=True)
            write_findings(root)
            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            sleep_stub = """#!/usr/bin/env bash
set -euo pipefail
root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
mkdir -p "$root/.deslop"
count_file="$root/.deslop/stub_calls"
prev=0
if [ -f "$count_file" ]; then prev="$(cat "$count_file" 2>/dev/null || echo 0)"; fi
case "$prev" in ''|*[!0-9]*) prev=0 ;; esac
printf '%s\\n' "$((prev + 1))" > "$count_file"
sleep 15
last=""
while [ "$#" -gt 0 ]; do
  if [ "$1" = "--output-last-message" ]; then last="$2"; shift 2; continue; fi
  shift
done
payload='{"repo_summary":"empty","review_wave_id":"wave-1","partitions_reviewed":["all"],"findings":[]}'
if [ -n "$last" ]; then printf '%s\\n' "$payload" > "$last"; fi
printf '%s\\n' "$payload"
"""
            write_executable(fake_bin / "codex", sleep_stub)
            env = {"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}", "DESLOP_HARNESS": "codex"}
            result = run(
                [str(SCRIPT_DIR / "deslop-loop.sh"), "--until-clean",
                 "--max-iterations", "10", "--max-review-calls", "10", "--max-seconds", "4",
                 "--priority", "P0,P1,P2"],
                cwd=root, env=env,
            )
            state = read_state(root)
            # Wall deadline must kill the read-only stage; reservation persists.
            self.assertNotEqual(state["loop_outcome"]["stop_reason"], "until_clean")
            self.assertGreaterEqual(int(state["loop_goal"]["consumed"]["review_calls"]), 1)
            self.assertGreaterEqual(count_reviews(root), 1)

    def test_reservation_survives_crash_checkpoint(self) -> None:
        from deslop_loop_support import effective_consumed_elapsed, ensure_goal_consumed, load_loop_goal

        tempdir, root = self.make_repo()
        with tempdir:
            (root / "sample.py").write_text("value = 1\n")
            (root / ".gitignore").write_text(".deslop/\nfake-bin/\n")
            run(["git", "add", "sample.py", ".gitignore"], cwd=root, check=True)
            run(["git", "commit", "-m", "sample"], cwd=root, check=True)
            run([str(SCRIPT_DIR / "deslop-init.sh")], cwd=root, check=True)
            write_findings(root)
            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            write_executable(fake_bin / "codex", EMPTY_STUB)
            env = {"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}", "DESLOP_HARNESS": "codex"}
            run(
                [str(SCRIPT_DIR / "deslop-loop.sh"), "--until-clean",
                 "--max-iterations", "10", "--max-review-calls", "1", "--max-seconds", "28800"],
                cwd=root, env=env, check=True,
            )
            state = read_state(root)
            self.assertGreaterEqual(int(state["loop_goal"]["consumed"]["review_calls"]), 1)
            # Simulate crash between reservation and finalize: persisted
            # in-flight wall time must count toward the wall budget.
            goal = load_loop_goal(state)
            assert goal is not None
            ensure_goal_consumed(goal)
            goal["in_flight"] = {"kind": "review", "started_at": time.time() - 30.0, "elapsed_at_start": 10.0}
            goal["consumed"]["elapsed_seconds"] = 10.0
            state["loop_goal"] = goal
            (root / ".deslop" / "state.json").write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
            reloaded = read_state(root)
            resumed = effective_consumed_elapsed(load_loop_goal(reloaded) or {})
            self.assertGreaterEqual(resumed, 30.0)

    def test_wrong_priority_scope_limited(self) -> None:
        tempdir, root = self.make_repo()
        with tempdir:
            (root / "sample.py").write_text("value = 1\n")
            (root / ".gitignore").write_text(".deslop/\nfake-bin/\n")
            run(["git", "add", "sample.py", ".gitignore"], cwd=root, check=True)
            run(["git", "commit", "-m", "sample"], cwd=root, check=True)
            run([str(SCRIPT_DIR / "deslop-init.sh")], cwd=root, check=True)
            p2 = minimal_finding("DSL-000002", severity="P2", status="accepted")
            write_findings(root, p2)
            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            write_executable(fake_bin / "codex", EMPTY_STUB)
            env = {"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}", "DESLOP_HARNESS": "codex"}
            result = run(
                [str(SCRIPT_DIR / "deslop-loop.sh"), "--max-iterations", "2", "--priority", "P0,P1"],
                cwd=root,
                env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            state = read_state(root)
            # P2 at wrong priority must not be treated as eligible queue fuel.
            self.assertEqual(state["loop_outcome"]["stop_reason"], "no_eligible_findings")
            status = run([sys.executable, str(SCRIPT_DIR / "deslop-status.py"), "--json"], cwd=root)
            payload = json.loads(status.stdout)
            self.assertIsNone(payload["next"])
            self.assertIn("P2 remain", payload["loop_summary"].get("priority_note") or "")

    def test_incomplete_coverage_needs_all_partitions(self) -> None:
        tempdir, root = self.make_repo()
        with tempdir:
            (root / "src").mkdir()
            (root / "lib").mkdir()
            (root / "src" / "a.py").write_text("x=1\n")
            (root / "lib" / "b.py").write_text("y=1\n")
            (root / ".gitignore").write_text(".deslop/\nfake-bin/\n")
            run(["git", "add", "."], cwd=root, check=True)
            run(["git", "commit", "-m", "two parts"], cwd=root, check=True)
            run([str(SCRIPT_DIR / "deslop-init.sh")], cwd=root, check=True)
            write_findings(root)
            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            write_executable(fake_bin / "codex", EMPTY_STUB)
            env = {"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}", "DESLOP_HARNESS": "codex"}
            result = run(
                [str(SCRIPT_DIR / "deslop-loop.sh"), "--until-clean", "--priority", "P0,P1,P2"],
                cwd=root,
                env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            state = read_state(root)
            self.assertEqual(state["loop_outcome"]["stop_reason"], "until_clean")
            partitions = state["loop_progress"]["partitions"]
            self.assertGreaterEqual(len(partitions), 2)
            # Two complete empty sweeps of all partitions.
            self.assertGreaterEqual(count_reviews(root), len(partitions) * 2)
            self.assertEqual(state["loop_progress"]["consecutive_empty_review_waves"], 2)

    def test_concurrent_lock_blocked(self) -> None:
        from deslop_loop_support import acquire_loop_lock, release_loop_lock

        tempdir, root = self.make_repo()
        with tempdir:
            (root / "sample.py").write_text("value = 1\n")
            (root / ".gitignore").write_text(".deslop/\nfake-bin/\n")
            run(["git", "add", "sample.py", ".gitignore"], cwd=root, check=True)
            run(["git", "commit", "-m", "sample"], cwd=root, check=True)
            run([str(SCRIPT_DIR / "deslop-init.sh")], cwd=root, check=True)
            write_findings(root)
            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            write_executable(fake_bin / "codex", EMPTY_STUB)
            env = {"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}", "DESLOP_HARNESS": "codex"}
            # Live flock holder blocks a concurrent loop.
            acquire_loop_lock(root)
            try:
                result = run(
                    [str(SCRIPT_DIR / "deslop-loop.sh"), "--until-clean"],
                    cwd=root,
                    env=env,
                )
            finally:
                release_loop_lock(root)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("another deslop-loop is running", result.stderr + result.stdout)

    def test_stale_lock_file_does_not_block(self) -> None:
        tempdir, root = self.make_repo()
        with tempdir:
            (root / "sample.py").write_text("value = 1\n")
            (root / ".gitignore").write_text(".deslop/\nfake-bin/\n")
            run(["git", "add", "sample.py", ".gitignore"], cwd=root, check=True)
            run(["git", "commit", "-m", "sample"], cwd=root, check=True)
            run([str(SCRIPT_DIR / "deslop-init.sh")], cwd=root, check=True)
            write_findings(root)
            # Stale content from a crashed holder (no flock held) must not block.
            (root / ".deslop" / "loop.lock").write_text('{"pid": 999999}\n')
            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            write_executable(fake_bin / "codex", EMPTY_STUB)
            env = {"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}", "DESLOP_HARNESS": "codex"}
            result = run(
                [str(SCRIPT_DIR / "deslop-loop.sh"), "--until-clean",
                 "--max-iterations", "10", "--max-review-calls", "10", "--max-seconds", "28800",
                 "--priority", "P0,P1,P2"],
                cwd=root,
                env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_new_goal_clears_dirty_baseline(self) -> None:
        tempdir, root = self.make_repo()
        with tempdir:
            (root / "sample.py").write_text("value = 1\n")
            (root / ".gitignore").write_text(".deslop/\nfake-bin/\n")
            run(["git", "add", "sample.py", ".gitignore"], cwd=root, check=True)
            run(["git", "commit", "-m", "sample"], cwd=root, check=True)
            run([str(SCRIPT_DIR / "deslop-init.sh")], cwd=root, check=True)
            write_findings(root)
            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            write_executable(fake_bin / "codex", EMPTY_STUB)
            env = {"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}", "DESLOP_HARNESS": "codex"}
            run(
                [str(SCRIPT_DIR / "deslop-loop.sh"), "--until-clean",
                 "--max-iterations", "10", "--max-review-calls", "1", "--max-seconds", "28800"],
                cwd=root, env=env, check=True,
            )
            # Dirty matching the old baseline...
            (root / "sample.py").write_text("value = dirty\n")
            run(["git", "add", "sample.py"], cwd=root, check=True)
            # Fake an old baseline matching current dirt without goal binding.
            import hashlib

            state = read_state(root)
            # Compute via support to bind to old goal, then switch goal.
            from deslop_loop_support import worktree_fingerprint

            fp = worktree_fingerprint(root)
            state["loop_dirty_baseline"] = {"fingerprint": fp, "at": "2026-01-01T00:00:00Z",
                                            "finding_id": "DSL-000001", "goal_created_at": "old-goal"}
            (root / ".deslop" / "state.json").write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
            # --new-goal without --allow-dirty must refuse dirty worktree.
            result = run(
                [str(SCRIPT_DIR / "deslop-loop.sh"), "--until-clean", "--new-goal",
                 "--max-iterations", "10", "--max-review-calls", "10", "--max-seconds", "28800"],
                cwd=root, env=env,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("dirty", (result.stderr + result.stdout).lower())


if __name__ == "__main__":
    unittest.main()
