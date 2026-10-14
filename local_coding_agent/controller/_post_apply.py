from __future__ import annotations

import hashlib
from pathlib import Path
from threading import Event

from typing import Any

from ..repository_tools import BoundedRepositoryTools, ToolCancelled, ToolPolicyError
from ..semantic_linter import lint_patch_in_memory
from ..task import TaskEnvelope
from ..validators import _normalize_task_path, apply_patch, check_patch_applies, parse_unified_diff


def run_post_apply_checks(
    task: TaskEnvelope,
    tools: BoundedRepositoryTools,
    *,
    active_cancel: Event | None,
    audit: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool]:
    """Run each allowlisted check against the already-applied workspace.

    Module-level so the mediated apply path in ``DelegationService`` reuses the
    exact same evidence contract without re-entering the model loop. Raises
    ``ToolCancelled`` when cancellation is requested between checks.
    """
    checks: list[dict[str, Any]] = []
    if not task.checks:
        raise ToolPolicyError("at least one targeted check is required before apply")
    for command in task.checks:
        if active_cancel is not None and active_cancel.is_set():
            raise ToolCancelled("task was cancelled")
        check = tools.execute("run_tests", {"command": command})
        observed = {
            "command": command,
            "passed": check["passed"],
            "evidence": check["evidence"],
            "stdout": check.get("stdout", ""),
            "stderr": check.get("stderr", ""),
            "exit_code": check.get("exit_code"),
        }
        checks.append(observed)
        audit.append(
            {
                "event": "post_apply_check",
                "command": command,
                "passed": check["passed"],
                "exit_code": check.get("exit_code"),
                "stdout": check.get("stdout", ""),
                "stderr": check.get("stderr", ""),
            }
        )
    return checks, all(check["passed"] for check in checks)


def add_risk(result: dict[str, Any], kind: str, message: str) -> None:
    risks = result.get("risks")
    if not isinstance(risks, list):
        risks = []
        result["risks"] = risks
    risks.append({"kind": kind, "message": message})


def apply_failure(kind: str, message: str, audit: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "status": "failed",
        "error": {"kind": kind, "message": message},
        "audit": [*audit, {"event": "apply_rejected", "kind": kind}],
    }


def _file_digest(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None
    except OSError:
        return "unreadable"


def _baseline(workspace: Path, changed: tuple[str, ...]) -> dict[str, str | None]:
    return {name: _file_digest(workspace / name) for name in changed}


def _restored(workspace: Path, baseline: dict[str, str | None]) -> bool:
    return all(_file_digest(workspace / name) == digest for name, digest in baseline.items())


def mediated_apply(
    workspace: Path,
    patch: str,
    task: TaskEnvelope,
    *,
    cancel_event: Event | None = None,
    audit: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Apply a reviewed patch under the full verification gate.

    Single controller-owned implementation shared by the service, CLI and
    desktop apply paths: refuse a patch with no targeted check, enforce the
    declared file scope, pre-check and lint the patch, apply it, run the
    allowlisted checks, and roll back to the recorded baseline on any failure.
    A rollback is only reported as successful when the baseline digests are
    actually restored; otherwise the workspace is reported as modified/unknown.
    """

    audit = list(audit) if audit is not None else []
    if not isinstance(patch, str) or not patch.strip():
        return apply_failure("no_patch", "proposal carries no resolved patch", audit)
    if not task.checks:
        return apply_failure(
            "apply_requires_checks",
            "applying a non-empty patch requires at least one targeted check",
            audit,
        )

    changed, issues = parse_unified_diff(patch)
    if issues:
        return apply_failure("patch_parse_failed", "; ".join(issues), audit)
    allowed = {_normalize_task_path(name) for name in task.files}
    outside = [name for name in changed if _normalize_task_path(name) not in allowed]
    if outside:
        return apply_failure(
            "patch_outside_scope",
            f"patch touches files outside the declared scope: {', '.join(outside)}",
            audit,
        )

    try:
        applies, detail = check_patch_applies(workspace, patch)
    except ValueError as error:
        return apply_failure("apply_policy", str(error), audit)
    if not applies:
        return apply_failure(
            "stale_workspace",
            f"proposal no longer applies cleanly to the workspace: {detail}",
            audit,
        )

    # Semantic linter pre-gate (R18): never mutate the workspace with a
    # patch that introduces syntax errors.
    lint_report = lint_patch_in_memory(str(workspace), patch)
    if not lint_report.valid:
        return apply_failure(
            "semantic_lint_failed",
            "; ".join(lint_report.prescriptions) or "patch failed static analysis",
            audit,
        )

    baseline = _baseline(workspace, changed)
    try:
        tools = BoundedRepositoryTools(workspace, task, cancel_event=cancel_event)
    except (ToolPolicyError, ValueError) as error:
        return apply_failure("apply_policy", str(error), audit)

    applied, apply_detail = apply_patch(workspace, patch)
    if not applied:
        return apply_failure("apply_failed", f"patch could not be applied: {apply_detail}", audit)
    audit.append({"event": "patch_applied"})

    result: dict[str, Any] = {"status": "accepted", "checks": [], "risks": [], "audit": audit}
    try:
        post_checks, post_checks_passed = run_post_apply_checks(
            task, tools, active_cancel=cancel_event, audit=audit
        )
    except ToolCancelled:
        result["status"] = "failed"
        result["error"] = {"kind": "cancelled", "message": "post-apply checks were cancelled"}
        audit.append({"event": "post_apply_cancelled"})
    except ToolPolicyError as error:
        result["status"] = "rejected"
        result["error"] = {"kind": "post_apply_check_failed", "message": f"post-apply check could not complete: {error}"}
        audit.append({"event": "post_apply_check_error", "detail": str(error)})
    except Exception:  # noqa: BLE001 - apply boundary must roll back, never leave a half-applied patch.
        result["status"] = "failed"
        result["error"] = {"kind": "apply_error", "message": "post-apply checks raised an unexpected error"}
        audit.append({"event": "post_apply_check_error", "detail": "unexpected_error"})
    else:
        result["checks"] = post_checks
        if post_checks_passed:
            result["applied"] = True
            result["workspace_state"] = "original"
            audit.append({"event": "post_apply_checks_passed"})
            return result
        result["status"] = "rejected"
        result["error"] = {"kind": "post_apply_check_failed", "message": "a targeted check failed after applying the patch"}
        audit.append({"event": "post_apply_check_failed"})

    # Reached only on a cancelled / policy-error / unexpected-error / failed
    # check: roll the patch back. A failed rollback is surfaced explicitly so
    # a consumer never mistakes a still-modified workspace for a clean one.
    try:
        rollback_ok, rollback_detail = apply_patch(workspace, patch, reverse=True)
    except Exception as error:  # noqa: BLE001 - expose uncertain workspace state
        rollback_ok = False
        rollback_detail = str(error)
    if rollback_ok and not _restored(workspace, baseline):
        # ponytail: git apply --reverse can exit 0 without reverting every file
        # (e.g. a partially applied hunk). Compare baseline digests instead of
        # trusting the exit code; verify by content if this ever false-positives.
        rollback_ok = False
        rollback_detail = "rollback did not restore the recorded baseline"

    if rollback_ok:
        result["rolled_back"] = True
        result["workspace_state"] = "original"
        audit.append({"event": "patch_rolled_back"})
        return result

    result["rolled_back"] = False
    result["workspace_modified"] = True
    result["workspace_state"] = "unknown"
    add_risk(result, "rollback_failed", f"patch rollback failed: {rollback_detail}")
    audit.append({"event": "rollback_failed", "detail": rollback_detail})
    return result
