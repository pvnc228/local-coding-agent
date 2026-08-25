from __future__ import annotations

from threading import Event

from typing import Any

from ..repository_tools import BoundedRepositoryTools, ToolCancelled, ToolPolicyError
from ..task import TaskEnvelope


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
