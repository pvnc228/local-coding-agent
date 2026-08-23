"""Transport-neutral, proposal-only entry point for bounded delegations."""

from __future__ import annotations

import copy
import json
import sys
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Event, RLock
from typing import Any, Callable, Mapping

from .atomizer import TaskBudget, preflight
from .controller import Controller, ModelClient, run_post_apply_checks
from .ollama_adapter import ModelProfile, build_client
from .profiles import get_profile
from .repository_tools import BoundedRepositoryTools, ToolCancelled, ToolPolicyError
from .semantic_linter import lint_patch_in_memory
from .stats import append_stats, default_stats_path
from .task import TaskEnvelope
from .validators import apply_patch, check_patch_applies




@dataclass(frozen=True)
class DelegationRequest:
    """A host-approved request that is independent of any transport schema."""

    request_id: str
    workspace_ref: str
    model_profile: str
    task: TaskEnvelope

    def __post_init__(self) -> None:
        for name, value in (
            ("request_id", self.request_id),
            ("workspace_ref", self.workspace_ref),
            ("model_profile", self.model_profile),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"delegation field '{name}' must be a non-empty string")
        if not isinstance(self.task, TaskEnvelope):
            raise ValueError("delegation field 'task' must be a TaskEnvelope")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DelegationRequest":
        if not isinstance(value, Mapping):
            raise ValueError("delegation request must be an object")
        task = value.get("task")
        if not isinstance(task, Mapping):
            raise ValueError("delegation field 'task' must be an object")
        return cls(
            request_id=value.get("request_id"),
            workspace_ref=value.get("workspace_ref"),
            model_profile=value.get("model_profile"),
            task=TaskEnvelope.from_mapping(task),
        )


@dataclass
class _CachedResult:
    fingerprint: str
    completed: Event
    result: dict[str, Any] | None = None
    request: DelegationRequest | None = None


class DelegationService:
    """Direct adapter that resolves only host-registered workspaces and profiles.

    Direct ``delegate`` remains proposal-only and does not accept an apply flag
    or arbitrary paths. The separate mediated ``apply`` method is host-facing,
    confirmation-bound and still keeps policy/result ownership in the controller
    and service boundary.
    """

    def __init__(
        self,
        workspaces: Mapping[str, str | Path],
        *,
        model_factory: Callable[[ModelProfile], ModelClient] = build_client,
        max_turns: int = 4,
        max_cached_results: int = 256,
        preflight_budget: TaskBudget = TaskBudget(),
    ) -> None:
        if max_turns <= 0:
            raise ValueError("max_turns must be positive")
        if max_cached_results <= 0:
            raise ValueError("max_cached_results must be positive")
        if not isinstance(preflight_budget, TaskBudget):
            raise ValueError("preflight_budget must be a TaskBudget")
        registered: dict[str, Path] = {}
        for reference, raw_path in workspaces.items():
            if not isinstance(reference, str) or not reference.strip():
                raise ValueError("workspace references must be non-empty strings")
            path = Path(raw_path).resolve()
            if not path.is_dir():
                raise ValueError(f"registered workspace {reference!r} is not a directory: {path}")
            registered[reference] = path
        self._workspaces = registered
        self._apply_locks = {reference: RLock() for reference in registered}
        self._model_factory = model_factory
        self._max_turns = max_turns
        self._max_cached_results = max_cached_results
        self._preflight_budget = preflight_budget
        self._cache: OrderedDict[tuple[str, str, str], _CachedResult] = OrderedDict()
        self._cache_lock = RLock()

    def delegate(
        self,
        caller_id: str,
        request: DelegationRequest,
        *,
        cancel_event: Event | None = None,
        completion_event: Event | None = None,
    ) -> dict[str, Any]:
        """Run one proposal-only delegation with caller-scoped idempotency."""

        controller_started = Event()
        try:
            if not isinstance(caller_id, str) or not caller_id.strip():
                return self._policy_failure("invalid_caller", "caller_id must be a non-empty string")
            if not isinstance(request, DelegationRequest):
                return self._policy_failure("invalid_request", "request must be a DelegationRequest")

            cache_key = (caller_id, request.workspace_ref, request.request_id)
            fingerprint = self._fingerprint(request)
            with self._cache_lock:
                cached = self._cache.get(cache_key)
                if cached is None:
                    if len(self._cache) >= self._max_cached_results:
                        for stale_key, stale_record in self._cache.items():
                            if stale_record.completed.is_set():
                                self._cache.pop(stale_key)
                                break
                        else:
                            return self._policy_failure(
                                "idempotency_capacity",
                                "in-memory idempotency capacity is exhausted by active requests",
                            )
                    cached = _CachedResult(fingerprint=fingerprint, completed=Event(), request=request)
                    self._cache[cache_key] = cached
                    owner = True
                elif cached.fingerprint != fingerprint:
                    return self._policy_failure(
                        "idempotency_conflict",
                        "request_id was already used with a different request payload",
                    )
                else:
                    self._cache.move_to_end(cache_key)
                    owner = False

            if not owner:
                cached.completed.wait()
                assert cached.result is not None
                return copy.deepcopy(cached.result)

            result: dict[str, Any] | None = None
            started_ns = time.monotonic_ns()
            try:
                try:
                    result = self._execute(
                        request,
                        cancel_event=cancel_event,
                        completion_event=completion_event,
                        controller_started=controller_started,
                    )
                except Exception as error:
                    import traceback
                    traceback.print_exc(file=sys.stderr)
                    result = self._policy_failure("controller_error", f"controller execution failed: {error}")
            finally:
                if result is None:
                    result = self._policy_failure("interrupted", "controller execution interrupted")
                normalized = self._normalize_result(result)
                append_stats(
                    default_stats_path(),
                    normalized,
                    model=request.model_profile,
                    latency_ns=time.monotonic_ns() - started_ns,
                )
                with self._cache_lock:
                    cached.result = copy.deepcopy(normalized)
                    cached.completed.set()
                    self._evict_completed_results()
            return copy.deepcopy(normalized)
        finally:
            if completion_event is not None and not controller_started.is_set():
                completion_event.set()

    def _execute(
        self,
        request: DelegationRequest,
        *,
        cancel_event: Event | None = None,
        completion_event: Event | None = None,
        controller_started: Event | None = None,
    ) -> dict[str, Any]:
        workspace = self._workspaces.get(request.workspace_ref)
        if workspace is None:
            return self._policy_failure(
                "unknown_workspace",
                f"workspace_ref is not registered: {request.workspace_ref!r}",
            )
        report = preflight(request.task, self._preflight_budget)
        if not report.accepted:
            return self._policy_failure("preflight_rejected", report.reason or "preflight_rejected")
        try:
            profile = get_profile(request.model_profile)
        except ValueError:
            return self._policy_failure(
                "unknown_model_profile",
                f"model_profile is not allowlisted: {request.model_profile!r}",
            )
        model = self._model_factory(profile)
        # apply is intentionally absent: direct delegation always remains a proposal.
        if controller_started is not None:
            controller_started.set()
        return Controller(
            model,
            workspace,
            max_turns=self._max_turns,
            max_files=self._preflight_budget.max_files,
            preflight_budget=self._preflight_budget,
            system_contract=profile.system_contract,
        ).run(
            request.task,
            cancel_event=cancel_event,
            completion_event=completion_event,
        )

    def apply(
        self,
        caller_id: str,
        workspace_ref: str,
        request_id: str,
        *,
        cancel_event: Event | None = None,
    ) -> dict[str, Any]:
        """Apply a previously stored terminal proposal to its workspace.

        Mediated-apply: revalidates the stored patch against the current
        workspace, applies it, runs the task's allowlisted checks, and rolls
        back on any failure. Only a stored terminal proposal whose status is
        ``accepted`` and that carries a resolved ``patch`` is applied; the local
        model is never invoked again.
        """

        audit: list[dict[str, Any]] = [{"event": "apply_requested", "request_id": request_id}]
        if not isinstance(caller_id, str) or not caller_id.strip():
            return self._policy_failure("invalid_caller", "caller_id must be a non-empty string")
        cache_key = (caller_id, workspace_ref, request_id)
        with self._cache_lock:
            record = self._cache.get(cache_key)
        if record is None:
            return self._apply_failure("unknown_proposal", "proposal is unknown for this caller/workspace", audit)
        assert record.request is not None
        request = record.request
        record.completed.wait()
        proposal = record.result
        if proposal is None:
            return self._apply_failure("unknown_proposal", "proposal has no stored result", audit)
        if proposal.get("status") != "accepted":
            return self._apply_failure("proposal_not_accepted", "only an accepted proposal may be applied", audit)
        patch = proposal.get("patch")
        if not isinstance(patch, str) or not patch.strip():
            return self._apply_failure("no_patch", "proposal carries no resolved patch", audit)
        if not request.task.checks:
            return self._apply_failure(
                "apply_requires_checks",
                "applying a non-empty patch requires at least one targeted check",
                audit,
            )

        workspace = self._workspaces.get(request.workspace_ref)
        if workspace is None:
            return self._apply_failure("unknown_workspace", "workspace_ref is not registered", audit)

        workspace_lock = self._apply_locks.get(request.workspace_ref)
        if workspace_lock is None:
            return self._apply_failure("unknown_workspace", "workspace_ref is not registered", audit)
        with workspace_lock:
            return self._apply_to_workspace(request, proposal, workspace, patch, cancel_event, audit)

    def _apply_to_workspace(
        self,
        request: DelegationRequest,
        proposal: Mapping[str, Any],
        workspace: Path,
        patch: str,
        cancel_event: Event | None,
        audit: list[dict[str, Any]],
    ) -> dict[str, Any]:

        try:
            applies, detail = check_patch_applies(workspace, patch)
        except ValueError as error:
            return self._apply_failure("apply_policy", str(error), audit)
        if not applies:
            return self._apply_failure(
                "stale_workspace",
                f"proposal no longer applies cleanly to the workspace: {detail}",
                audit,
            )

        # Semantic linter pre-gate (R18): never mutate the workspace with a
        # patch that introduces syntax errors.
        lint_report = lint_patch_in_memory(str(workspace), patch)
        if not lint_report.valid:
            return self._apply_failure(
                "semantic_lint_failed",
                "; ".join(lint_report.prescriptions) or "patch failed static analysis",
                audit,
            )

        result: dict[str, Any] = {
            "status": "accepted",
            "summary": proposal.get("summary", ""),
            "patch": patch,
            "checks": [],
            "risks": [],
            "audit": audit,
        }
        try:
            tools = BoundedRepositoryTools(workspace, request.task, cancel_event=cancel_event)
        except (ToolPolicyError, ValueError) as error:
            return self._apply_failure("apply_policy", str(error), audit)
        applied, apply_detail = apply_patch(workspace, patch)
        if not applied:
            return self._apply_failure("apply_failed", f"patch could not be applied: {apply_detail}", audit)
        audit.append({"event": "patch_applied"})
        try:
            post_checks, post_checks_passed = run_post_apply_checks(
                request.task, tools, active_cancel=cancel_event, audit=audit
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
            result["post_apply_checks"] = post_checks
            if post_checks_passed:
                result["checks"] = post_checks
                result["applied"] = True
                audit.append({"event": "post_apply_checks_passed"})
                return result
            result["status"] = "rejected"
            result["error"] = {"kind": "post_apply_check_failed", "message": "a targeted check failed after applying the patch"}
            audit.append({"event": "post_apply_check_failed"})

        # Reached only on a cancelled / policy-error / unexpected-error / failed
        # check: roll the patch back. A failed rollback is surfaced explicitly so
        # a consumer never mistakes a still-modified workspace for a clean one.
        rollback_ok, rollback_detail = apply_patch(workspace, patch, reverse=True)
        if rollback_ok:
            audit.append({"event": "patch_rolled_back"})
        else:
            result["workspace_modified"] = True
            self._add_risk(result, "rollback_failed", f"patch rollback failed: {rollback_detail}")
            audit.append({"event": "rollback_failed", "detail": rollback_detail})
        return result

    def proposal_preview(
        self,
        caller_id: str,
        workspace_ref: str,
        request_id: str,
    ) -> dict[str, Any]:
        """Return bounded, read-only details for an informed apply confirmation."""

        cache_key = (caller_id, workspace_ref, request_id)
        with self._cache_lock:
            record = self._cache.get(cache_key)
        if record is None or record.request is None:
            return {"status": "unknown_proposal", "request_id": request_id}
        if not record.completed.is_set() or record.result is None:
            return {"status": "working", "request_id": request_id}
        proposal = record.result
        return {
            "status": proposal.get("status"),
            "request_id": request_id,
            "workspace_ref": workspace_ref,
            "summary": proposal.get("summary", ""),
            "files": list(record.request.task.files),
            "patch": proposal.get("patch", ""),
        }

    @staticmethod
    def _add_risk(result: dict[str, Any], kind: str, message: str) -> None:
        risks = result.get("risks")
        if not isinstance(risks, list):
            risks = []
            result["risks"] = risks
        risks.append({"kind": kind, "message": message})

    @staticmethod
    def _apply_failure(kind: str, message: str, audit: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "status": "failed",
            "error": {"kind": kind, "message": message},
            "audit": [*audit, {"event": "apply_rejected", "kind": kind}],
        }

    def _evict_completed_results(self) -> None:
        while len(self._cache) > self._max_cached_results:
            for key, record in self._cache.items():
                if record.completed.is_set():
                    self._cache.pop(key)
                    break
            else:
                # Active reservations must keep their idempotency boundary.
                return

    @staticmethod
    def _normalize_result(result: Mapping[str, Any]) -> dict[str, Any]:
        normalized = dict(result)
        normalized["applied"] = False
        return normalized

    @staticmethod
    def _fingerprint(request: DelegationRequest) -> str:
        return json.dumps(asdict(request), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _policy_failure(kind: str, message: str) -> dict[str, Any]:
        return {
            "status": "failed",
            "error": {"kind": kind, "message": message},
            "audit": [{"event": "policy_rejected", "kind": kind}],
        }
