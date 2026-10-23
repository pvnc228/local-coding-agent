"""Patch judgement: validate, git-apply, and run external oracles on a proposal."""

from __future__ import annotations

import json
import inspect
import os
import shutil
import subprocess
import sys
import textwrap
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Mapping

from ..repository_tools import BoundedRepositoryTools, ToolPolicyError
from ..validators import validate_candidate

from ._cases import BenchmarkCase


def _judge_patch(
    result: Mapping[str, Any],
    case: BenchmarkCase,
    workspace: Path,
    *,
    fallback_patch: str,
    fallback_edits: list[dict[str, Any]] | None = None,
) -> tuple[bool, bool, str, str]:
    patch_source = "accepted_result" if result.get("status") == "accepted" else "tool_proposal"
    patch = result.get("patch") if patch_source == "accepted_result" else fallback_patch
    if (not isinstance(patch, str) or not patch.strip()) and fallback_patch:
        patch_source = "tool_proposal"
        patch = fallback_patch
    edits = None
    if not isinstance(patch, str) or not patch.strip():
        if fallback_edits:
            patch_source = "tool_proposal"
            edits = fallback_edits
    has_patch = isinstance(patch, str) and bool(patch.strip())
    if not has_patch and not edits:
        return False, False, "candidate did not contain a patch", "none"
    validation = _validate_patch_for_case(
        patch,
        case,
        edits=edits,
        workspace=workspace,
        include_checks=False,
    )
    if not validation.valid:
        return False, False, "; ".join(validation.issues), patch_source
    resolved = validation.resolved_patch or patch
    if shutil.which("git") is None:
        return False, False, "git executable is unavailable for isolated patch application", patch_source
    apply_check = _git_apply(workspace, resolved, check=True)
    if apply_check.returncode != 0:
        return False, False, _process_error(apply_check), patch_source
    applied = _git_apply(workspace, resolved, check=False)
    if applied.returncode != 0:
        return False, False, _process_error(applied), patch_source
    checks_ok, checks_error = _run_task_checks(case, workspace)
    if not checks_ok:
        return True, False, checks_error, patch_source
    try:
        if case.oracle is not None:
            correct, oracle_error = _run_oracle_in_restricted_process(case.oracle, workspace)
        else:
            correct, oracle_error = _exact_file_oracle(case, workspace)
    except Exception as error:  # external oracle must turn malformed proposals into a score
        correct, oracle_error = False, f"external oracle error: {error}"
    if not correct:
        return True, False, oracle_error, patch_source
    if set(validation.changed_files) != set(case.expected_files):
        return True, False, "external oracle changed-file set mismatch", patch_source
    return True, True, "", patch_source


def _validate_patch_for_case(
    patch: str,
    case: BenchmarkCase,
    *,
    edits=None,
    workspace=None,
    include_checks: bool = True,
):
    task = case.task if include_checks else replace(case.task, checks=())
    candidate = {
        "status": "candidate",
        "summary": "benchmark proposal",
        "patch": patch,
        "checks": [],
        "risks": [],
    }
    if edits is not None:
        candidate["edits"] = edits
    return validate_candidate(candidate, task, workspace_root=workspace)


def _run_task_checks(case: BenchmarkCase, workspace: Path) -> tuple[bool, str]:
    """Run the case's allowlisted checks through the production repository seam."""

    if not case.task.checks:
        return True, ""
    tools = BoundedRepositoryTools(
        workspace,
        case.task,
        max_tool_result_bytes=32_000,
        max_files=max(5, len(case.task.files)),
    )
    for command in case.task.checks:
        try:
            result = tools.execute("run_tests", {"command": command})
        except (ToolPolicyError, OSError, ValueError) as error:
            return False, f"test_check_failed: {command}: {error}"
        if result.get("passed") is not True:
            detail = result.get("stderr") or result.get("stdout") or "check returned a failure"
            return False, f"test_check_failed: {command}: {str(detail)[:2000]}"
    return True, ""


def _exact_file_oracle(case: BenchmarkCase, workspace: Path) -> tuple[bool, str]:
    for raw_path, expected in case.expected_files.items():
        actual = (workspace / raw_path).read_text(encoding="utf-8")
        if actual != expected:
            return False, f"external oracle mismatch: {raw_path}"
    return True, ""


def _git_apply(workspace: Path, patch: str, *, check: bool) -> subprocess.CompletedProcess[bytes]:
    command = ["git", "apply", "--whitespace=nowarn"]
    if check:
        command.append("--check")
    command.append("-")
    return subprocess.run(
        command,
        cwd=workspace,
        input=patch.encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=10,
    )


def _process_error(process: subprocess.CompletedProcess[bytes]) -> str:
    detail = process.stderr.decode("utf-8", errors="replace").strip()
    return detail or f"git apply exited with code {process.returncode}"


def _run_oracle_in_restricted_process(
    oracle: Callable[[Path], tuple[bool, str]], workspace: Path
) -> tuple[bool, str]:
    """Run model-controlled fixture code outside the controller process."""

    try:
        oracle_source = textwrap.dedent(inspect.getsource(oracle))
    except (OSError, TypeError) as error:
        return False, f"external oracle source is unavailable: {error}"
    payload = {
        "workspace": str(workspace),
        "oracle_name": getattr(oracle, "__name__", ""),
        "oracle_source": oracle_source,
    }
    # the worker module lives one directory up from this package
    worker = Path(__file__).resolve().parents[1] / "benchmark_oracle_worker.py"
    try:
        completed = subprocess.run(
            [sys.executable, "-I", "-u", str(worker)],
            cwd=workspace,
            env=_benchmark_worker_environment(workspace),
            input=(json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
            **_benchmark_process_options(),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, f"external oracle process failed: {error}"
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        return False, detail[:2000] or f"external oracle exited with code {completed.returncode}"
    try:
        result = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        return False, f"external oracle returned invalid JSON: {error}"
    if not isinstance(result, Mapping) or result.get("ok") is not True:
        detail = result.get("error") if isinstance(result, Mapping) else None
        return False, f"external oracle error: {detail or 'unknown worker error'}"
    correct = result.get("correct")
    detail = result.get("detail", "")
    if not isinstance(correct, bool) or not isinstance(detail, str):
        return False, "external oracle returned an invalid result shape"
    return correct, detail


def _benchmark_worker_environment(workspace: Path) -> dict[str, str]:
    python_dir = str(Path(sys.executable).resolve().parent)
    environment = {
        "PATH": python_dir,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "TEMP": str(workspace),
        "TMP": str(workspace),
    }
    for key in ("SystemRoot", "WINDIR"):
        value = os.environ.get(key)
        if value:
            environment[key] = value
    return environment


def _benchmark_process_options() -> dict[str, Any]:
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}
