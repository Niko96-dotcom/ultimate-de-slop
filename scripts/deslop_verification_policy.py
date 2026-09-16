"""Shared deterministic evidence requirements for verifier and finalizer."""
from pathlib import PurePosixPath
from typing import Any


def finding_changed_files(finding: dict[str, Any]) -> list[str]:
    raw = (finding.get("last_fix") or {}).get("changed_files", [])
    return raw if isinstance(raw, list) and all(isinstance(p, str) for p in raw) else []


def is_docs_only_path(value: str) -> bool:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        return False
    # A docs directory can contain executable code. Suffixes such as .txt can
    # be runtime inputs, so they are not automatically documentation either.
    return path.suffix.lower() in {".md", ".markdown", ".rst", ".adoc"} or path.name.lower() in {
        "readme", "license", "licence", "changelog", "contributing",
    }


def is_docs_only_exception(finding: dict[str, Any], verify: dict[str, Any] | None) -> bool:
    changed = finding_changed_files(finding)
    explanation = any(str(finding.get(key) or "").strip() for key in (
        "expected_checks_explanation", "no_expected_checks_reason", "checks_explanation",
    ))
    evidence = (verify or {}).get("evidence", [])
    return bool(changed and all(is_docs_only_path(p) for p in changed)
                and not finding.get("expected_checks") and explanation
                and isinstance(evidence, list) and any("doc" in str(item).lower() for item in evidence))


def checks_failed(checks: dict[str, Any] | None, *, finding_id: str,
                  finding: dict[str, Any], verify: dict[str, Any] | None) -> bool:
    if not isinstance(checks, dict) or checks.get("finding_id") != finding_id:
        return True
    results = checks.get("results")
    if checks.get("status") == "skipped" and results == []:
        return not is_docs_only_exception(finding, verify)
    if checks.get("status") != "passed" or not isinstance(results, list) or not results:
        return True
    return any(not isinstance(row, dict) or row.get("status") != "passed"
               or type(row.get("exit_code")) is not int or row["exit_code"] != 0
               or not str(row.get("command") or "").strip() for row in results)


def proof_matches(artifact, finding, root) -> bool:
    from deslop_snapshot import snapshot
    run = (finding.get("last_fix") or {}).get("run_dir")
    # Older manually-created finding records have no attempt identifier.
    if not run:
        return True
    return (isinstance(artifact, dict) and artifact.get("finding_id") == finding.get("id")
            and artifact.get("fix_run") == run and artifact.get("worktree_manifest") == snapshot(root))
