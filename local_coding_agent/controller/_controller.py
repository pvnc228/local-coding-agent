from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from threading import Event, Thread

from typing import Any

from .. import controller as _controller_pkg
from ..atomizer import TaskBudget, preflight
from ..context_manager import (
    ContextAssembler,
    HarnessState,
    compact_tool_exchanges,
    purge_diff_residues,
)
from ..prescriptions import json_syntax_prescription, prescribe_all, tool_policy_prescription
from ..repository_tools import BoundedRepositoryTools, ToolCancelled, ToolPolicyError
from ..ollama_adapter import classify_backend_error
from ..semantic_linter import lint_patch_in_memory
from ..task import TaskEnvelope
from ..validators import validate_candidate
from ._constants import SYSTEM_CONTRACT, TOOL_DEFINITIONS, ModelClient
from ._post_apply import run_post_apply_checks


# apply_patch is resolved through the package namespace at call time so that
# tests patching `local_coding_agent.controller.apply_patch` keep working
# (the name is re-exported by the package __init__).
def _apply_patch(*args, **kwargs):
    return _controller_pkg.apply_patch(*args, **kwargs)


class Controller:
    def __init__(
        self,
        model: ModelClient,
        workspace_root: str,
        *,
        max_turns: int = 4,
        max_same_call: int = 1,
        max_tool_result_bytes: int = 64_000,
        max_files: int = 5,
        max_patch_bytes: int = 128_000,
        max_patch_files: int = 2,
        max_context_bytes: int = 128_000,

        max_retries: int = 1,

        preflight_budget: TaskBudget = TaskBudget(),
        cancel_event: Event | None = None,
        system_contract: str | None = None,
        blocked_tools: set[str] | None = None,
    ) -> None:
        if max_turns <= 0 or max_same_call <= 0 or max_retries < 0:
            raise ValueError("controller limits are invalid")
        if max_retries > 10:
            raise ValueError("max_retries exceeds hard cap of 10")
        if not isinstance(preflight_budget, TaskBudget):
            raise ValueError("preflight_budget must be a TaskBudget")
        self.model = model
        self.workspace_root = workspace_root
        self.max_turns = max_turns
        self.max_same_call = max_same_call
        self.max_tool_result_bytes = max_tool_result_bytes
        self.max_files = max_files
        self.max_patch_bytes = max_patch_bytes
        self.max_patch_files = max_patch_files
        self.max_context_bytes = max_context_bytes
        self.max_retries = max_retries
        self.preflight_budget = preflight_budget
        self.cancel_event = cancel_event
        self.system_contract = system_contract or SYSTEM_CONTRACT
        self.blocked_tools = set(blocked_tools or ())

    def run(
        self,
        task: TaskEnvelope,
        *,
        cancel_event: Event | None = None,
        completion_event: Event | None = None,
        apply: bool = False,
    ) -> dict[str, Any]:
        audit: list[dict[str, Any]] = [{"event": "task_received", "task_id": task.id}]
        try:
            report = preflight(task, self.preflight_budget)
            if not report.accepted:
                if completion_event is not None:
                    completion_event.set()
                return self._failure(
                    "failed",
                    "preflight_rejected",
                    report.reason or "preflight_rejected",
                    audit,
                )
            messages = self._initial_messages(task)
        except ValueError as error:
            if completion_event is not None:
                completion_event.set()
            return self._failure("needs_context", "context_limit", str(error), audit)

        executor: ThreadPoolExecutor | None = None
        try:
            active_cancel = cancel_event or self.cancel_event
            tools = BoundedRepositoryTools(
                self.workspace_root,
                task,
                max_tool_result_bytes=self.max_tool_result_bytes,
                max_files=self.max_files,
                max_patch_bytes=self.max_patch_bytes,
                max_patch_files=self.max_patch_files,
                cancel_event=active_cancel,
                blocked_tools=self.blocked_tools,
            )
            seen_calls: dict[str, int] = {}
            observed_checks: dict[str, dict[str, Any]] = {}
            attempts: list[dict[str, Any]] = []
            viewed_files: set[str] = set()
            last_patch: list[str] = []
            retries = 0
            executor = ThreadPoolExecutor(max_workers=1)
            return self._run_turns(
                task,
                messages,
                tools,
                active_cancel,
                seen_calls,
                observed_checks,
                attempts,
                viewed_files,
                last_patch,
                retries,
                executor,
                audit,
                apply=apply,
            )
        finally:
            if executor is None:
                if completion_event is not None:
                    completion_event.set()
            else:
                executor.shutdown(wait=False, cancel_futures=True)
                if completion_event is not None:
                    Thread(
                        target=self._wait_for_executor,
                        args=(executor, completion_event),
                        daemon=True,
                    ).start()

    @staticmethod
    def _wait_for_executor(executor: ThreadPoolExecutor, completion_event: Event) -> None:
        try:
            executor.shutdown(wait=True)
        finally:
            completion_event.set()

    def _run_turns(
        self,
        task: TaskEnvelope,
        messages: list[dict[str, Any]],
        tools: BoundedRepositoryTools,
        active_cancel: Event | None,
        seen_calls: dict[str, int],
        observed_checks: dict[str, dict[str, Any]],
        attempts: list[dict[str, Any]],
        viewed_files: set[str],
        last_patch: list[str],
        retries: int,
        executor: ThreadPoolExecutor,
        audit: list[dict[str, Any]],
        *,
        apply: bool = False,
    ) -> dict[str, Any]:
        last_invalid_candidate: dict[str, Any] | None = None
        for turn in range(1, self.max_turns + 1):
            if active_cancel is not None and active_cancel.is_set():
                return self._failure("failed", "cancelled", "task was cancelled", audit)
            if self._messages_size(messages) > self.max_context_bytes:
                messages = self._compact_messages(messages, audit=audit)
            if self._messages_size(messages) > self.max_context_bytes:
                return self._failure(
                    "failed",
                    "context_limit",
                    f"cumulative context exceeds max_context_bytes={self.max_context_bytes}",
                    audit,
                )
            audit.append({"event": "model_request", "turn": turn, "message_count": len(messages)})
            try:
                future = executor.submit(
                    self.model.chat, messages, tools=self._tools_for_task(task)
                )
                while True:
                    try:
                        response = future.result(timeout=0.05)
                        break
                    except (TimeoutError, FuturesTimeoutError):
                        if active_cancel is not None and active_cancel.is_set():
                            # ponytail: the abandoned chat thread keeps running
                            # until its own ~30s HTTP timeout.
                            return self._failure("failed", "cancelled", "task was cancelled", audit)

            except Exception as error:  # model boundary: normalize executor failures
                if last_invalid_candidate is not None:
                    return last_invalid_candidate
                backend_kind = classify_backend_error(error)
                if backend_kind == "offline":
                    return self._failure("failed", "backend_offline", str(error), audit)
                if backend_kind == "server_error":
                    return self._failure("failed", "backend_error", str(error), audit)
                return self._failure("failed", "model_error", str(error), audit)
            audit.append({
                "event": "model_response",
                "turn": turn,
                "eval_tokens": int(response.get("eval_count") or 0) if isinstance(response, dict) else 0,
                "eval_duration_ns": int(response.get("eval_duration") or 0) if isinstance(response, dict) else 0,
            })
            message = response.get("message") if isinstance(response, dict) else None
            if not isinstance(message, dict):
                if retries < self.max_retries:
                    retries += 1
                    attempts.append({"attempt": retries, "reason": "invalid_response"})
                    messages.append({"role": "user", "content": "Верни только объект JSON результата задачи."})
                    audit.append({"event": "retry", "reason": "invalid_response"})
                    continue
                attempts.append({"attempt": retries + 1, "reason": "invalid_response"})
                return self._escalation(
                    task,
                    reason="invalid_response",
                    attempts=attempts,
                    viewed_files=viewed_files,
                    last_patch=last_patch,
                    observed_checks=observed_checks,
                    audit=audit,
                )

            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                compatible_call = self._decode_content_tool_call(message.get("content"))
                if compatible_call is not None:
                    message = dict(message)
                    message["tool_calls"] = [compatible_call]
                    message["content"] = ""
                    tool_calls = message["tool_calls"]
                    audit.append({"event": "content_tool_call_compatibility", "turn": turn})
            if tool_calls:
                messages.append(message)
                for call in tool_calls:
                    name = None
                    call_id = None
                    try:
                        name, arguments, call_id = self._decode_tool_call(call)
                        signature_arguments = arguments
                        if name == "list_files" and "path" not in signature_arguments:
                            signature_arguments = {**arguments, "path": "."}
                        signature = json.dumps(
                            {"name": name, "arguments": signature_arguments},
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                        seen_calls[signature] = seen_calls.get(signature, 0) + 1
                        if seen_calls[signature] > self.max_same_call:
                            if last_patch:
                                result = {
                                    "ok": True,
                                    "patch": last_patch[-1],
                                    "message": f"{name} already executed with valid patch. Conclude now.",
                                }
                                audit.append({"event": "tool_call_skipped_duplicate", "name": name, "turn": turn})
                            else:
                                return self._failure(
                                    "failed",
                                    "duplicate_tool_call",
                                    f"repeated tool call: {name}",
                                    audit,
                                )
                        else:
                            audit.append({"event": "tool_call", "name": name, "arguments": arguments, "turn": turn})
                            result = tools.execute(name, arguments)
                        if name == "read_file":
                            path = arguments.get("path")
                            if isinstance(path, str):
                                viewed_files.add(path)
                        elif name == "search_text":
                            for path in (arguments.get("paths") or list(task.files)):
                                if isinstance(path, str):
                                    viewed_files.add(path)
                        elif name == "list_files":
                            for path in result.get("files", []):
                                if isinstance(path, str):
                                    viewed_files.add(path)
                        elif name == "propose_patch":
                            if result.get("ok", True) is not False:
                                patch = result.get("patch")
                                if isinstance(patch, str):
                                    last_patch[:] = [patch]
                        if name == "run_tests" and "passed" in result:
                            observed_checks[arguments["command"]] = {
                                "command": arguments["command"],
                                "passed": result["passed"],
                                "evidence": result["evidence"],
                                "stdout": result.get("stdout", ""),
                                "stderr": result.get("stderr", ""),
                                "exit_code": result.get("exit_code"),
                            }
                        tool_message: dict[str, Any] = {
                            "role": "tool",
                            "tool_name": name,
                            "content": json.dumps(result, ensure_ascii=False),
                        }
                        if call_id is not None:
                            tool_message["tool_call_id"] = call_id
                        messages.append(tool_message)
                        audit.append({"event": "tool_result", "name": name, "turn": turn})
                    except ToolPolicyError as error:
                        audit.append({"event": "tool_policy_error", "name": name, "error": str(error), "turn": turn})
                        tool_payload = tool_policy_prescription(name, str(error))
                        tool_message = {
                            "role": "tool",
                            "tool_name": name,
                            "content": json.dumps(tool_payload, ensure_ascii=False),
                        }
                        if call_id is not None:
                            tool_message["tool_call_id"] = call_id
                        messages.append(tool_message)
                        audit.append({"event": "tool_result", "name": name, "turn": turn})
                    except (ValueError, TypeError, json.JSONDecodeError) as error:
                        # Malformed tool-call arguments (e.g. truncated JSON) are
                        # fed back as a structured prescription so the model can
                        # re-issue a valid tool call within the turn budget,
                        # mirroring the ToolPolicyError branch above — never a
                        # raw parser error surfaced to the caller.
                        audit.append({
                            "event": "tool_json_error",
                            "name": name,
                            "error": str(error),
                            "turn": turn,
                        })
                        tool_payload = {
                            "ok": False,
                            "status": "error",
                            "error_code": "invalid_json",
                            "error": str(error),
                            "hint": (
                                "Твой tool-call не удалось разобрать как валидный JSON. "
                                'Верни аргументы как СТРОГО один JSON-объект, например '
                                '{"file": "src/main.py", "search": "...", "replace": "..."}. '
                                "Без текста до и после, без markdown-разметки."
                            ),
                        }
                        tool_message: dict[str, Any] = {
                            "role": "tool",
                            "tool_name": name or "unknown",
                            "content": json.dumps(tool_payload, ensure_ascii=False),
                        }
                        if call_id is not None:
                            tool_message["tool_call_id"] = call_id
                        messages.append(tool_message)
                        audit.append({"event": "tool_result", "name": name or "unknown", "turn": turn})
                    except ToolCancelled:
                        return self._failure("failed", "cancelled", "task was cancelled", audit)
                continue

            content = message.get("content")
            try:
                result = self._parse_final_result(content)
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                if retries < self.max_retries:
                    retries += 1
                    attempts.append({"attempt": retries, "reason": "invalid_json"})
                    messages.append(message)
                    messages.append({
                        "role": "user",
                        "content": json_syntax_prescription(str(error)),
                    })
                    audit.append({"event": "retry", "reason": "invalid_json"})
                    continue
                attempts.append({"attempt": retries + 1, "reason": "invalid_json"})
                return self._escalation(
                    task,
                    reason="invalid_json",
                    attempts=attempts,
                    viewed_files=viewed_files,
                    last_patch=last_patch,
                    observed_checks=observed_checks,
                    audit=audit,
                )
            result = dict(result)
            for controller_field in (
                "audit",
                "applied",
                "error",
                "post_apply_checks",
                "validation",
            ):
                result.pop(controller_field, None)
            if "status" not in result:
                result["status"] = "candidate"
            if "summary" not in result:
                result["summary"] = "Task completed"
            if "risks" not in result:
                result["risks"] = []

            if result.get("edits") and result.get("patch"):
                result.pop("patch", None)
                audit.append({"event": "redundant_patch_normalized_to_edits"})
            if not result.get("patch") and not result.get("edits") and last_patch:
                result["patch"] = last_patch[-1]
                audit.append({"event": "patch_reused_from_tool_proposal"})


            result["checks"] = [
                dict(observed_checks[command])
                for command in task.checks
                if command in observed_checks and observed_checks[command].get("passed") is True
            ]
            report = validate_candidate(
                result,
                task,
                max_patch_bytes=self.max_patch_bytes,
                max_patch_files=self.max_patch_files,
                observed_checks=observed_checks,
                workspace_root=self.workspace_root,
            )

            # Semantic linter pre-gate (R18): catch syntax-level breakage before
            # the patch is accepted or applied, with a targeted prescription.
            lint_issues: list[str] = []
            gate_patch = report.resolved_patch or result.get("patch")
            if report.valid and isinstance(gate_patch, str) and gate_patch.strip():
                lint_report = lint_patch_in_memory(str(self.workspace_root), gate_patch)
                if not lint_report.valid:
                    lint_issues = [
                        f"{d.file}:{d.line}: {d.message}" for d in lint_report.diagnostics
                    ]

            result["validation"] = {
                "valid": report.valid,
                "changed_files": list(report.changed_files),
                "issues": list(report.issues),
                "lint_issues": lint_issues,
            }
            if report.resolved_patch:
                result["patch"] = report.resolved_patch
                result.pop("edits", None)

            if not report.valid or lint_issues:
                last_invalid_candidate = dict(result)
                last_invalid_candidate["status"] = "rejected"
                last_invalid_candidate["audit"] = audit
                if turn < self.max_turns and retries < self.max_retries:
                    retries += 1
                    all_issues = [*report.issues, *lint_issues]
                    prescription = prescribe_all(list(report.issues)) if not lint_issues else (
                        "Исправь синтаксические ошибки: " + "; ".join(lint_issues)
                    )
                    feedback_msg = {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "error": "CANDIDATE_VALIDATION_FAILED" if not lint_issues else "SEMANTIC_LINT_FAILED",
                                "issues": all_issues,
                                "instruction": f"ОШИБКА ВАЛИДАЦИИ: {prescription} Исправь эти поля и верни скорректированный JSON-объект.",
                            },
                            ensure_ascii=False,
                        ),
                    }
                    cleaned_assistant_message = dict(message)
                    try:
                        raw_content = message.get("content")
                        if isinstance(raw_content, str) and raw_content.strip():
                            parsed_c = json.loads(raw_content)
                            if isinstance(parsed_c, dict) and parsed_c.get("patch"):
                                parsed_c["patch"] = "<invalid_patch_omitted>"
                                cleaned_assistant_message["content"] = json.dumps(parsed_c, ensure_ascii=False)
                    except (json.JSONDecodeError, TypeError, KeyError, AttributeError):
                        pass
                    messages.append(cleaned_assistant_message)
                    messages.append(feedback_msg)
                    audit.append({
                        "event": "templated_feedback",
                        "reason": "semantic_lint_failed" if lint_issues else "candidate_validation_failed",
                        "issues": [*report.issues, *lint_issues],
                        "prescription": prescription,
                        "turn": turn,
                    })
                    continue

            candidate_valid = bool(report.valid) and not lint_issues
            result["status"] = "accepted" if candidate_valid else "rejected"
            audit.append({"event": "candidate_validated", "valid": candidate_valid})
            if candidate_valid and apply:
                patch = report.resolved_patch or result.get("patch")
                if not isinstance(patch, str) or not patch.strip():
                    audit.append({"event": "apply_skipped", "reason": "candidate has no patch"})
                elif not task.checks:
                    result["status"] = "rejected"
                    self._add_risk(
                        result,
                        "apply_requires_checks",
                        "applying a non-empty patch requires at least one targeted check",
                    )
                    audit.append({"event": "apply_rejected", "reason": "apply_requires_checks"})
                else:
                    applied, apply_detail = _apply_patch(self.workspace_root, patch)
                    if not applied:
                        result["status"] = "rejected"
                        self._add_risk(
                            result,
                            "apply_failed",
                            f"patch could not be applied: {apply_detail}",
                        )
                        audit.append({"event": "apply_failed", "detail": apply_detail})
                    else:
                        audit.append({"event": "patch_applied"})
                        try:
                            post_checks, post_checks_passed = self._run_post_apply_checks(
                                task, tools, active_cancel, audit
                            )
                        except ToolCancelled:
                            rollback_ok, rollback_detail = _apply_patch(
                                self.workspace_root, patch, reverse=True
                            )
                            result["status"] = "failed"
                            self._add_risk(result, "cancelled", "post-apply checks were cancelled")
                            audit.append({"event": "post_apply_cancelled"})
                            if rollback_ok:
                                audit.append({"event": "patch_rolled_back"})
                            else:
                                self._mark_rollback_failure(result, rollback_detail)
                                audit.append(
                                    {"event": "rollback_failed", "detail": rollback_detail}
                                )
                        except ToolPolicyError as error:
                            rollback_ok, rollback_detail = _apply_patch(
                                self.workspace_root, patch, reverse=True
                            )
                            result["status"] = "rejected"
                            self._add_risk(
                                result,
                                "post_apply_check_failed",
                                f"post-apply check could not complete: {error}",
                            )
                            audit.append({"event": "post_apply_check_error", "detail": str(error)})
                            if rollback_ok:
                                audit.append({"event": "patch_rolled_back"})
                            else:
                                self._mark_rollback_failure(result, rollback_detail)
                                audit.append(
                                    {"event": "rollback_failed", "detail": rollback_detail}
                                )
                        else:
                            result["post_apply_checks"] = post_checks
                            if post_checks_passed:
                                result["checks"] = post_checks
                                result["applied"] = True
                                audit.append({"event": "post_apply_checks_passed"})
                            else:
                                rollback_ok, rollback_detail = _apply_patch(
                                    self.workspace_root, patch, reverse=True
                                )
                                result["status"] = "rejected"
                                self._add_risk(
                                    result,
                                    "post_apply_check_failed",
                                    "a targeted check failed after applying the patch",
                                )
                                if rollback_ok:
                                    audit.append({"event": "patch_rolled_back"})
                                else:
                                    self._mark_rollback_failure(result, rollback_detail)
                                    audit.append(
                                        {"event": "rollback_failed", "detail": rollback_detail}
                                    )
            elif apply:
                audit.append({"event": "apply_skipped", "reason": "candidate rejected"})
            if not report.valid or lint_issues:
                self._add_risk(
                    result,
                    "semantic_lint_failed" if lint_issues else "validation",
                    "; ".join(lint_issues) or "; ".join(report.issues),
                )
            result["audit"] = audit
            return result

        if last_patch:
            candidate: dict[str, Any] = {
                "status": "candidate",
                "summary": "Propose patch completed before max turns",
                "patch": last_patch[-1],
                "checks": [
                    dict(observed_checks[command])
                    for command in task.checks
                    if command in observed_checks and observed_checks[command].get("passed") is True
                ],
                "risks": [],
            }
            report = validate_candidate(
                candidate,
                task,
                max_patch_bytes=self.max_patch_bytes,
                max_patch_files=self.max_patch_files,
                observed_checks=observed_checks,
                workspace_root=self.workspace_root,
            )
            # Same semantic lint gate as the in-loop path: a salvaged patch
            # must never be accepted with syntax errors.
            salvage_lint_issues: list[str] = []
            if report.valid and isinstance(candidate.get("patch"), str) and candidate["patch"].strip():
                salvage_lint = lint_patch_in_memory(str(self.workspace_root), candidate["patch"])
                if not salvage_lint.valid:
                    salvage_lint_issues = [
                        f"{d.file}:{d.line}: {d.message}" for d in salvage_lint.diagnostics
                    ]
            candidate["validation"] = {
                "valid": report.valid,
                "changed_files": list(report.changed_files),
                "issues": list(report.issues),
                "lint_issues": salvage_lint_issues,
            }
            salvaged_valid = bool(report.valid) and not salvage_lint_issues
            candidate["status"] = "accepted" if salvaged_valid else "rejected"
            if salvage_lint_issues:
                self._add_risk(candidate, "semantic_lint_failed", "; ".join(salvage_lint_issues))
            candidate["audit"] = audit
            audit.append({"event": "candidate_salvaged_from_last_patch", "valid": salvaged_valid})
            return candidate

        if attempts:
            return self._escalation(
                task,
                reason="max_turns",
                attempts=attempts,
                viewed_files=viewed_files,
                last_patch=last_patch,
                observed_checks=observed_checks,
                audit=audit,
            )
        return self._failure("failed", "max_turns", f"max_turns={self.max_turns} exceeded", audit)

    def _run_post_apply_checks(
        self,
        task: TaskEnvelope,
        tools: BoundedRepositoryTools,
        active_cancel: Event | None,
        audit: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], bool]:
        return run_post_apply_checks(task, tools, active_cancel=active_cancel, audit=audit)

    @staticmethod
    def _add_risk(result: dict[str, Any], kind: str, message: str) -> None:
        risks = result.get("risks")
        if not isinstance(risks, list):
            risks = []
            result["risks"] = risks
        risks.append({"kind": kind, "message": message})

    @classmethod
    def _mark_rollback_failure(cls, result: dict[str, Any], detail: str) -> None:
        result["workspace_modified"] = True
        cls._add_risk(result, "rollback_failed", f"patch rollback failed: {detail}")

    def _messages_size(self, messages) -> int:
        return len(json.dumps(messages, ensure_ascii=False).encode("utf-8"))

    def _compact_messages(
        self, messages: list[dict[str, Any]], *, audit: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Compact messages by evicting historical tool-call exchanges older than 1 turn."""
        compacted, dropped = compact_tool_exchanges(messages, max_bytes=self.max_context_bytes)
        if dropped:
            tool_names = [m.get("tool_name") for m in dropped if m.get("role") == "tool"]
            audit.append({
                "event": "context_compacted",
                "dropped_messages_count": len(dropped),
                "dropped_tool_names": tool_names,
            })
        return compacted

    @staticmethod
    def _find_tool_exchange_blocks(messages: list[dict[str, Any]]) -> list[tuple[int, int]]:
        """Find atomic ranges [start, end) of assistant(tool_calls) and their corresponding tool results.

        Preserves messages[0] (system) and messages[1] (initial task envelope).
        """
        blocks: list[tuple[int, int]] = []
        i = 2
        n = len(messages)
        while i < n:
            msg = messages[i]
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                start = i
                j = i + 1
                while j < n and messages[j].get("role") == "tool":
                    j += 1
                blocks.append((start, j))
                i = j
            else:
                i += 1
        return blocks

    def _initial_messages(self, task: TaskEnvelope) -> list[dict[str, Any]]:
        payload = {
            "id": task.id,
            "goal": task.goal,
            "files": list(task.files),
            "context": task.context,
            "constraints": list(task.constraints),
            "checks": list(task.checks),
            "acceptance": list(task.acceptance),
            "limits": {
                "max_turns": self.max_turns,
                "max_same_call": self.max_same_call,
                "max_tool_result_bytes": self.max_tool_result_bytes,
                "max_files": self.max_files,
                "max_patch_files": self.max_patch_files,
            },
        }
        content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(content.encode("utf-8")) > self.max_context_bytes:
            raise ValueError(f"task context exceeds max_context_bytes={self.max_context_bytes}")
        return [
            {"role": "system", "content": self.system_contract},
            {"role": "user", "content": content},
        ]

    @staticmethod
    def _decode_tool_call(call: Any) -> tuple[str, dict[str, Any], str | None]:
        if not isinstance(call, dict):
            raise ValueError("tool call must be an object")
        call_id = call.get("id")
        if call_id is not None and not isinstance(call_id, str):
            raise ValueError("tool call id must be a string")
        function = call.get("function")
        if not isinstance(function, dict):
            raise ValueError("tool call has no function object")
        name = function.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("tool call has no function name")
        arguments = function.get("arguments", {})
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        if not isinstance(arguments, dict):
            raise ValueError("tool arguments must be an object")
        return name, arguments, call_id

    @staticmethod
    def _decode_content_tool_call(content: Any) -> dict[str, Any] | None:
        if not isinstance(content, str) or not content.strip():
            return None
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        name = payload.get("name")
        arguments = payload.get("arguments")
        if not isinstance(name, str) or not name or not isinstance(arguments, (dict, str)):
            return None
        return {"function": {"name": name, "arguments": arguments}}

    def _tools_for_task(self, task: TaskEnvelope) -> list[dict[str, Any]]:
        if task.checks:
            candidates = TOOL_DEFINITIONS
        else:
            candidates = [
                definition
                for definition in TOOL_DEFINITIONS
                if definition["function"]["name"] != "run_tests"
            ]
        # Don't advertise tools that read-only/plan mode blocks — saves the model
        # from wasting turns on calls that are guaranteed to raise ToolPolicyError.
        if self.blocked_tools:
            candidates = [
                definition
                for definition in candidates
                if definition["function"]["name"] not in self.blocked_tools
            ]
        return candidates

    @staticmethod
    def _parse_final_result(content: Any) -> dict[str, Any]:
        if not isinstance(content, str) or not content.strip():
            raise ValueError("final model response has no JSON content")
        cleaned = content.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()
        try:
            result = json.loads(cleaned)
        except json.JSONDecodeError:
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start != -1 and end != -1 and end > start:
                result = json.loads(cleaned[start : end + 1])
            else:
                raise
        if not isinstance(result, dict):
            raise ValueError("final model response must be a JSON object")
        return result

    @staticmethod
    def _failure(status: str, kind: str, message: str, audit: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "status": status,
            "summary": message,
            "patch": "",
            "checks": [],
            "risks": [{"kind": kind, "message": message}],
            "error": {"kind": kind, "message": message},
            "audit": audit,
        }

    def _escalation(
        self,
        task: TaskEnvelope,
        *,
        reason: str,
        attempts: list[dict[str, Any]],
        viewed_files: set[str],
        last_patch: list[str],
        observed_checks: dict[str, dict[str, Any]],
        audit: list[dict[str, Any]],
    ) -> dict[str, Any]:
        audit.append({"event": "escalation", "reason": reason, "attempts": len(attempts)})
        return {
            "status": "failed",
            "summary": f"retry budget exhausted: {reason}",
            "patch": "",
            "checks": [],
            "risks": [],
            "error": {"kind": "retry_budget_exhausted", "message": reason},
            "escalation": {
                "reason": reason,
                "task": {
                    "id": task.id,
                    "goal": task.goal,
                    "files": list(task.files),
                    "context": task.context,
                    "constraints": list(task.constraints),
                    "checks": list(task.checks),
                    "acceptance": list(task.acceptance),
                },
                "attempts": list(attempts),
                "viewed_files": sorted(viewed_files),
                "last_patch": last_patch[0] if last_patch else "",
                "validation_issues": [],
                "external_evidence": dict(observed_checks),
                "risks": [],
            },
            "audit": audit,
        }
