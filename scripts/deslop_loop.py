#!/usr/bin/env python3
"""Run the bounded ultimate-de-slop loop."""

from __future__ import annotations

import argparse
import os
import subprocess
import signal
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from deslop_loop_support import (
    DEFAULT_UNTIL_CLEAN_MAX_FIX_ATTEMPTS,
    DEFAULT_UNTIL_CLEAN_MAX_REVIEW_CALLS,
    DEFAULT_UNTIL_CLEAN_MAX_SECONDS,
    GOAL_SCOPE_UNTIL_CLEAN,
    UNTIL_CLEAN_REQUIRED_EMPTY_SWEEPS,
    accepted_count_from_run,
    acquire_loop_lock,
    baseline_verified_ids,
    choose_next_id,
    clean_blockers,
    clear_dirty_baseline,
    content_fingerprint,
    create_loop_goal,
    current_partition,
    dangling_accepted_ids,
    effective_consumed_elapsed,
    eligible_accepted_count_from_run,
    ensure_goal_consumed,
    filtered_git_porcelain,
    get_finding,
    get_finding_status,
    goal_in_flight,
    goal_status_for_stop_reason,
    has_verified_uncommitted_work,
    inventory_is_truncated,
    load_dirty_baseline,
    load_loop_goal,
    load_state,
    loop_progress,
    note_coverage_content,
    partition_paths,
    release_loop_lock,
    repo_root,
    reset_coverage_for_new_sweep,
    resolve_settings,
    review_wave_result,
    save_state,
    should_allow_dirty,
    stop_requested,
    store_dirty_baseline,
    sync_partitions,
    unresolved_findings,
    validate_positive_finite,
    validate_positive_int,
    validate_priorities,
    worktree_fingerprint,
)


def fail(message: str) -> None:
    print(f"deslop-loop: error: {message}", file=sys.stderr)
    raise SystemExit(1)


def script_dir() -> Path:
    return Path(__file__).resolve().parent


def run_command(
    args: list[str], *, cwd: Path, check: bool = True, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = None
    if extra_env:
        env = os.environ.copy()
        env.update(extra_env)
    deadline = float((extra_env or {}).get("DESLOP_STAGE_DEADLINE", "0"))
    remaining = max(0.0, deadline - time.monotonic()) if deadline else None
    if remaining is not None and remaining <= 0:
        result = subprocess.CompletedProcess(args, 124, "", "goal stage deadline exhausted")
    elif remaining is None:
        result = subprocess.run(args, cwd=cwd, env=env, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, check=False)
    else:
        env["DESLOP_TIMEOUT_SECONDS"] = str(min(float(env.get("DESLOP_TIMEOUT_SECONDS", remaining)), remaining))
        process = subprocess.Popen(args, cwd=cwd, env=env, text=True, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, start_new_session=True)
        try:
            stdout, stderr = process.communicate(timeout=remaining)
            result = subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                stdout, stderr = process.communicate(timeout=12)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                stdout, stderr = process.communicate()
            result = subprocess.CompletedProcess(args, 124, stdout, stderr + "\ngoal stage deadline exhausted")
    if check and result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or f"command failed: {' '.join(args)}"
        fail(message)
    return result


def run_init(root: Path) -> None:
    run_command([str(script_dir() / "deslop-init.sh")], cwd=root)


def _parse_run_dir(output: str) -> Path | None:
    for line in reversed(output.splitlines()):
        if line.startswith("Run directory: "):
            return Path(line.removeprefix("Run directory: ").strip())
    return None


def run_review(
    root: Path, partition: str | None, *, stage_timeout: float | None = None
) -> Path:
    args = [str(script_dir() / "deslop-review.sh")]
    if partition:
        args.extend(["--partition", partition])
    extra: dict[str, str] | None = None
    if stage_timeout is not None and stage_timeout > 0 and stage_timeout != float("inf"):
        extra = {"DESLOP_TIMEOUT_SECONDS": str(stage_timeout),
                 "DESLOP_STAGE_DEADLINE": str(time.monotonic() + stage_timeout)}
    result = run_command(args, cwd=root, extra_env=extra)
    run_dir = _parse_run_dir(result.stdout)
    if run_dir is None:
        fail("review completed but no run directory was printed; refusing stale fallback")
    assert run_dir is not None
    if not (run_dir / "arbiter.json").exists():
        fail(f"review run dir missing arbiter.json: {run_dir}")
    return run_dir


def _finding_changed_during_attempt(root: Path, finding_id: str) -> bool:
    item = get_finding(root, finding_id)
    if not isinstance(item, dict):
        return False
    last_failure = item.get("last_failure")
    if isinstance(last_failure, dict) and "changed_during_attempt" in last_failure:
        return bool(last_failure.get("changed_during_attempt"))
    last_fix = item.get("last_fix")
    if isinstance(last_fix, dict) and "changed_during_attempt" in last_fix:
        return bool(last_fix.get("changed_during_attempt"))
    return False


def _parse_checks_json(output: str) -> Path | None:
    for line in reversed(output.splitlines()):
        text = line.strip()
        if text.startswith("Checks complete: "):
            return Path(text.removeprefix("Checks complete: ").strip())
        if text.startswith("No checks found. Wrote "):
            return Path(text.removeprefix("No checks found. Wrote ").strip())
        # Legacy: bare path line ending in checks.json
        if text.endswith("checks.json") and not text.startswith(" "):
            candidate = Path(text.split()[-1])
            if candidate.suffix == ".json":
                return candidate
    return None


def _parse_verify_json(output: str) -> Path | None:
    for line in reversed(output.splitlines()):
        text = line.strip()
        if text.startswith("Verify JSON: "):
            return Path(text.removeprefix("Verify JSON: ").strip())
    return None


def run_fix_cycle(
    root: Path,
    finding_id: str,
    *,
    commit: bool,
    auto_revert: bool,
    stage_timeout: float | None = None,
) -> tuple[int, str | None, bool]:
    """Run fix/checks/verify/finalize for one finding using only current run dirs.

    Returns (exit_code, halt_status, should_retry). Stop between stages yields
    halt_status "stop_file" so callers record stop_file (never clean).
    Missing current-run artifacts yield "stage_failed" (never stale fallback).
    """
    scripts = script_dir()
    extra: dict[str, str] | None = None
    if stage_timeout is not None and stage_timeout > 0 and stage_timeout != float("inf"):
        extra = {"DESLOP_TIMEOUT_SECONDS": str(stage_timeout),
                 "DESLOP_STAGE_DEADLINE": str(time.monotonic() + stage_timeout)}
    try:
        fingerprint_before: str | None = worktree_fingerprint(root)
    except OSError:
        fingerprint_before = None

    def fingerprint_changed() -> bool:
        changed = _finding_changed_during_attempt(root, finding_id)
        if changed:
            return True
        if fingerprint_before is None:
            return True  # fail closed: unreadable before-state counts as changed
        try:
            return worktree_fingerprint(root) != fingerprint_before
        except OSError:
            return True

    if stop_requested(root):
        return 1, "stop_file", False
    fix_result = run_command(
        [str(scripts / "deslop-fix.sh"), "--allow-dirty", finding_id],
        cwd=root,
        check=False,
        extra_env=extra,
    )
    fix_run_dir = _parse_run_dir(fix_result.stdout + "\n" + fix_result.stderr)
    if fix_result.returncode != 0:
        status = get_finding_status(root, finding_id)
        if status == "unknown":
            return 1, "stage_failed", False
        if stop_requested(root):
            return 1, "stop_file", False
        if status == "accepted" and not fingerprint_changed():
            return 1, "accepted", True
        return 1, status, False
    if stop_requested(root):
        return 1, "stop_file", False
    if fix_run_dir is None or not fix_run_dir.exists():
        return 1, "stage_failed", False

    checks_result = run_command(
        [str(scripts / "deslop-run-checks.sh"), "--no-fail", finding_id],
        cwd=root,
        check=False,
        extra_env=extra,
    )
    if stop_requested(root):
        return 1, "stop_file", False
    checks_json = _parse_checks_json(checks_result.stdout)
    if checks_json is None or not checks_json.exists():
        return 1, "stage_failed", False

    verify_args = [str(scripts / "deslop-verify.sh"), "--checks-json", str(checks_json), finding_id]
    verify_result = run_command(verify_args, cwd=root, check=False, extra_env=extra)
    if stop_requested(root):
        return 1, "stop_file", False
    if verify_result.returncode != 0:
        status = get_finding_status(root, finding_id)
        if status == "unknown":
            status = "fixed_unverified"
        return 1, status, False
    verify_json = _parse_verify_json(verify_result.stdout)
    if verify_json is None or not verify_json.exists():
        return 1, "stage_failed", False

    finalize_args = [str(scripts / "deslop-finalize.py"), finding_id,
                     "--verify-json", str(verify_json), "--checks-json", str(checks_json)]
    if commit:
        finalize_args.append("--commit")
    if auto_revert:
        finalize_args.append("--auto-revert")
    result = run_command(finalize_args, cwd=root, check=False, extra_env=extra)
    if stop_requested(root):
        return 1, "stop_file", False
    if result.returncode != 0:
        status = get_finding_status(root, finding_id)
        if status == "accepted" and not fingerprint_changed():
            return 1, status, True
        return 1, status, False
    return 0, None, False


def record_outcome(
    root: Path,
    *,
    stop_reason: str,
    iterations_completed: int,
    settings,
    baseline_ids: list[str],
    halt_finding_id: str | None = None,
    halt_status: str | None = None,
    review_calls_completed: int | None = None,
    max_review_calls: int | None = None,
    elapsed_seconds: float | None = None,
    max_seconds: float | None = None,
) -> None:
    args = [
        sys.executable,
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
    if review_calls_completed is not None:
        args.extend(["--review-calls-completed", str(review_calls_completed)])
    if max_review_calls is not None:
        args.extend(["--max-review-calls", str(max_review_calls)])
    if elapsed_seconds is not None:
        args.extend(["--elapsed-seconds", str(float(elapsed_seconds))])
    if max_seconds is not None:
        args.extend(["--max-seconds", str(float(max_seconds))])
    scope = getattr(settings, "until_clean", False)
    if scope:
        args.extend(["--goal-scope", GOAL_SCOPE_UNTIL_CLEAN])
    run_command(args, cwd=root)


def print_status(root: Path) -> None:
    result = run_command([sys.executable, str(script_dir() / "deslop-status.py")], cwd=root, check=False)
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="" if result.stderr.endswith("\n") else "\n")


def create_commit_branch(root: Path) -> None:
    branch = f"deslop/{subprocess.check_output(['date', '-u', '+%Y%m%dT%H%M%SZ'], text=True).strip()}"
    run_command(["git", "checkout", "-b", branch], cwd=root)
    print(f"Created branch {branch}")


def _settings_for_goal(settings, goal: dict[str, Any]):
    """Outcome settings must match the stored goal (scope/limits)."""
    from deslop_loop_support import LoopSettings

    limits = goal.get("limits", {}) if isinstance(goal.get("limits"), dict) else {}
    try:
        max_fix = int(limits.get("max_fix_attempts", settings.max_iterations))
    except (TypeError, ValueError):
        max_fix = settings.max_iterations
    try:
        max_rev = int(limits.get("max_review_calls", settings.max_review_calls or DEFAULT_UNTIL_CLEAN_MAX_REVIEW_CALLS))
    except (TypeError, ValueError):
        max_rev = int(settings.max_review_calls or DEFAULT_UNTIL_CLEAN_MAX_REVIEW_CALLS)
    try:
        max_sec = float(limits.get("max_seconds", settings.max_seconds or DEFAULT_UNTIL_CLEAN_MAX_SECONDS))
    except (TypeError, ValueError):
        max_sec = float(settings.max_seconds or DEFAULT_UNTIL_CLEAN_MAX_SECONDS)
    return LoopSettings(
        max_iterations=max_fix,
        priority=str(goal.get("priority") or settings.priority),
        review_every=settings.review_every,
        empty_review_waves_required=int(
            goal.get("empty_sweeps_required", settings.empty_review_waves_required)
            or settings.empty_review_waves_required
        ),
        agent_timeout_seconds=settings.agent_timeout_seconds,
        agent_idle_timeout_seconds=settings.agent_idle_timeout_seconds,
        until_clean=True,
        max_review_calls=max_rev,
        max_seconds=float(max_sec),
        new_goal=False,
    )


def finish(
    root: Path,
    *,
    stop_reason: str,
    iterations_completed: int,
    settings,
    baseline_ids: list[str],
    halt_finding_id: str | None = None,
    halt_status: str | None = None,
    review_calls_completed: int | None = None,
    max_review_calls: int | None = None,
    elapsed_seconds: float | None = None,
    max_seconds: float | None = None,
    goal: dict[str, Any] | None = None,
) -> int:
    """One helper records/prints stop with counters; returns process exit code."""
    effective = _settings_for_goal(settings, goal) if goal is not None else settings
    if goal is not None:
        # Keep outcome counters aligned with the stored goal.
        consumed = goal.get("consumed", {}) if isinstance(goal.get("consumed"), dict) else {}
        if review_calls_completed is None:
            try:
                review_calls_completed = int(consumed.get("review_calls", 0) or 0)
            except (TypeError, ValueError):
                review_calls_completed = 0
        if max_review_calls is None:
            max_review_calls = effective.max_review_calls
        if elapsed_seconds is None:
            try:
                elapsed_seconds = float(consumed.get("elapsed_seconds", 0.0) or 0.0)
            except (TypeError, ValueError):
                elapsed_seconds = 0.0
        if max_seconds is None:
            max_seconds = effective.max_seconds
    try:
        record_outcome(
            root,
            stop_reason=stop_reason,
            iterations_completed=iterations_completed,
            settings=effective,
            baseline_ids=baseline_ids,
            halt_finding_id=halt_finding_id,
            halt_status=halt_status,
            review_calls_completed=review_calls_completed,
            max_review_calls=max_review_calls,
            elapsed_seconds=elapsed_seconds,
            max_seconds=max_seconds,
        )
    except SystemExit:
        return 1
    if goal is not None:
        try:
            fresh = load_state(root)
            stored = load_loop_goal(fresh)
            if stored is not None:
                stored["status"] = goal_status_for_stop_reason(stop_reason)
                stored["reason"] = stop_reason
                from deslop_loop_support import now_iso

                stored["updated_at"] = now_iso()
                # Terminal halt clears stale dirty baseline unless this cycle
                # just stored it (its timestamp is newer than invocation).
                # finish() cannot know invocation start; clearing is handled
                # by callers via maybe_clear_stale_baseline(). Keep stored.
                fresh["loop_goal"] = stored
                save_state(root, fresh)
        except OSError as exc:
            print(f"deslop-loop: error: could not persist goal status: {exc}", file=sys.stderr)
            print_status(root)
            return 1
    print_status(root)
    if stop_reason in ("stage_failed", "needs_recovery", "finalize_halt"):
        return 1
    if stop_reason in ("until_clean", "no_eligible_findings", "stop_file"):
        return 0
    # Budget exhaustion is truthful, not an error.
    return 0


def _update_goal_consumed_and_save(
    root: Path,
    *,
    fix_delta: int = 0,
    review_delta: int = 0,
    elapsed_seconds: float | None = None,
) -> dict[str, Any]:
    fresh = load_state(root)
    goal = load_loop_goal(fresh)
    if goal is None:
        return fresh
    consumed = ensure_goal_consumed(goal)
    consumed["fix_attempts"] = int(consumed.get("fix_attempts", 0) or 0) + fix_delta
    consumed["review_calls"] = int(consumed.get("review_calls", 0) or 0) + review_delta
    if elapsed_seconds is not None:
        consumed["elapsed_seconds"] = float(elapsed_seconds)
    from deslop_loop_support import now_iso

    goal["updated_at"] = now_iso()
    fresh["loop_goal"] = goal
    save_state(root, fresh)
    return fresh


def _reserve_goal_and_save(
    root: Path, *, fix_delta: int = 0, review_delta: int = 0, elapsed: float, kind: str
) -> dict[str, Any]:
    """Persist consumed reservation BEFORE child launch (crash-safe)."""
    fresh = load_state(root)
    goal = load_loop_goal(fresh)
    if goal is None:
        return fresh
    consumed = ensure_goal_consumed(goal)
    consumed["fix_attempts"] = int(consumed.get("fix_attempts", 0) or 0) + fix_delta
    consumed["review_calls"] = int(consumed.get("review_calls", 0) or 0) + review_delta
    consumed["elapsed_seconds"] = float(elapsed)
    goal["in_flight"] = {"kind": kind, "started_at": time.time(), "elapsed_at_start": float(elapsed)}
    from deslop_loop_support import now_iso

    goal["updated_at"] = now_iso()
    fresh["loop_goal"] = goal
    save_state(root, fresh)
    return fresh


def _finalize_reservation_and_save(root: Path, *, elapsed: float) -> dict[str, Any]:
    fresh = load_state(root)
    goal = load_loop_goal(fresh)
    if goal is None:
        return fresh
    consumed = ensure_goal_consumed(goal)
    consumed["elapsed_seconds"] = float(elapsed)
    goal.pop("in_flight", None)
    from deslop_loop_support import now_iso

    goal["updated_at"] = now_iso()
    fresh["loop_goal"] = goal
    save_state(root, fresh)
    return fresh


def run_reviews_until_queue_or_stop(root: Path, settings, state: dict[str, Any], fix_count: int) -> tuple[bool, int]:
    """Bounded helper: sweep partitions until queue appears or stop/empty. Returns (should_stop, fix_count)."""
    while True:
        if stop_requested(root):
            return True, fix_count
        try:
            next_id = choose_next_id(root, settings.priorities)
        except RuntimeError as exc:
            try:
                record_outcome(
                    root, stop_reason="stage_failed", iterations_completed=fix_count,
                    settings=settings, baseline_ids=[], halt_status=str(exc),
                )
            except SystemExit:
                pass
            raise
        if next_id:
            return False, fix_count

        progress = sync_partitions(root, state, ",".join(settings.priorities))
        partition = current_partition(progress)
        if partition is None:
            fail("no partitions available for review")

        if stop_requested(root):
            return True, fix_count
        print(f"Reviewing partition: {partition}")
        try:
            run_dir = run_review(root, partition)
        except SystemExit as exc:
            try:
                record_outcome(
                    root, stop_reason="stage_failed", iterations_completed=fix_count,
                    settings=settings, baseline_ids=[], halt_status="review_failed",
                )
            except SystemExit:
                pass
            raise exc
        if stop_requested(root):
            # Stop during review: never count an empty wave.
            return True, fix_count
        state = load_state(root)
        progress = sync_partitions(root, state, ",".join(settings.priorities))
        if dangling_accepted_ids(root, run_dir):
            try:
                record_outcome(
                    root, stop_reason="stage_failed", iterations_completed=fix_count,
                    settings=settings, baseline_ids=[], halt_status="stale_arbiter",
                )
            except SystemExit:
                pass
            raise SystemExit(1)
        eligible = eligible_accepted_count_from_run(root, run_dir, settings.priorities)
        progress, action = review_wave_result(
            progress=progress, accepted_count=eligible,
            empty_review_waves_required=settings.empty_review_waves_required,
        )
        state["loop_progress"] = progress
        try:
            save_state(root, state)
        except OSError as exc:
            print(f"deslop-loop: error: could not save progress: {exc}", file=sys.stderr)
            raise SystemExit(1)

        if eligible > 0:
            print(f"Review accepted {eligible} eligible finding(s); continuing loop.")
            return False, fix_count
        if stop_requested(root):
            return True, fix_count
        if action == "continue_partition":
            print(f"No eligible findings in partition {partition}; continuing to next partition.")
            continue
        if action == "continue_wave":
            print(
                "No eligible findings in review wave "
                f"({progress['consecutive_empty_review_waves']}/{settings.empty_review_waves_required}); "
                "starting another review wave."
            )
            continue
        print("No eligible accepted findings remain after consecutive empty review waves.")
        return True, fix_count


def _check_unresolved_blocking(root: Path, priorities: list[str]) -> list[dict[str, Any]]:
    return clean_blockers(root, priorities)


def execute_loop(
    root: Path, *, settings, commit: bool, auto_revert: bool, allow_dirty: bool,
) -> int:
    if not should_allow_dirty(root, allow_dirty):
        print("deslop-loop: error: git tree is dirty. Commit/stash changes or pass --allow-dirty intentionally.", file=sys.stderr)
        try:
            dirty = filtered_git_porcelain(root)
        except OSError:
            dirty = "<unreadable worktree>"
        if dirty:
            print(dirty, file=sys.stderr)
        baseline_ids = baseline_verified_ids(root)
        return finish(root, stop_reason="stage_failed", iterations_completed=0,
                      settings=settings, baseline_ids=baseline_ids, halt_status="dirty_worktree")

    if allow_dirty and has_verified_uncommitted_work(root):
        print("Continuing with verified-but-uncommitted fixes (--allow-dirty).")

    run_init(root)
    baseline_ids = baseline_verified_ids(root)
    try:
        state = load_state(root)
        sync_partitions(root, state, settings.priority)
        save_state(root, state)
    except OSError as exc:
        print(f"deslop-loop: error: could not initialize progress: {exc}", file=sys.stderr)
        return finish(root, stop_reason="stage_failed", iterations_completed=0,
                      settings=settings, baseline_ids=baseline_ids, halt_status="stage_failed")

    fix_count = 0
    cycle = 1
    try:
        partitions = state.get("loop_progress", {}).get("partitions", ["."]) or ["."]
    except AttributeError:
        partitions = ["."]
    max_cycles = max(settings.max_iterations * max(len(partitions), 1) * 4, settings.max_iterations + 4)

    while fix_count < settings.max_iterations and cycle <= max_cycles:
        if stop_requested(root):
            print("Stop file found: .deslop/stop")
            return finish(root, stop_reason="stop_file", iterations_completed=fix_count,
                          settings=settings, baseline_ids=baseline_ids)

        try:
            next_id = choose_next_id(root, settings.priorities)
        except RuntimeError as exc:
            print(f"deslop-loop: stage failed: {exc}", file=sys.stderr)
            return finish(root, stop_reason="stage_failed", iterations_completed=fix_count,
                          settings=settings, baseline_ids=baseline_ids, halt_status="next_failed")
        if next_id is None:
            try:
                should_stop, fix_count = run_reviews_until_queue_or_stop(root, settings, state, fix_count)
            except SystemExit:
                print_status(root)
                return 1
            try:
                state = load_state(root)
            except OSError as exc:
                print(f"deslop-loop: error: could not reload state: {exc}", file=sys.stderr)
                return 1
            if should_stop:
                blocking = _check_unresolved_blocking(root, settings.priorities)
                if blocking:
                    ids = ",".join(str(item.get("id")) for item in blocking[:5])
                    print(f"Unresolved findings require explicit recovery: {ids}", file=sys.stderr)
                    return finish(root, stop_reason="needs_recovery", iterations_completed=fix_count,
                                  settings=settings, baseline_ids=baseline_ids,
                                  halt_finding_id=str(blocking[0].get("id")),
                                  halt_status=str(blocking[0].get("status")))
                if stop_requested(root):
                    return finish(root, stop_reason="stop_file", iterations_completed=fix_count,
                                  settings=settings, baseline_ids=baseline_ids)
                return finish(root, stop_reason="no_eligible_findings", iterations_completed=fix_count,
                              settings=settings, baseline_ids=baseline_ids)
            try:
                next_id = choose_next_id(root, settings.priorities)
            except RuntimeError as exc:
                print(f"deslop-loop: stage failed: {exc}", file=sys.stderr)
                return finish(root, stop_reason="stage_failed", iterations_completed=fix_count,
                              settings=settings, baseline_ids=baseline_ids, halt_status="next_failed")
            if next_id is None:
                cycle += 1
                continue

        if stop_requested(root):
            print("Stop file found: .deslop/stop")
            return finish(root, stop_reason="stop_file", iterations_completed=fix_count,
                          settings=settings, baseline_ids=baseline_ids)

        if not should_allow_dirty(root, allow_dirty):
            print("deslop-loop: error: worktree changed outside loop; pass --allow-dirty to proceed.", file=sys.stderr)
            return finish(root, stop_reason="stage_failed", iterations_completed=fix_count,
                          settings=settings, baseline_ids=baseline_ids,
                          halt_finding_id=next_id, halt_status="dirty_worktree")

        print(f"Iteration {fix_count + 1}: fixing {next_id}")
        try:
            code, halt_status, should_retry = run_fix_cycle(root, next_id, commit=commit, auto_revert=auto_revert)
        except SystemExit as exc:
            return finish(root, stop_reason="stage_failed", iterations_completed=fix_count,
                          settings=settings, baseline_ids=baseline_ids,
                          halt_finding_id=next_id, halt_status="stage_failed")
        if halt_status == "stop_file":
            return finish(root, stop_reason="stop_file", iterations_completed=fix_count,
                          settings=settings, baseline_ids=baseline_ids,
                          halt_finding_id=next_id, halt_status="stop_file")
        if code != 0:
            if should_retry and halt_status == "accepted":
                print(f"Fix attempt for {next_id} failed safely; retrying within budgets.")
                cycle += 1
                continue
            print(f"Finalize stopped the loop for {next_id}.", file=sys.stderr)
            stop_reason = "finalize_halt"
            if halt_status in ("needs_human", "blocked", "fixed_unverified", "fixing", "stage_failed"):
                stop_reason = "needs_recovery" if halt_status in ("needs_human", "blocked", "fixed_unverified", "fixing") else "stage_failed"
            return finish(root, stop_reason=stop_reason, iterations_completed=fix_count + 1,
                          settings=settings, baseline_ids=baseline_ids,
                          halt_finding_id=next_id, halt_status=halt_status)

        fix_count += 1
        try:
            fingerprint = worktree_fingerprint(root)
            fresh = load_state(root)
            store_dirty_baseline(fresh, fingerprint, next_id)
            progress = fresh.get("loop_progress", {})
            if isinstance(progress, dict):
                progress["consecutive_empty_review_waves"] = 0
                fresh["loop_progress"] = progress
            save_state(root, fresh)
            state = fresh
        except OSError as exc:
            print(f"deslop-loop: error: could not record post-fix baseline: {exc}", file=sys.stderr)
            return finish(root, stop_reason="stage_failed", iterations_completed=fix_count,
                          settings=settings, baseline_ids=baseline_ids,
                          halt_finding_id=next_id, halt_status="stage_failed")
        cycle += 1

    return finish(root, stop_reason="max_iterations_reached", iterations_completed=fix_count,
                  settings=settings, baseline_ids=baseline_ids)


def _elapsed_for_goal(goal: dict[str, Any], invocation_start: float, base_elapsed: float) -> float:
    try:
        return float(base_elapsed) + (time.monotonic() - invocation_start)
    except OSError:
        return float(base_elapsed)


def ensure_until_clean_goal(root: Path, settings, *, new_goal: bool) -> tuple[dict[str, Any], bool]:
    """Load or create the persistent until-clean goal. Never silently renews."""
    state = load_state(root)
    existing = load_loop_goal(state)
    if new_goal:
        partitions = partition_paths(root)
        goal = create_loop_goal(
            priority=settings.priority,
            max_fix_attempts=settings.max_iterations,
            max_review_calls=int(settings.max_review_calls or DEFAULT_UNTIL_CLEAN_MAX_REVIEW_CALLS),
            max_seconds=float(settings.max_seconds or DEFAULT_UNTIL_CLEAN_MAX_SECONDS),
            partitions=partitions,
        )
        goal["empty_sweeps_required"] = settings.empty_review_waves_required
        state["loop_goal"] = goal
        clear_dirty_baseline(state)
        progress = sync_partitions(root, state, settings.priority)
        reset_coverage_for_new_sweep(state)
        try:
            fp = content_fingerprint(root)
        except OSError:
            fp = ""
        if fp:
            note_coverage_content(state, fp)
            goal["completed_fingerprint"] = None
        goal["empty_sweeps_required"] = settings.empty_review_waves_required
        state["loop_goal"] = goal
        save_state(root, state)
        return goal, True
    if existing is not None and existing.get("scope") == GOAL_SCOPE_UNTIL_CLEAN:
        ensure_goal_consumed(existing)
        if existing.get("status") not in ("active", None):
            return existing, False
        stored_priority = str(existing.get("priority") or settings.priority)
        try:
            validate_priorities(stored_priority)
        except ValueError:
            stored_priority = settings.priority
        if settings.priority != stored_priority:
            print(f"Preserving stored goal priority {stored_priority} (requested {settings.priority}); use --new-goal to reset.")
        sync_partitions(root, state, stored_priority)
        save_state(root, state)
        fresh = load_state(root)
        goal = load_loop_goal(fresh) or existing
        return goal, False
    partitions = partition_paths(root)
    goal = create_loop_goal(
        priority=settings.priority,
        max_fix_attempts=settings.max_iterations,
        max_review_calls=int(settings.max_review_calls or DEFAULT_UNTIL_CLEAN_MAX_REVIEW_CALLS),
        max_seconds=float(settings.max_seconds or DEFAULT_UNTIL_CLEAN_MAX_SECONDS),
        partitions=partitions,
    )
    goal["empty_sweeps_required"] = settings.empty_review_waves_required
    state["loop_goal"] = goal
    clear_dirty_baseline(state)
    progress = sync_partitions(root, state, settings.priority)
    reset_coverage_for_new_sweep(state)
    try:
        fp = content_fingerprint(root)
    except OSError:
        fp = ""
    if fp:
        note_coverage_content(state, fp)
    goal["empty_sweeps_required"] = settings.empty_review_waves_required
    state["loop_goal"] = goal
    save_state(root, state)
    return goal, True


def _goal_limits(goal: dict[str, Any], settings):
    limits = goal.get("limits", {}) if isinstance(goal.get("limits"), dict) else {}
    try:
        max_fix = int(limits.get("max_fix_attempts", settings.max_iterations))
    except (TypeError, ValueError):
        max_fix = settings.max_iterations
    try:
        max_rev = int(limits.get("max_review_calls", settings.max_review_calls or DEFAULT_UNTIL_CLEAN_MAX_REVIEW_CALLS))
    except (TypeError, ValueError):
        max_rev = int(settings.max_review_calls or DEFAULT_UNTIL_CLEAN_MAX_REVIEW_CALLS)
    try:
        max_sec = float(limits.get("max_seconds", settings.max_seconds or DEFAULT_UNTIL_CLEAN_MAX_SECONDS))
    except (TypeError, ValueError):
        max_sec = float(settings.max_seconds or DEFAULT_UNTIL_CLEAN_MAX_SECONDS)
    required = int(goal.get("empty_sweeps_required", UNTIL_CLEAN_REQUIRED_EMPTY_SWEEPS) or UNTIL_CLEAN_REQUIRED_EMPTY_SWEEPS)
    return max_fix, max_rev, max_sec, required


def execute_until_clean(root: Path, *, settings, commit: bool, auto_revert: bool, allow_dirty: bool) -> int:
    try:
        start_state = load_state(root)
        start_goal = load_loop_goal(start_state)
        goal_created = str((start_goal or {}).get("created_at") or "")
    except OSError:
        goal_created = ""
    if not should_allow_dirty(root, allow_dirty, goal_created or None):
        print("deslop-loop: error: git tree is dirty. Commit/stash changes or pass --allow-dirty intentionally.", file=sys.stderr)
        try:
            dirty = filtered_git_porcelain(root)
        except OSError:
            dirty = "<unreadable worktree>"
        if dirty:
            print(dirty, file=sys.stderr)
        baseline_ids = baseline_verified_ids(root)
        try:
            state = load_state(root)
            goal = load_loop_goal(state)
        except OSError:
            goal = None
        return finish(root, stop_reason="stage_failed", iterations_completed=0,
                      settings=settings, baseline_ids=baseline_ids, halt_status="dirty_worktree",
                      review_calls_completed=0, max_review_calls=settings.max_review_calls,
                      elapsed_seconds=0.0, max_seconds=settings.max_seconds, goal=goal)
    if allow_dirty and has_verified_uncommitted_work(root):
        print("Continuing with verified-but-uncommitted fixes (--allow-dirty).")
    run_init(root)
    baseline_ids = baseline_verified_ids(root)
    try:
        state = load_state(root)
    except OSError as exc:
        print(f"deslop-loop: error: could not load state: {exc}", file=sys.stderr)
        return 1
    goal = load_loop_goal(state)
    if goal is None:
        goal, _ = ensure_until_clean_goal(root, settings, new_goal=False)
        try:
            state = load_state(root)
        except OSError as exc:
            print(f"deslop-loop: error: could not reload state: {exc}", file=sys.stderr)
            return 1
        goal = load_loop_goal(state) or goal
    ensure_goal_consumed(goal)
    # Crash checkpoint: wall time in a persisted in-flight marker counts.
    base_elapsed = effective_consumed_elapsed(goal)
    invocation_start = time.monotonic()
    goal_priority_str = str(goal.get("priority") or settings.priority)
    try:
        goal_priorities = validate_priorities(goal_priority_str)
    except ValueError:
        goal_priorities = settings.priorities
        goal_priority_str = settings.priority
    try:
        state = load_state(root)
        sync_partitions(root, state, goal_priority_str)
        save_state(root, state)
    except OSError as exc:
        print(f"deslop-loop: error: could not sync partitions: {exc}", file=sys.stderr)
        return finish(root, stop_reason="stage_failed", iterations_completed=0,
                      settings=settings, baseline_ids=baseline_ids, halt_status="stage_failed",
                      goal=goal)
    # Seed coverage content fingerprint for sweep-restart detection.
    try:
        current_fp = content_fingerprint(root)
        st = load_state(root)
        prog = st.get("loop_progress", {})
        if isinstance(prog, dict) and not prog.get("coverage_content_fingerprint"):
            note_coverage_content(st, current_fp)
            save_state(root, st)
    except OSError:
        pass

    def current_elapsed() -> float:
        return _elapsed_for_goal(goal, invocation_start, base_elapsed)

    def agent_timeout_for(remaining: float) -> float:
        candidate: float | None = None
        try:
            if settings.agent_timeout_seconds is not None:
                candidate = float(settings.agent_timeout_seconds)
        except (TypeError, ValueError):
            candidate = None
        if candidate is None or candidate <= 0:
            return remaining
        return min(candidate, remaining)

    def stop(reason: str, **overrides) -> int:
        outcome = dict(iterations_completed=fix_used, settings=settings, baseline_ids=baseline_ids,
                       review_calls_completed=review_used, max_review_calls=max_rev,
                       elapsed_seconds=elapsed, max_seconds=max_sec, goal=goal)
        outcome.update(overrides)
        return finish(root, stop_reason=reason, **outcome)

    while True:
        try:
            state = load_state(root)
        except OSError as exc:
            print(f"deslop-loop: error: could not reload state: {exc}", file=sys.stderr)
            return 1
        goal = load_loop_goal(state) or goal
        ensure_goal_consumed(goal)
        consumed = goal.get("consumed", {})
        fix_used = int(consumed.get("fix_attempts", 0) or 0)
        review_used = int(consumed.get("review_calls", 0) or 0)
        elapsed = current_elapsed()
        try:
            _update_goal_consumed_and_save(root, elapsed_seconds=elapsed)
        except OSError as exc:
            print(f"deslop-loop: error: could not persist elapsed: {exc}", file=sys.stderr)
            return stop("stage_failed", halt_status="stage_failed")
        max_fix, max_rev, max_sec, required = _goal_limits(goal, settings)

        if stop_requested(root):
            print("Stop file found: .deslop/stop")
            return stop("stop_file")

        # Content/partition/scope changes restart a complete sweep.
        try:
            sync_state = load_state(root)
            progress = sync_partitions(root, sync_state, goal_priority_str)
            fp_now = content_fingerprint(root)
            stored_cov = str(progress.get("coverage_content_fingerprint") or "")
            if stored_cov and stored_cov != fp_now:
                reset_coverage_for_new_sweep(sync_state, fp_now)
                progress = sync_state["loop_progress"]
            elif not stored_cov:
                note_coverage_content(sync_state, fp_now)
                progress = sync_state["loop_progress"]
            save_state(root, sync_state)
            state = sync_state
        except OSError as exc:
            print(f"deslop-loop: error: worktree unreadable; refusing clean: {exc}", file=sys.stderr)
            return stop("stage_failed", halt_status="stage_failed")
        waves = int(progress.get("consecutive_empty_review_waves", 0) or 0)

        # Clean gate BEFORE budget halts: budgets mean "cannot start another
        # stage", never "clean at cap is exhausted".
        if waves >= required:
            if inventory_is_truncated(root):
                print("Inventory truncated: refusing until-clean claim.", file=sys.stderr)
                return stop("stage_failed", halt_status="inventory_truncated")
            blocking = _check_unresolved_blocking(root, goal_priorities)
            if blocking:
                ids = ",".join(str(item.get("id")) for item in blocking[:5])
                print(f"Unresolved findings require explicit recovery: {ids}", file=sys.stderr)
                return stop("needs_recovery", halt_finding_id=str(blocking[0].get("id")), halt_status=str(blocking[0].get("status")))
            try:
                recheck = choose_next_id(root, goal_priorities)
            except RuntimeError as exc:
                print(f"deslop-loop: stage failed: {exc}", file=sys.stderr)
                return stop("stage_failed", halt_status="next_failed")
            if recheck is None:
                print(f"Clean: {required} complete empty sweeps of all partitions at {goal_priority_str} (scope-limited).")
                try:
                    fp_done = content_fingerprint(root)
                except OSError as exc:
                    print(f"deslop-loop: error: could not fingerprint clean tree: {exc}", file=sys.stderr)
                    return stop("stage_failed", halt_status="stage_failed")
                code = stop("until_clean")
                try:
                    st = load_state(root)
                    g = load_loop_goal(st)
                    if g is not None:
                        g["completed_fingerprint"] = fp_done
                        st["loop_goal"] = g
                        save_state(root, st)
                except OSError as exc:
                    print(f"deslop-loop: error: could not persist completion fingerprint: {exc}", file=sys.stderr)
                    return 1
                return code
            # Queue reappeared after sweeps: fall through to fix path.

        if elapsed >= max_sec:
            print(f"Time budget exhausted: {elapsed:.1f}s >= {max_sec:.1f}s")
            return stop("max_seconds_reached")
        if fix_used >= max_fix:
            print(f"Fix budget exhausted: {fix_used} >= {max_fix}")
            return stop("max_iterations_reached")
        if review_used >= max_rev:
            print(f"Review budget exhausted: {review_used} >= {max_rev}")
            return stop("max_review_calls_reached")

        remaining = max_sec - elapsed
        if remaining <= 0:
            print(f"Time budget exhausted: {elapsed:.1f}s >= {max_sec:.1f}s")
            return stop("max_seconds_reached")

        try:
            next_id = choose_next_id(root, goal_priorities)
        except RuntimeError as exc:
            print(f"deslop-loop: stage failed: {exc}", file=sys.stderr)
            return stop("stage_failed", halt_status="next_failed")

        if next_id is not None:
            if stop_requested(root):
                print("Stop file found: .deslop/stop")
                return stop("stop_file")
            try:
                goal_c = str(goal.get("created_at") or "")
            except AttributeError:
                goal_c = ""
            if not should_allow_dirty(root, allow_dirty, goal_c or None):
                print("deslop-loop: error: worktree changed outside loop; pass --allow-dirty to proceed.", file=sys.stderr)
                return stop("stage_failed", halt_finding_id=next_id, halt_status="dirty_worktree")
            print(f"Fix attempt {fix_used + 1}/{max_fix}: fixing {next_id} (scope {goal_priority_str})")
            reserve_elapsed = current_elapsed()
            try:
                _reserve_goal_and_save(root, fix_delta=1, elapsed=reserve_elapsed, kind="fix")
            except OSError as exc:
                print(f"deslop-loop: error: could not reserve fix budget: {exc}", file=sys.stderr)
                return stop("stage_failed", halt_finding_id=next_id, halt_status="stage_failed", elapsed_seconds=reserve_elapsed)
            stage_timeout = agent_timeout_for(max_sec - reserve_elapsed)
            if stage_timeout <= 0:
                try:
                    _finalize_reservation_and_save(root, elapsed=current_elapsed())
                except OSError:
                    pass
                print(f"Time budget exhausted: {current_elapsed():.1f}s >= {max_sec:.1f}s")
                return stop("max_seconds_reached", iterations_completed=fix_used + 1, review_calls_completed=review_used + 0, elapsed_seconds=current_elapsed())
            try:
                code, halt_status, should_retry = run_fix_cycle(
                    root, next_id, commit=commit, auto_revert=auto_revert, stage_timeout=stage_timeout)
            except SystemExit as exc:
                try:
                    _finalize_reservation_and_save(root, elapsed=current_elapsed())
                except OSError:
                    pass
                return stop("stage_failed", halt_finding_id=next_id, halt_status="stage_failed", review_calls_completed=review_used + 1, elapsed_seconds=current_elapsed())
            try:
                _finalize_reservation_and_save(root, elapsed=current_elapsed())
            except OSError as exc:
                print(f"deslop-loop: error: could not finalize fix reservation: {exc}", file=sys.stderr)
                return stop("stage_failed", iterations_completed=fix_used + 1, halt_finding_id=next_id, halt_status="stage_failed")
            try:
                state = load_state(root)
                goal = load_loop_goal(state) or goal
            except OSError:
                pass
            if halt_status == "stop_file":
                print("Stop file found: .deslop/stop")
                return stop("stop_file", iterations_completed=fix_used + 1, halt_finding_id=next_id, halt_status="stop_file")
            if code != 0:
                if should_retry and halt_status == "accepted":
                    print(f"Fix attempt for {next_id} failed safely; retrying within budgets.")
                    continue
                print(f"Finalize stopped the loop for {next_id}.", file=sys.stderr)
                stop_reason = "finalize_halt"
                if halt_status in ("needs_human", "blocked", "fixed_unverified", "fixing"):
                    stop_reason = "needs_recovery"
                elif halt_status in ("stage_failed",):
                    stop_reason = "stage_failed"
                return stop(stop_reason, iterations_completed=fix_used + 1, halt_finding_id=next_id, halt_status=halt_status)
            try:
                fingerprint = worktree_fingerprint(root)
                fresh = load_state(root)
                store_dirty_baseline(fresh, fingerprint, next_id, str(goal.get("created_at") or ""))
                progress = fresh.get("loop_progress", {})
                if isinstance(progress, dict):
                    progress["consecutive_empty_review_waves"] = 0
                    fresh["loop_progress"] = progress
                try:
                    note_coverage_content(fresh, fingerprint)
                except AttributeError:
                    pass
                save_state(root, fresh)
            except OSError as exc:
                print(f"deslop-loop: error: could not record post-fix baseline: {exc}", file=sys.stderr)
                return stop("stage_failed", iterations_completed=fix_used + 1, halt_finding_id=next_id, halt_status="stage_failed")
            continue

        # No eligible queue: need complete sweeps.
        try:
            state = load_state(root)
            progress = sync_partitions(root, state, goal_priority_str)
            save_state(root, state)
        except OSError as exc:
            print(f"deslop-loop: error: could not sync partitions: {exc}", file=sys.stderr)
            return stop("stage_failed", halt_status="stage_failed")
        waves = int(progress.get("consecutive_empty_review_waves", 0) or 0)

        if stop_requested(root):
            print("Stop file found: .deslop/stop")
            return stop("stop_file")

        partition = current_partition(progress)
        if partition is None:
            return stop("stage_failed", halt_status="no_partitions")
        print(f"Reviewing partition: {partition} ({waves}/{required} empty sweeps, scope {goal_priority_str})")
        reserve_elapsed = current_elapsed()
        try:
            _reserve_goal_and_save(root, review_delta=1, elapsed=reserve_elapsed, kind="review")
        except OSError as exc:
            print(f"deslop-loop: error: could not reserve review budget: {exc}", file=sys.stderr)
            return stop("stage_failed", halt_status="stage_failed", elapsed_seconds=reserve_elapsed)
        stage_timeout = agent_timeout_for(max_sec - reserve_elapsed)
        if stage_timeout <= 0:
            try:
                _finalize_reservation_and_save(root, elapsed=current_elapsed())
            except OSError:
                pass
            print(f"Time budget exhausted: {current_elapsed():.1f}s >= {max_sec:.1f}s")
            return stop("max_seconds_reached", review_calls_completed=review_used + 1, elapsed_seconds=current_elapsed())
        try:
            run_dir = run_review(root, partition, stage_timeout=stage_timeout)
        except SystemExit:
            try:
                _finalize_reservation_and_save(root, elapsed=current_elapsed())
            except OSError:
                pass
            try:
                fresh_elapsed = current_elapsed()
            except OSError:
                fresh_elapsed = reserve_elapsed
            return stop("stage_failed", halt_status="review_failed", review_calls_completed=review_used + 1, elapsed_seconds=fresh_elapsed)
        try:
            _finalize_reservation_and_save(root, elapsed=current_elapsed())
        except OSError as exc:
            print(f"deslop-loop: error: could not finalize review reservation: {exc}", file=sys.stderr)
            return stop("stage_failed", halt_status="stage_failed")
        try:
            state = load_state(root)
            goal = load_loop_goal(state) or goal
        except OSError:
            pass
        if stop_requested(root):
            print("Stop file found: .deslop/stop")
            return stop("stop_file")
        if dangling_accepted_ids(root, run_dir):
            print("Review arbiter references unknown findings; refusing stale wave.", file=sys.stderr)
            return stop("stage_failed", halt_status="stale_arbiter")
        try:
            eligible = eligible_accepted_count_from_run(root, run_dir, goal_priorities)
        except OSError as exc:
            print(f"deslop-loop: error: could not count eligible findings: {exc}", file=sys.stderr)
            return stop("stage_failed", halt_status="stage_failed")
        try:
            progress = sync_partitions(root, state, goal_priority_str)
            progress, action = review_wave_result(
                progress=progress, accepted_count=eligible, empty_review_waves_required=required)
            state["loop_progress"] = progress
            save_state(root, state)
        except OSError as exc:
            print(f"deslop-loop: error: could not save review wave: {exc}", file=sys.stderr)
            return stop("stage_failed", halt_status="stage_failed")
        if eligible > 0:
            print(f"Review accepted {eligible} eligible finding(s); continuing loop.")
            continue
        if stop_requested(root):
            print("Stop file found: .deslop/stop")
            return stop("stop_file")
        if action == "continue_partition":
            print(f"No eligible findings in partition {partition}; continuing to next partition.")
            continue
        if action == "continue_wave":
            print(f"No eligible findings in review wave ({progress['consecutive_empty_review_waves']}/{required}); starting another review wave.")
            continue
        continue


def should_continue(root: Path, settings) -> bool:
    if stop_requested(root):
        return False
    if getattr(settings, "until_clean", False):
        try:
            state = load_state(root)
        except OSError:
            return False
        goal = load_loop_goal(state)
        if goal is not None and goal.get("scope") == GOAL_SCOPE_UNTIL_CLEAN:
            status = str(goal.get("status") or "active")
            if status != "active":
                return False
            return True
    try:
        if choose_next_id(root, settings.priorities):
            return True
    except RuntimeError:
        return True
    try:
        state = load_state(root)
        progress = sync_partitions(root, state, settings.priority)
    except OSError:
        return False
    return int(progress.get("consecutive_empty_review_waves", 0) or 0) < settings.empty_review_waves_required


def _stored_until_clean_goal(root: Path) -> dict[str, Any] | None:
    try:
        state = load_state(root)
    except OSError:
        return None
    goal = load_loop_goal(state)
    if isinstance(goal, dict) and goal.get("scope") == GOAL_SCOPE_UNTIL_CLEAN:
        return goal
    return None


def _completed_fingerprint_stale(root: Path, goal: dict[str, Any]) -> bool:
    if str(goal.get("status") or "") != "complete":
        return False
    stored = goal.get("completed_fingerprint")
    if not isinstance(stored, str) or not stored:
        return True
    try:
        current = content_fingerprint(root)
    except OSError:
        return True
    return current != stored


def _reopen_stale_complete(root: Path, goal: dict[str, Any]) -> None:
    try:
        state = load_state(root)
        stored = load_loop_goal(state)
        if stored is None:
            return
        stored["status"] = "active"
        stored["reason"] = "content_changed"
        from deslop_loop_support import now_iso

        stored["updated_at"] = now_iso()
        reset_coverage_for_new_sweep(state)
        try:
            fp = content_fingerprint(root)
            note_coverage_content(state, fp)
        except OSError:
            pass
        state["loop_goal"] = stored
        save_state(root, state)
    except OSError as exc:
        print(f"deslop-loop: error: could not reopen stale complete goal: {exc}", file=sys.stderr)
        raise SystemExit(1)


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
    parser.add_argument("--until-clean", action="store_true", help="run until two complete empty sweeps at P0,P1,P2 with finite budgets")
    parser.add_argument("--max-review-calls", type=int, help="until-clean review budget (default 200)")
    parser.add_argument("--max-seconds", type=float, help="until-clean time budget in seconds (default 28800)")
    parser.add_argument("--new-goal", action="store_true", help="reset the persistent until-clean goal with explicit semantics")
    parser.add_argument(
        "--agent-timeout-seconds",
        type=float,
        help="wall-clock cap per agent call (default 5400; env DESLOP_TIMEOUT_SECONDS overrides)",
    )
    parser.add_argument(
        "--agent-idle-timeout-seconds",
        type=float,
        help="kill agent after this many seconds without stdout (default 1200; 0 disables; env DESLOP_IDLE_TIMEOUT_SECONDS overrides)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = repo_root()
    if args.new_goal and not args.until_clean:
        fail("--new-goal requires --until-clean")
    if (args.max_review_calls is not None or args.max_seconds is not None) and not args.until_clean:
        # Bare --continue may carry stored until-clean budgets; explicit budget
        # flags without --until-clean are still rejected unless this is a
        # bare continue that infers until-clean below.
        if not (args.continue_loop and args.max_review_calls is None and args.max_seconds is None):
            fail("--max-review-calls/--max-seconds require --until-clean")
    if args.until_clean and args.empty_review_waves is not None:
        try:
            validate_positive_int("empty_review_waves", args.empty_review_waves)
        except ValueError as exc:
            fail(str(exc))

    # Exclusive lock BEFORE configure/initialize so concurrent invocations
    # cannot race on config/state. Kernel flock auto-releases on crash.
    try:
        acquire_loop_lock(root)
    except RuntimeError as exc:
        print(f"deslop-loop: error: {exc}", file=sys.stderr)
        return 1
    try:
        stored = _stored_until_clean_goal(root)
        inferred_until_clean = False
        if not args.until_clean and args.continue_loop and stored is not None:
            # deslop-continue.sh passes ONLY --continue: infer existing
            # until-clean goal instead of bypassing budgets via bounded mode.
            inferred_until_clean = True
        if not args.until_clean and not args.continue_loop and stored is not None:
            # Explicit bounded invocation must not bypass an active/exhausted
            # until-clean goal.
            status = str(stored.get("status") or "active")
            if status in ("active", "exhausted", "failed"):
                print(
                    f"deslop-loop: error: until-clean goal exists (status={status}); "
                    "use --until-clean --continue or --until-clean --new-goal instead of bounded invocation.",
                    file=sys.stderr,
                )
                print_status(root)
                return 1
        effective_until_clean = bool(args.until_clean) or inferred_until_clean
        persist = (not args.no_persist_config) and not inferred_until_clean

        try:
            settings = resolve_settings(
                root,
                max_iterations=args.max_iterations,
                priority=args.priority,
                review_every=args.review_every,
                empty_review_waves_required=args.empty_review_waves,
                agent_timeout_seconds=args.agent_timeout_seconds,
                agent_idle_timeout_seconds=args.agent_idle_timeout_seconds,
                persist=persist,
                until_clean=effective_until_clean,
                max_review_calls=args.max_review_calls,
                max_seconds=args.max_seconds,
                new_goal=bool(args.new_goal),
            )
        except ValueError as exc:
            fail(str(exc))

        if effective_until_clean:
            try:
                goal, _created = ensure_until_clean_goal(root, settings, new_goal=bool(args.new_goal))
            except ValueError as exc:
                fail(str(exc))
            status = str(goal.get("status") or "active")
            if status == "complete" and _completed_fingerprint_stale(root, goal):
                print("Completed goal content changed; reopening for a complete re-sweep.")
                _reopen_stale_complete(root, goal)
                try:
                    fresh = load_state(root)
                    goal = load_loop_goal(fresh) or goal
                except OSError:
                    pass
                status = str(goal.get("status") or "active")
            if args.continue_loop and status in {"failed", "stopped"} and not stop_requested(root):
                if not _check_unresolved_blocking(root, validate_priorities(goal["priority"])):
                    goal["status"], goal["reason"] = "active", None
                    fresh = load_state(root)
                    fresh["loop_goal"] = goal
                    save_state(root, fresh)
                    status = "active"
            if status != "active":
                print(f"Preserved until-clean goal: status={status} reason={goal.get('reason')}")
                print_status(root)
                return 0 if status in ("complete", "stopped") else 1
            if args.commit:
                create_commit_branch(root)
            return execute_until_clean(root, settings=settings, commit=args.commit,
                                       auto_revert=args.auto_revert, allow_dirty=args.allow_dirty)

        if args.continue_loop and not should_continue(root, settings):
            print_status(root)
            return 0

        if args.commit:
            create_commit_branch(root)

        return execute_loop(root, settings=settings, commit=args.commit,
                            auto_revert=args.auto_revert, allow_dirty=args.allow_dirty)
    finally:
        release_loop_lock(root)


if __name__ == "__main__":
    raise SystemExit(main())
