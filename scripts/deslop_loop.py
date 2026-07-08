#!/usr/bin/env python3
"""Run the bounded ultimate-de-slop loop."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from deslop_loop_support import (
    accepted_count_from_run,
    baseline_verified_ids,
    choose_next_id,
    current_partition,
    has_verified_uncommitted_work,
    latest_json,
    load_state,
    repo_root,
    resolve_settings,
    review_wave_result,
    save_state,
    should_allow_dirty,
    sync_partitions,
)


def fail(message: str) -> None:
    print(f"deslop-loop: error: {message}", file=sys.stderr)
    raise SystemExit(1)


def script_dir() -> Path:
    return Path(__file__).resolve().parent


def run_command(args: list[str], *, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or f"command failed: {' '.join(args)}"
        fail(message)
    return result


def run_init(root: Path) -> None:
    run_command([str(script_dir() / "deslop-init.sh")], cwd=root)


def run_review(root: Path, partition: str | None) -> Path:
    args = [str(script_dir() / "deslop-review.sh")]
    if partition:
        args.extend(["--partition", partition])
    result = run_command(args, cwd=root)
    for line in reversed(result.stdout.splitlines()):
        if line.startswith("Run directory: "):
            return Path(line.removeprefix("Run directory: ").strip())
    runs = sorted((root / ".deslop" / "runs").glob("*-review"), key=lambda path: path.as_posix(), reverse=True)
    if not runs:
        fail("review completed but no run directory was found")
    return runs[0]


def run_fix_cycle(
    root: Path,
    finding_id: str,
    *,
    commit: bool,
    auto_revert: bool,
) -> tuple[int, str | None]:
    scripts = script_dir()
    run_command([str(scripts / "deslop-fix.sh"), "--allow-dirty", finding_id], cwd=root)
    run_command([str(scripts / "deslop-run-checks.sh"), "--no-fail", finding_id], cwd=root)
    checks_json = latest_json(root, "checks", finding_id)
    verify_args = [str(scripts / "deslop-verify.sh")]
    if checks_json:
        verify_args.extend(["--checks-json", str(checks_json)])
    verify_args.append(finding_id)
    run_command(verify_args, cwd=root)
    verify_json = latest_json(root, "verify", finding_id)
    finalize_args = [str(scripts / "deslop-finalize.py"), finding_id]
    if verify_json:
        finalize_args.extend(["--verify-json", str(verify_json)])
    if checks_json:
        finalize_args.extend(["--checks-json", str(checks_json)])
    if commit:
        finalize_args.append("--commit")
    if auto_revert:
        finalize_args.append("--auto-revert")
    result = run_command(finalize_args, cwd=root, check=False)
    if result.returncode != 0:
        findings_path = root / ".deslop" / "findings.jsonl"
        status = "unknown"
        if findings_path.exists():
            import json

            for line in findings_path.read_text().splitlines():
                if not line.strip():
                    continue
                item = json.loads(line)
                if item.get("id") == finding_id:
                    status = str(item.get("status") or "unknown")
                    break
        return 1, status
    return 0, None


def record_outcome(
    root: Path,
    *,
    stop_reason: str,
    iterations_completed: int,
    settings,
    baseline_ids: list[str],
    halt_finding_id: str | None = None,
    halt_status: str | None = None,
) -> None:
    args = [
        str(script_dir() / "deslop-record-outcome.py"),
        "--stop-reason",
        stop_reason,
        "--max-iterations",
        str(settings.max_iterations),
        "--priority",
        settings.priority,
        "--iterations-completed",
        str(iterations_completed),
        "--baseline-verified-ids",
        ",".join(baseline_ids),
    ]
    if halt_finding_id:
        args.extend(["--halt-finding-id", halt_finding_id])
    if halt_status:
        args.extend(["--halt-status", halt_status])
    run_command(args, cwd=root)


def print_status(root: Path) -> None:
    result = run_command([str(script_dir() / "deslop-status.py")], cwd=root, check=False)
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="" if result.stderr.endswith("\n") else "\n")


def create_commit_branch(root: Path) -> None:
    branch = f"deslop/{subprocess.check_output(['date', '-u', '+%Y%m%dT%H%M%SZ'], text=True).strip()}"
    run_command(["git", "checkout", "-b", branch], cwd=root)
    print(f"Created branch {branch}")


def run_reviews_until_queue_or_stop(root: Path, settings, state: dict[str, Any], fix_count: int) -> tuple[bool, int]:
    """Return (should_stop, fix_count)."""
    while True:
        next_id = choose_next_id(root, settings.priorities)
        if next_id:
            return False, fix_count

        progress = sync_partitions(root, state)
        partition = current_partition(progress)
        if partition is None:
            fail("no partitions available for review")

        print(f"Reviewing partition: {partition}")
        run_dir = run_review(root, partition)
        accepted_count = accepted_count_from_run(run_dir)
        progress, action = review_wave_result(
            progress=progress,
            accepted_count=accepted_count,
            empty_review_waves_required=settings.empty_review_waves_required,
        )
        state["loop_progress"] = progress
        save_state(root, state)

        if accepted_count > 0:
            print(f"Review accepted {accepted_count} finding(s); continuing loop.")
            return False, fix_count

        if action == "continue_partition":
            print(f"No accepted findings in partition {partition}; continuing to next partition.")
            continue

        if action == "continue_wave":
            print(
                "No accepted findings in review wave "
                f"({progress['consecutive_empty_review_waves']}/{settings.empty_review_waves_required}); "
                "starting another review wave."
            )
            continue

        print("No eligible accepted findings remain after consecutive empty review waves.")
        return True, fix_count


def execute_loop(
    root: Path,
    *,
    settings,
    commit: bool,
    auto_revert: bool,
    allow_dirty: bool,
) -> int:
    if not should_allow_dirty(root, allow_dirty):
        from deslop_loop_support import git_porcelain

        print("deslop-loop: error: git tree is dirty. Commit/stash changes or pass --allow-dirty intentionally.", file=sys.stderr)
        if git_porcelain(root):
            print(git_porcelain(root), file=sys.stderr)
        return 1

    if allow_dirty and has_verified_uncommitted_work(root):
        print("Continuing with verified-but-uncommitted fixes (--allow-dirty).")

    run_init(root)
    baseline_ids = baseline_verified_ids(root)
    state = load_state(root)
    sync_partitions(root, state)
    save_state(root, state)

    fix_count = 0
    cycle = 1
    max_cycles = max(settings.max_iterations * max(len(state.get("loop_progress", {}).get("partitions", ["."]) or ["."]), 1) * 4, settings.max_iterations + 4)

    while fix_count < settings.max_iterations and cycle <= max_cycles:
        if (root / ".deslop" / "stop").exists():
            print("Stop file found: .deslop/stop")
            record_outcome(
                root,
                stop_reason="stop_file",
                iterations_completed=fix_count,
                settings=settings,
                baseline_ids=baseline_ids,
            )
            print_status(root)
            return 0

        next_id = choose_next_id(root, settings.priorities)
        review_due = cycle == 1 or ((cycle - 1) % settings.review_every) == 0

        if next_id is None and review_due:
            should_stop, fix_count = run_reviews_until_queue_or_stop(root, settings, state, fix_count)
            state = load_state(root)
            if should_stop:
                record_outcome(
                    root,
                    stop_reason="no_eligible_findings",
                    iterations_completed=fix_count,
                    settings=settings,
                    baseline_ids=baseline_ids,
                )
                print_status(root)
                return 0
            next_id = choose_next_id(root, settings.priorities)
            if next_id is None:
                cycle += 1
                continue
        elif next_id is None:
            record_outcome(
                root,
                stop_reason="no_eligible_findings",
                iterations_completed=fix_count,
                settings=settings,
                baseline_ids=baseline_ids,
            )
            print_status(root)
            return 0
        elif review_due:
            print(f"Accepted finding {next_id} already queued; skipping review before fix.")

        print(f"Iteration {fix_count + 1}: fixing {next_id}")
        code, halt_status = run_fix_cycle(root, next_id, commit=commit, auto_revert=auto_revert)
        if code != 0:
            print(f"Finalize stopped the loop for {next_id}.", file=sys.stderr)
            record_outcome(
                root,
                stop_reason="finalize_halt",
                iterations_completed=fix_count + 1,
                settings=settings,
                baseline_ids=baseline_ids,
                halt_finding_id=next_id,
                halt_status=halt_status,
            )
            print_status(root)
            return 1

        fix_count += 1
        state = load_state(root)
        progress = state.get("loop_progress", {})
        if isinstance(progress, dict):
            progress["consecutive_empty_review_waves"] = 0
            state["loop_progress"] = progress
            save_state(root, state)
        cycle += 1

    record_outcome(
        root,
        stop_reason="max_iterations_reached",
        iterations_completed=fix_count,
        settings=settings,
        baseline_ids=baseline_ids,
    )
    print_status(root)
    return 0


def should_continue(root: Path, settings) -> bool:
    if (root / ".deslop" / "stop").exists():
        return False
    if choose_next_id(root, settings.priorities):
        return True
    state = load_state(root)
    progress = sync_partitions(root, state)
    return int(progress.get("consecutive_empty_review_waves", 0) or 0) < settings.empty_review_waves_required


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the bounded ultimate-de-slop loop.")
    parser.add_argument("--max-iterations", type=int)
    parser.add_argument("--priority")
    parser.add_argument("--review-every", type=int)
    parser.add_argument("--empty-review-waves", type=int, help="consecutive empty review waves before stop")
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--auto-revert", action="store_true")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--continue", dest="continue_loop", action="store_true", help="resume while work remains")
    parser.add_argument("--no-persist-config", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = repo_root()
    settings = resolve_settings(
        root,
        max_iterations=args.max_iterations,
        priority=args.priority,
        review_every=args.review_every,
        empty_review_waves_required=args.empty_review_waves,
        persist=not args.no_persist_config,
    )

    if args.continue_loop and not should_continue(root, settings):
        print_status(root)
        return 0

    if args.commit:
        create_commit_branch(root)

    return execute_loop(
        root,
        settings=settings,
        commit=args.commit,
        auto_revert=args.auto_revert,
        allow_dirty=args.allow_dirty,
    )


if __name__ == "__main__":
    raise SystemExit(main())
