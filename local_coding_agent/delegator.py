"""Harness-agnostic delegating agent: decompose, delegate, decompose further.

The delegating agent owns the outer "expensive agent" loop: it breaks a wide
task into bounded children using decomposition templates, delegates each child
through the transport-neutral ``delegate`` seam, and on a decomposable failure
re-splits that child at finer granularity. It does not know about MCP, stdio or
the CLI, and never talks to the model directly.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
import difflib
import json
import os
from pathlib import Path
import re
import shlex
from threading import Event
import time
from typing import Any, Callable, Mapping, Sequence

from .atomizer import TaskBudget, decompose
from .controller._scratch import scratch_workspace, verify_in_scratch
from .prescriptions import prescribe_all, redact_check_output, test_failure_prescription
from .semantic_linter import lint_patch_in_memory
from .service import DelegationRequest
from .task import TaskEnvelope
from .validators import apply_patch, parse_unified_diff, validate_candidate


Delegate = Callable[[str, DelegationRequest], Mapping[str, Any]]


@dataclass(frozen=True)
class DecompositionTemplate:
    """A named strategy that turns one task envelope into bounded children."""

    name: str
    split: Callable[[TaskEnvelope, TaskBudget], tuple[TaskEnvelope, ...]]


def _split_by_files(task: TaskEnvelope, budget: TaskBudget) -> tuple[TaskEnvelope, ...]:
    # ponytail: reuse atomizer.decompose; a context/checks-over-budget task is
    # not file-splittable, so fall back to the unchanged envelope and let the
    # delegate reject it with a machine-readable reason.
    try:
        return decompose(task, budget).children
    except ValueError:
        return (task,)


def _split_per_file(task: TaskEnvelope, budget: TaskBudget) -> tuple[TaskEnvelope, ...]:
    del budget
    return tuple(
        TaskEnvelope(
            id=f"{task.id}#{index + 1}",
            goal=task.goal,
            files=(file,),
            context=task.context,
            constraints=task.constraints,
            checks=task.checks,
            acceptance=task.acceptance,
        )
        for index, file in enumerate(task.files)
    )


BY_FILES = DecompositionTemplate("by_files", _split_by_files)
PER_FILE = DecompositionTemplate("per_file", _split_per_file)
DEFAULT_TEMPLATES = (BY_FILES, PER_FILE)

_DECOMPOSABLE_KINDS = frozenset(
    {
        "preflight_rejected",
        "context_limit",
        "max_turns",
        "retry_budget_exhausted",
        "repair_budget_exhausted",
        "too_many_files",
        "too_many_checks",
    }
)


def is_decomposable_failure(result: Mapping[str, Any]) -> bool:
    """True when a child failure can be retried at finer granularity."""

    error = result.get("error")
    if not isinstance(error, Mapping):
        return False
    kind = error.get("kind")
    if kind in _DECOMPOSABLE_KINDS:
        return True
    if kind == "controller_policy":
        message = str(error.get("message", ""))
        return "max_files" in message or "context" in message
    return False


_FAILED_TEST_RE = re.compile(r"(?:^|\s)(?:FAILED|ERROR)\s+([^\s]+)", re.MULTILINE)
_PYTEST_RE = re.compile(r"^\s*(?:python(?:\.exe)?\s+-m\s+)?pytest(?:\s|$)")
_UNSAFE_SHELL_RE = re.compile(r"[;&|`$<>\r\n\x00]")
_SAFE_TEST_NODE_RE = re.compile(r"^[A-Za-z0-9_.:/\\-]+::[A-Za-z0-9_.:/\\\-\[\],=+]+$")


def _cancelled(event: Event | None) -> bool:
    return event is not None and event.is_set()


def _failed_test_ids(checks: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    """Extract deterministic pytest/unittest node ids from check evidence."""

    found: list[str] = []
    for check in checks:
        if check.get("passed") is True:
            continue
        text = "\n".join(
            str(check.get(name, ""))
            for name in ("stdout", "stderr", "evidence", "detail")
        )
        for match in _FAILED_TEST_RE.finditer(text):
            node = match.group(1).strip().rstrip(":")
            if "::" in node and node not in found:
                found.append(node)
    return tuple(found)


def _narrow_check(command: str, node_id: str) -> str | None:
    """Return an allowlisted command that addresses only one test node."""

    if not isinstance(command, str) or _UNSAFE_SHELL_RE.search(command):
        return None
    if not _PYTEST_RE.search(command) or not _SAFE_TEST_NODE_RE.fullmatch(node_id):
        return None
    target = node_id.split("::", 1)[0].replace("\\", "/").removeprefix("./")
    parts = re.split(r"(\s+)", command)
    target_indexes: list[int] = []
    for index, part in enumerate(parts):
        normalized = part.strip('"\'').replace("\\", "/").removeprefix("./")
        if normalized.startswith("-") or not normalized:
            continue
        if normalized == target or target.startswith(normalized.rstrip("/") + "/"):
            target_indexes.append(index)
    if len(target_indexes) != 1:
        return None
    quoted = f'"{node_id.replace(chr(34), chr(34) * 2)}"' if os.name == "nt" else shlex.quote(node_id)
    parts[target_indexes[0]] = quoted
    return "".join(parts)


def _failed_test_tasks(
    task: TaskEnvelope, checks: Sequence[Mapping[str, Any]]
) -> tuple[tuple[TaskEnvelope, ...], str | None]:
    node_ids = _failed_test_ids(checks)
    if len(node_ids) <= 1:
        return (), "fewer than two independently addressable failed tests"
    commands = {
        str(check.get("command")): check
        for check in checks
        if check.get("passed") is not True and isinstance(check.get("command"), str)
    }
    children: list[TaskEnvelope] = []
    for index, node_id in enumerate(node_ids):
        command: str | None = None
        for candidate in commands:
            if candidate not in task.checks:
                continue
            if _narrow_check(candidate, node_id) is not None:
                command = candidate
                break
        if command is None:
            return (), f"no failed check command contains {node_id}"
        narrowed = _narrow_check(command, node_id)
        if narrowed is None:
            return (), f"check command cannot be narrowed to {node_id}"
        children.append(
            TaskEnvelope(
                id=f"{task.id}#repair-{index + 1}",
                goal=f"{task.goal}\nСделай так, чтобы прошёл {node_id}",
                files=task.files,
                context=task.context,
                constraints=task.constraints,
                checks=(narrowed,),
                acceptance=task.acceptance,
            )
        )
    return tuple(children), None


def _task_step(task: TaskEnvelope, index: int, step: Mapping[str, Any]) -> TaskEnvelope:
    check = step.get("check")
    checks = () if check is None else (check,)
    return TaskEnvelope(
        id=f"{task.id}#step-{index + 1}",
        goal=step["goal"],
        files=tuple(step["files"]),
        context=f"Parent goal: {task.goal}\nPlanned step: {step['goal']}",
        constraints=task.constraints,
        checks=checks,
        acceptance=task.acceptance,
    )


def _path_key(path: str) -> str:
    return path.replace("\\", "/").removeprefix("./")


def _parse_plan(content: Any, task: TaskEnvelope) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(content, str):
        return None, "planner response content must be JSON text"
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        value = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        return None, f"planner returned invalid JSON: {error}"
    if not isinstance(value, Mapping) or not isinstance(value.get("steps"), list):
        return None, "planner schema requires an object with a steps array"
    steps = value["steps"]
    if not steps or len(steps) > 5:
        return None, "planner steps must contain between one and five items"
    allowed_files = set(task.files)
    allowed_checks = set(task.checks)
    normalized: list[dict[str, Any]] = []
    for index, step in enumerate(steps):
        if not isinstance(step, Mapping):
            return None, f"planner step {index + 1} must be an object"
        goal = step.get("goal")
        files = step.get("files")
        check = step.get("check")
        if not isinstance(goal, str) or not goal.strip():
            return None, f"planner step {index + 1} has no goal"
        if isinstance(files, str) or not isinstance(files, list) or not files:
            return None, f"planner step {index + 1} files must be a non-empty array"
        if not all(isinstance(path, str) and path in allowed_files for path in files):
            return None, f"planner step {index + 1} files exceed the task allowlist"
        if check is not None and (not isinstance(check, str) or check not in allowed_checks):
            return None, f"planner step {index + 1} check must be null or an allowlisted task check"
        normalized.append({"goal": goal, "files": list(files), "check": check})
    return {"steps": normalized}, None


def _controller_clone(controller: Any, workspace: Path, cancel_event: Event | None) -> Any:
    """Clone controller limits into scratch while retaining its model wrapper."""

    from .controller import Controller

    kwargs: dict[str, Any] = {}
    for name in (
        "max_turns",
        "max_same_call",
        "max_tool_result_bytes",
        "max_files",
        "max_patch_bytes",
        "max_patch_files",
        "max_context_bytes",
        "max_retries",
        "preflight_budget",
        "system_contract",
        "blocked_tools",
    ):
        if hasattr(controller, name):
            kwargs[name] = getattr(controller, name)
    kwargs.update(max_repair_rounds=0, cancel_event=cancel_event)
    # The wrapper is deliberately passed through unchanged: this is the single
    # global model-call budget for all child and planner calls.
    return Controller(controller.model, str(workspace), **kwargs)


def _apply_child_patch(workspace: Path, patch: str, task: TaskEnvelope) -> tuple[bool, str]:
    if not isinstance(patch, str) or not patch.strip():
        return True, ""
    changed, issues = parse_unified_diff(patch)
    allowed = {_path_key(path) for path in task.files}
    outside = [path for path in changed if _path_key(path) not in allowed]
    if issues:
        return False, "; ".join(issues)
    if outside:
        return False, f"patch changes files outside task allowlist: {', '.join(outside)}"
    return apply_patch(workspace, patch)


def _render_diff_lines(lines: Sequence[str]) -> str:
    rendered: list[str] = []
    for line in lines:
        rendered.append(line)
        if not line.endswith("\n") and not line.startswith(("---", "+++", "@@")):
            rendered.extend(("\n", "\\ No newline at end of file\n"))
    return "".join(rendered)


def _combined_diff(original: Mapping[str, bytes | None], workspace: Path) -> tuple[str, str | None]:
    """Render one git-apply-compatible diff, including creates/deletes."""

    parts: list[str] = []
    for name, old_bytes in original.items():
        target = workspace / Path(name)
        new_bytes = target.read_bytes() if target.is_file() else None
        if old_bytes is None and new_bytes is None:
            continue
        if old_bytes == new_bytes:
            continue
        try:
            old = old_bytes.decode("utf-8") if old_bytes is not None else ""
            new = new_bytes.decode("utf-8") if new_bytes is not None else ""
        except UnicodeDecodeError:
            return "", f"cannot construct a text diff for {name}"
        path = _path_key(name)
        created = old_bytes is None
        deleted = new_bytes is None
        from_name = "/dev/null" if created else f"a/{path}"
        to_name = "/dev/null" if deleted else f"b/{path}"
        diff = list(
            difflib.unified_diff(
                old.splitlines(keepends=True),
                new.splitlines(keepends=True),
                fromfile=from_name,
                tofile=to_name,
            )
        )
        if diff:
            mode = "new file mode 100644\n" if created else "deleted file mode 100644\n" if deleted else ""
            parts.append(f"diff --git a/{path} b/{path}\n" + mode + _render_diff_lines(diff))
    return "".join(parts), None


def _quality_result(
    *,
    task: TaskEnvelope,
    strategy: str,
    children: Sequence[Mapping[str, Any]],
    patch: str,
    checks: Sequence[Mapping[str, Any]],
    passed: bool,
    model_calls: int,
    error: Mapping[str, Any] | None = None,
    verification: Mapping[str, Any] | None = None,
    audit: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "accepted" if passed else "rejected",
        "task_id": task.id,
        "strategy": strategy,
        "patch": patch,
        "checks": [dict(check) for check in checks],
        "children": [dict(child) for child in children],
        "model_calls": model_calls,
        "verification": dict(verification or {"mode": "none", "rounds": 0}),
        "audit": audit if audit is not None else [],
    }
    if error is not None:
        result["error"] = dict(error)
    return result


def _execute_children(
    controller: Any,
    task: TaskEnvelope,
    children: Sequence[TaskEnvelope],
    strategy: str,
    *,
    cancel_event: Event | None,
) -> dict[str, Any]:
    audit: list[dict[str, Any]] = []
    child_reports: list[dict[str, Any]] = []
    model_calls = 0
    workspace_root = Path(controller.workspace_root)
    try:
        with scratch_workspace(workspace_root, cancel_event=cancel_event, audit=audit) as scratch:
            original = {
                name: (
                    (scratch / Path(name)).read_bytes()
                    if (scratch / Path(name)).is_file()
                    else None
                )
                for name in task.files
            }
            for child in children:
                if _cancelled(cancel_event):
                    return _quality_result(
                        task=task, strategy=strategy, children=child_reports, patch="",
                        checks=(), passed=False, model_calls=model_calls,
                        error={"kind": "cancelled", "message": "task was cancelled"},
                        audit=audit,
                    )
                child_controller = _controller_clone(controller, scratch, cancel_event)
                result = dict(child_controller.run(child, cancel_event=cancel_event, apply=False))
                model_calls += int(result.get("model_calls") or 0) or sum(
                    event.get("event") == "model_request"
                    for event in result.get("audit", ())
                    if isinstance(event, Mapping)
                )
                if result.get("status") != "accepted":
                    child_error = result.get("error")
                    child_message = (
                        str(child_error.get("message", ""))
                        if isinstance(child_error, Mapping)
                        else "child did not produce an accepted candidate"
                    )
                    return _quality_result(
                        task=task, strategy=strategy, children=child_reports, patch="",
                        checks=(), passed=False, model_calls=model_calls,
                        error={
                            "kind": "child_failed",
                            "message": child_message,
                            "task_id": child.id,
                        },
                        audit=audit,
                    )
                patch = result.get("patch")
                if isinstance(patch, str) and patch.strip():
                    changed, issues = parse_unified_diff(patch)
                    if issues or any(
                        _path_key(path) not in {_path_key(name) for name in child.files}
                        for path in changed
                    ):
                        return _quality_result(
                            task=task, strategy=strategy, children=child_reports, patch="",
                            checks=(), passed=False, model_calls=model_calls,
                            error={"kind": "child_patch_invalid", "message": "; ".join(issues) or "child patch exceeds allowlist"},
                            audit=audit,
                        )
                    lint_report = lint_patch_in_memory(str(scratch), patch)
                    if not lint_report.valid:
                        detail = "; ".join(
                            f"{item.file}:{item.line}: {item.message}"
                            for item in lint_report.diagnostics
                        )
                        return _quality_result(
                            task=task, strategy=strategy, children=child_reports, patch="",
                            checks=(), passed=False, model_calls=model_calls,
                            error={"kind": "child_lint_failed", "message": detail or "child patch failed semantic lint"},
                            audit=audit,
                        )
                    # This uses a bounded, isolated verification seam for each
                    # child. A failed intermediate check is recorded but does
                    # not discard its patch: later dependent steps need it.
                    verdict = verify_in_scratch(
                        scratch, patch, child, cancel_event=cancel_event, audit=audit
                    )
                    result["scratch_verification"] = {
                        "state": verdict.state,
                        "passed": verdict.passed,
                        "checks": verdict.checks,
                    }
                    applied, detail = _apply_child_patch(scratch, patch, child)
                    if not applied:
                        return _quality_result(
                            task=task, strategy=strategy, children=child_reports, patch="",
                            checks=(), passed=False, model_calls=model_calls,
                            error={"kind": "child_patch_apply_failed", "message": detail},
                            audit=audit,
                        )
                child_reports.append({"task_id": child.id, "result": result})
            patch, diff_error = _combined_diff(original, scratch)
            if diff_error:
                return _quality_result(
                    task=task, strategy=strategy, children=child_reports, patch="",
                    checks=(), passed=False, model_calls=model_calls,
                    error={"kind": "combined_diff_failed", "message": diff_error},
                    audit=audit,
                )
            validation = validate_candidate(
                {'status': 'candidate', 'summary': 'combined steps', 'patch': patch, 'checks': [], 'risks': []},
                task, workspace_root=workspace_root,
                max_patch_bytes=controller.max_patch_bytes, max_patch_files=controller.max_patch_files)
            if not validation.valid:
                return _quality_result(
                    task=task, strategy=strategy, children=child_reports, patch=patch,
                    checks=(), passed=False, model_calls=model_calls, audit=audit,
                    error={'kind': 'combined_validation_failed', 'message': '; '.join(validation.issues)})
            if not task.checks:
                return _quality_result(
                    task=task, strategy=strategy, children=child_reports, patch=patch,
                    checks=(), passed=True, model_calls=model_calls,
                    audit=audit,
                )
            final = verify_in_scratch(
                workspace_root, patch, task, cancel_event=cancel_event, audit=audit
            )
            return _quality_result(
                task=task,
                strategy=strategy,
                children=child_reports,
                patch=patch,
                checks=final.checks,
                passed=final.passed,
                model_calls=model_calls,
                error=None if final.passed else {
                    "kind": "quality_checks_failed" if final.state == "failed" else "scratch_unavailable",
                    "message": final.detail or "final checks failed",
                    "state": final.state,
                },
                verification={"mode": "scratch" if final.state in ("passed", "failed") else "none", "rounds": 1},
                audit=audit,
            )
    except Exception as error:
        return _quality_result(
            task=task, strategy=strategy, children=child_reports, patch="",
            checks=(), passed=False, model_calls=model_calls,
            error={"kind": "scratch_unavailable", "message": str(error)[:500]},
            audit=audit,
        )


def _planner_call(
    controller: Any,
    task: TaskEnvelope,
    *,
    failure: str = "",
    cancel_event: Event | None = None,
) -> tuple[dict[str, Any] | None, str | None, int]:
    if _cancelled(cancel_event):
        return None, "planner cancelled", 0
    prompt = {
        "task": task.as_dict(),
        "schema": {"steps": [{"goal": "string", "files": ["task-file"], "check": "string or null"}]},
        "limits": {"max_steps": 5, "files": "must be a subset of task.files", "check": "must be null or one of task.checks"},
    }
    if failure:
        prompt["previous_failure"] = failure[:2000]
        prompt["prescription"] = (
            "The previous plan was rejected. Return one strict JSON object with "
            "steps only; keep every files entry inside task.files and every check "
            "null or exactly one task check."
        )
    messages = [
        {
            "role": "system",
            "content": "Plan the coding task. Return only strict JSON matching the requested schema; do not propose edits.",
        },
        {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
    ]
    max_context_bytes = getattr(controller, "max_context_bytes", 128_000)
    encoded_size = len(json.dumps(messages, ensure_ascii=False).encode("utf-8"))
    if encoded_size > max_context_bytes:
        return None, f"planner context exceeds max_context_bytes={max_context_bytes}", 0
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(controller.model.chat, messages, tools=[])
        deadline = time.monotonic() + 60.0
        while True:
            if _cancelled(cancel_event):
                future.cancel()
                executor.shutdown(wait=False, cancel_futures=True)
                return None, "planner cancelled", 1
            if time.monotonic() >= deadline:
                future.cancel()
                executor.shutdown(wait=False, cancel_futures=True)
                return None, "planner call exceeded bounded timeout", 1
            try:
                response = future.result(timeout=0.05)
                break
            except FuturesTimeoutError:
                continue
        content = response.get("message", {}).get("content") if isinstance(response, Mapping) else None
        plan, error = _parse_plan(content, task)
        executor.shutdown(wait=False, cancel_futures=True)
        return plan, error, 1
    except Exception as error:
        executor.shutdown(wait=False, cancel_futures=True)
        safe_error = redact_check_output(str(error))[:500]
        return None, f"planner call failed: {prescribe_all(['planner response failed'])} {safe_error}", 1


def _run_tests_strategy(
    controller: Any,
    task: TaskEnvelope,
    *,
    cancel_event: Event | None,
) -> dict[str, Any]:
    baseline = dict(controller.run(task, cancel_event=cancel_event, apply=False))
    if baseline.get("status") == "accepted":
        baseline["strategy"] = "tests"
        return baseline
    baseline_error = baseline.get("error")
    if not isinstance(baseline_error, Mapping) or baseline_error.get("kind") != "repair_budget_exhausted":
        baseline["strategy"] = "tests"
        baseline["quality"] = {
            "state": "not_splittable",
            "detail": "test repair splitting requires an exhausted repair budget",
        }
        return baseline
    children, reason = _failed_test_tasks(task, baseline.get("checks") or ())
    if not children:
        baseline["strategy"] = "tests"
        baseline["quality"] = {"state": "not_splittable", "detail": reason or "no failed tests found"}
        error = baseline.get("error")
        if not isinstance(error, Mapping):
            baseline["error"] = {"kind": "not_splittable", "message": reason or "no failed tests found"}
        return baseline
    return _execute_children(controller, task, children, "tests", cancel_event=cancel_event)


def _run_quality_strategy(
    controller: Any,
    task: TaskEnvelope,
    strategy: str,
    *,
    cancel_event: Event | None = None,
    completion_event: Event | None = None,
) -> dict[str, Any]:
    active_cancel = cancel_event or getattr(controller, "cancel_event", None)
    try:
        if strategy == "single":
            return controller.run(task, cancel_event=active_cancel, completion_event=completion_event, apply=False)
        if strategy == "tests":
            return _run_tests_strategy(controller, task, cancel_event=active_cancel)
        if strategy in {"per_file", "by_files"}:
            template = PER_FILE if strategy == "per_file" else BY_FILES
            children = template.split(task, getattr(controller, "preflight_budget", TaskBudget()))
            return _execute_children(controller, task, children, strategy, cancel_event=active_cancel)
        if strategy != "plan":
            return {"status": "rejected", "error": {"kind": "invalid_strategy", "message": strategy}}

        plan: dict[str, Any] | None = None
        planner_calls = 0
        planner_error = ""
        for attempt in range(2):
            plan, planner_error, calls = _planner_call(
                controller,
                task,
                failure=planner_error if attempt else "",
                cancel_event=active_cancel,
            )
            planner_calls += calls
            if plan is not None:
                break
            if _cancelled(active_cancel):
                return {
                    "status": "failed",
                    "error": {"kind": "cancelled", "message": "task was cancelled"},
                    "model_calls": planner_calls,
                }
        if plan is None:
            fallback = _run_tests_strategy(controller, task, cancel_event=active_cancel)
            fallback["strategy"] = "plan"
            fallback["quality"] = {"state": "planner_invalid_fallback", "detail": planner_error}
            fallback["model_calls"] = int(fallback.get("model_calls") or 0) + planner_calls
            return fallback

        steps = [_task_step(task, index, step) for index, step in enumerate(plan["steps"])]
        result = _execute_children(controller, task, steps, "plan", cancel_event=active_cancel)
        result["model_calls"] = int(result.get("model_calls") or 0) + planner_calls
        if result.get("status") == "accepted":
            return result
        if not isinstance(result.get("error"), Mapping) or result["error"].get("kind") != "quality_checks_failed":
            return result
        # One bounded replan is allowed after the final full-check gate fails.
        failure = json.dumps(
            {
                "error": result.get("error"),
                "observed_failure": test_failure_prescription(
                    result.get("checks") or (), task.files
                ),
            },
            ensure_ascii=False,
        )
        replanned, replan_error, calls = _planner_call(
            controller, task, failure=failure, cancel_event=active_cancel
        )
        if replanned is None:
            result["model_calls"] = int(result.get("model_calls") or 0) + calls
            result["quality"] = {"state": "replan_invalid", "detail": replan_error}
            return result
        retry_steps = [_task_step(task, index, step) for index, step in enumerate(replanned["steps"])]
        retry = _execute_children(controller, task, retry_steps, "plan", cancel_event=active_cancel)
        retry["model_calls"] = int(retry.get("model_calls") or 0) + planner_calls + calls
        retry["quality"] = {"state": "replanned", "previous_failure": failure[:500]}
        return retry
    finally:
        if completion_event is not None and strategy != "single":
            completion_event.set()


def run_quality_strategy(
    controller: Any,
    task: TaskEnvelope,
    strategy: str = "single",
    *,
    cancel_event: Event | None = None,
    completion_event: Event | None = None,
) -> dict[str, Any]:
    """Run one quality strategy while keeping all work in accumulated scratch.

    Child controllers inherit limits and the exact same ``controller.model``
    object, so an outer ``BudgetModel`` observes planner and child calls as one
    budget. Only the final combined patch is returned to the caller.
    """

    if completion_event is None:
        return _run_quality_strategy(controller, task, strategy, cancel_event=cancel_event)
    # Reuse the service's activity tracking. A cancelled planner/controller may
    # return while its HTTP call still owns model resources; completion waits
    # for those calls, including child controllers, rather than just this loop.
    from .quality import _CallBudget
    activity = _CallBudget(100)
    original_model = controller.model
    controller.model = activity.wrap(original_model)
    try:
        return _run_quality_strategy(controller, task, strategy, cancel_event=cancel_event)
    finally:
        controller.model = original_model
        activity.complete_when_idle(completion_event)


class DelegatingAgent:
    """Decompose a task into bounded children and delegate each one.

    ``delegate`` is the harness-agnostic seam (e.g. ``DelegationService.delegate``)
    with signature ``(caller_id, request) -> result``. ``workspace_ref`` and
    ``model_profile`` are deployment configuration, not part of the task.
    """

    def __init__(
        self,
        delegate: Delegate,
        *,
        workspace_ref: str,
        model_profile: str,
        budget: TaskBudget | None = None,
        templates: Sequence[DecompositionTemplate] = DEFAULT_TEMPLATES,
        max_depth: int = 3,
        max_parallel_children: int = 1,
    ) -> None:
        if max_depth <= 0:
            raise ValueError("max_depth must be positive")
        if not templates:
            raise ValueError("templates must not be empty")
        if max_parallel_children <= 0:
            raise ValueError("max_parallel_children must be positive")
        self.delegate = delegate
        self.workspace_ref = workspace_ref
        self.model_profile = model_profile
        self.budget = budget or TaskBudget()
        self.templates = tuple(templates)
        self.max_depth = max_depth
        self.max_parallel_children = max_parallel_children

    @staticmethod
    def run_quality_strategy(
        controller: Any,
        task: TaskEnvelope,
        strategy: str = "single",
        *,
        cancel_event: Event | None = None,
        completion_event: Event | None = None,
    ) -> dict[str, Any]:
        """Shared quality execution seam used by the service and CLI layers."""

        return run_quality_strategy(
            controller,
            task,
            strategy,
            cancel_event=cancel_event,
            completion_event=completion_event,
        )

    def run(self, caller_id: str, task: TaskEnvelope, *, independent_files: bool = False) -> dict[str, Any]:
        """Dispatch legacy proposal children independently.

        This transport-neutral method preserves its historical envelope
        contract. Splitting requires the host to assert ``independent_files``.
        Dependent edits must use :func:`run_quality_strategy`, whose
        ``per_file``/``by_files`` modes provide accumulated scratch state.
        """
        if not isinstance(caller_id, str) or not caller_id.strip():
            raise ValueError("caller_id must be a non-empty string")
        if type(independent_files) is not bool:
            raise ValueError('independent_files must be a boolean')
        splits = [0]
        leaves = self._dispatch(caller_id, task, 0, splits, independent_files=independent_files)
        accepted = sum(1 for leaf in leaves if leaf["result"].get("status") == "accepted")
        if accepted == len(leaves):
            status = "accepted"
        elif accepted:
            status = "partial"
        else:
            status = "failed"
        result = {
            "status": status,
            "task_id": task.id,
            "splits": splits[0],
            "children": leaves,
        }
        if len(leaves) == 1 and leaves[0]['result'].get('error', {}).get('kind') == 'independent_files_required':
            result['error'] = leaves[0]['result']['error']
        return result

    def _dispatch(
        self,
        caller_id: str,
        task: TaskEnvelope,
        depth: int,
        splits: list[int],
        *, independent_files: bool = False,
    ) -> list[dict[str, Any]]:
        template = self.templates[min(depth, len(self.templates) - 1)]
        children = template.split(task, self.budget)
        if len(children) > 1 and not independent_files:
            return [{'task_id': task.id, 'depth': depth, 'result': {
                'status': 'failed', 'error': {'kind': 'independent_files_required',
                'message': 'Legacy children do not share patches. Assert independent_files=True or use a scratch execution strategy.'}}}]
        leaves: list[dict[str, Any]] = []

        def run_child(child: TaskEnvelope, index: int) -> tuple[dict[str, Any], bool]:
            request = DelegationRequest(
                request_id=f"{task.id}@{depth}.{index}",
                workspace_ref=self.workspace_ref,
                model_profile=self.model_profile,
                task=child,
            )
            result = self.delegate(caller_id, request)
            return dict(result), is_decomposable_failure(result) and self._can_split_further(child, depth)

        if self.max_parallel_children > 1 and len(children) > 1:
            with ThreadPoolExecutor(max_workers=self.max_parallel_children) as pool:
                outcomes = list(pool.map(lambda item: run_child(item[1], item[0]), enumerate(children)))
        else:
            outcomes = [run_child(child, index) for index, child in enumerate(children)]

        for child, (result, decomposable) in zip(children, outcomes):
            if decomposable:
                splits[0] += 1
                leaves.extend(self._dispatch(caller_id, child, depth + 1, splits, independent_files=independent_files))
            else:
                leaves.append({"task_id": child.id, "depth": depth, "result": result})
        return leaves

    def _can_split_further(self, task: TaskEnvelope, depth: int) -> bool:
        if depth + 1 >= self.max_depth:
            return False
        next_template = self.templates[min(depth + 1, len(self.templates) - 1)]
        return len(next_template.split(task, self.budget)) > 1
