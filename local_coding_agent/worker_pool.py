"""Bounded in-memory worker pool for proposal-only delegations."""

from __future__ import annotations

import copy
import json
import math
import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Deque, Mapping

from .service import DelegationRequest
from .task_store import TaskRecord, TaskStore



_TERMINAL_STATES = frozenset({"completed", "failed", "cancelled", "interrupted"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ExecutionOverload(RuntimeError):
    """Raised when the shared runtime admission capacity is full."""


class _ExecutionLease:
    def __init__(self, gate: "SharedExecutionGate") -> None:
        self._gate = gate
        self._released = False
        self._lock = threading.Lock()

    def release(self) -> None:
        with self._lock:
            if self._released:
                return
            self._released = True
        self._gate._release()


class SharedExecutionGate:
    """Thread-safe admission and active-slot gate shared by all MCP paths."""

    def __init__(self, max_workers: int, max_queue: int) -> None:
        if max_workers <= 0:
            raise ValueError("max_workers must be positive")
        if max_queue < 0:
            raise ValueError("max_queue must be non-negative")
        self._slots = threading.Semaphore(max_workers)
        self._capacity = max_workers + max_queue
        self._condition = threading.Condition()
        self._admitted = 0

    def try_acquire(self) -> _ExecutionLease | None:
        with self._condition:
            if self._admitted >= self._capacity:
                return None
            self._admitted += 1
        return _ExecutionLease(self)

    def run(self, function, *args):
        lease = self.try_acquire()
        if lease is None:
            raise ExecutionOverload("bounded execution queue is full")
        try:
            return self.run_reserved(lease, function, *args)
        finally:
            lease.release()

    def run_reserved(self, lease: _ExecutionLease, function, *args):
        """Run an already-admitted task while holding a shared active slot."""

        self._slots.acquire()
        try:
            return function(*args)
        finally:
            self._slots.release()

    def _release(self) -> None:
        with self._condition:
            self._admitted -= 1
            self._condition.notify_all()


@dataclass
class _Job:
    job_id: str
    caller_id: str
    request_key: tuple[str, str, str]
    fingerprint: str
    request: DelegationRequest
    state: str = "queued"
    created_at: str = ""
    updated_at: str = ""
    result: dict[str, Any] | None = None
    cancel_event: threading.Event | None = None
    execution_complete: threading.Event | None = None
    completed: threading.Event | None = None
    execution_lease: _ExecutionLease | None = None


class BoundedWorkerPool:
    """Run service delegations with bounded queue and caller-scoped jobs.

    This is deliberately an in-memory execution primitive. It provides bounded
    overload behavior and cooperative cancellation for a future task adapter,
    but it does not claim durable persistence or MCP Tasks conformance.
    """

    def __init__(
        self,
        service: Any,
        *,
        max_workers: int = 1,
        max_queue: int = 16,
        max_completed_jobs: int = 256,
        execution_gate: SharedExecutionGate | None = None,
        task_store: TaskStore | None = None,
    ) -> None:
        if max_workers <= 0:
            raise ValueError("max_workers must be positive")
        if max_queue < 0:
            raise ValueError("max_queue must be non-negative")
        if max_completed_jobs <= 0:
            raise ValueError("max_completed_jobs must be positive")
        self._service = service
        self._max_completed_jobs = max_completed_jobs
        self._condition = threading.Condition()
        self._queue: Deque[str] = deque()
        self._jobs: dict[str, _Job] = {}
        self._idempotency: dict[tuple[str, str, str], str] = {}
        self._max_workers = max_workers
        self._max_queue = max_queue
        self._execution_gate = execution_gate
        self._task_store = task_store
        self._active_workers = 0
        self._stopping = False
        if self._task_store is not None:
            for record in self._task_store.list(limit=self._max_completed_jobs):
                completed_evt = threading.Event()
                completed_evt.set()
                recovered_result = copy.deepcopy(record.result)
                if record.state == "interrupted":
                    # The durable store has no request envelope to safely
                    # replay after a process restart. Keep the explicit
                    # interrupted state, but expose a terminal controller
                    # result so pollers cannot wait forever in "working".
                    interruption = copy.deepcopy(record.error) or {
                        "kind": "process_interrupted",
                        "message": "task was interrupted by server restart",
                    }
                    recovered_result = dict(recovered_result or {})
                    recovered_result.update(
                        {
                            "status": "failed",
                            "error": interruption,
                            "applied": False,
                        }
                    )
                job = _Job(
                    job_id=record.task_id,
                    caller_id=record.caller_id,
                    request_key=(record.caller_id, record.workspace_ref, record.request_id),
                    fingerprint=record.fingerprint,
                    request=None,  # type: ignore[arg-type]
                    state=record.state,
                    created_at=record.created_at,
                    updated_at=record.updated_at,
                    result=recovered_result,
                    cancel_event=threading.Event(),
                    execution_complete=threading.Event(),
                    completed=completed_evt,
                )
                self._jobs[record.task_id] = job
                self._idempotency[job.request_key] = job.job_id

        self._workers = [
            threading.Thread(
                target=self._worker_loop,
                name=f"local-agent-worker-{index}",
                daemon=True,
            )
            for index in range(max_workers)
        ]
        for worker in self._workers:
            worker.start()

    def _persist_job(self, job: _Job) -> None:
        if self._task_store is None:
            return
        try:
            record = TaskRecord(
                task_id=job.job_id,
                caller_id=job.caller_id,
                request_id=job.request.request_id if job.request else job.job_id,
                workspace_ref=job.request.workspace_ref if job.request else "",
                model_profile=job.request.model_profile if job.request else "",
                state=job.state,
                created_at=job.created_at,
                updated_at=job.updated_at,
                result=job.result,
                error=job.result.get("error") if isinstance(job.result, dict) else None,
                fingerprint=job.fingerprint,
            )
            self._task_store.save(record)
        except Exception:
            pass

    def submit(self, caller_id: str, request: DelegationRequest) -> dict[str, Any]:
        """Queue one proposal-only request or return a bounded policy failure."""

        if not isinstance(caller_id, str) or not caller_id.strip():
            return self._failure("invalid_caller", "caller_id must be a non-empty string")
        if not isinstance(request, DelegationRequest):
            return self._failure("invalid_request", "request must be a DelegationRequest")

        request_key = (caller_id, request.workspace_ref, request.request_id)
        fingerprint = self._fingerprint(request)
        with self._condition:
            existing_id = self._idempotency.get(request_key)
            if existing_id is not None:
                existing = self._jobs.get(existing_id)
                if existing is not None:
                    if existing.fingerprint and existing.fingerprint != fingerprint:
                        return self._failure(
                            "idempotency_conflict",
                            "request_id was already used with a different request payload",
                        )
                    return self._snapshot(existing)
                self._idempotency.pop(request_key, None)

            if self._stopping:
                return self._failure("pool_shutdown", "worker pool is shutting down")
            if self._active_workers + len(self._queue) >= self._max_workers + self._max_queue:
                return self._failure(
                    "queue_overload",
                    f"worker capacity is full at max_workers={self._max_workers}, "
                    f"max_queue={self._max_queue}",
                )

            execution_lease = self._execution_gate.try_acquire() if self._execution_gate is not None else None
            if self._execution_gate is not None and execution_lease is None:
                return self._failure(
                    "queue_overload",
                    "shared execution capacity is full",
                )

            job = _Job(
                job_id=uuid.uuid4().hex,
                caller_id=caller_id,
                request_key=request_key,
                fingerprint=fingerprint,
                request=request,
                created_at=_now_iso(),
                updated_at=_now_iso(),
                cancel_event=threading.Event(),
                execution_complete=threading.Event(),
                completed=threading.Event(),
                execution_lease=execution_lease,
            )
            self._jobs[job.job_id] = job
            self._idempotency[request_key] = job.job_id
            self._queue.append(job.job_id)
            self._persist_job(job)
            self._condition.notify()
            return self._snapshot(job)


    def get(self, caller_id: str, job_id: str) -> dict[str, Any]:
        """Return a caller-scoped job snapshot without waiting."""

        with self._condition:
            job = self._owned_job(caller_id, job_id)
            if job is None:
                return self._failure("unknown_job", "job is unknown or belongs to another caller")
            return self._snapshot(job)

    def status(self) -> dict[str, Any]:
        """Return a snapshot of worker pool load, queue, and jobs for monitoring."""
        with self._condition:
            jobs_summary = [
                {
                    "job_id": job.job_id,
                    "caller_id": job.caller_id,
                    "state": job.state,
                    "created_at": job.created_at,
                    "updated_at": job.updated_at,
                    "has_result": job.result is not None,
                }
                for job in self._jobs.values()
            ]
            return {
                "active_workers": self._active_workers,
                "max_workers": self._max_workers,
                "queued_jobs": len(self._queue),
                "max_queue": self._max_queue,
                "stopping": self._stopping,
                "total_jobs": len(self._jobs),
                "jobs": jobs_summary,
            }


    def wait(self, caller_id: str, job_id: str, *, timeout: float = 5.0) -> dict[str, Any]:
        """Wait at most ``timeout`` seconds, then return the current snapshot."""

        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout < 0:
            return self._failure("invalid_timeout", "timeout must be a finite non-negative number")
        with self._condition:
            job = self._owned_job(caller_id, job_id)
            if job is None:
                return self._failure("unknown_job", "job is unknown or belongs to another caller")
            completed = job.completed
        assert completed is not None
        completed.wait(timeout)
        with self._condition:
            return self._snapshot(job)

    def cancel(self, caller_id: str, job_id: str) -> dict[str, Any]:
        """Request cooperative cancellation for a queued or running job."""

        with self._condition:
            job = self._owned_job(caller_id, job_id)
            if job is None:
                return self._failure("unknown_job", "job is unknown or belongs to another caller")
            assert job.cancel_event is not None
            if job.state == "queued":
                try:
                    self._queue.remove(job.job_id)
                except ValueError:
                    # A worker may have claimed the job between the state check
                    # and removal. The worker owns the running transition.
                    if job.state != "queued":
                        return self._snapshot(job)
                job.cancel_event.set()
                job.state = "cancelled"
                job.updated_at = _now_iso()
                self._release_execution_lease(job)
                self._complete_locked(job)
                self._persist_job(job)
                self._condition.notify_all()
                return self._snapshot(job)
            if job.state == "working":
                job.cancel_event.set()
                snapshot = self._snapshot(job)
                snapshot["cancellation_requested"] = True
                return snapshot
            return self._snapshot(job)

    def shutdown(self, *, wait: bool = True, timeout: float = 5.0) -> bool:
        """Stop workers and cancel pending work with a bounded join."""

        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout < 0:
            raise ValueError("timeout must be a finite non-negative number")
        with self._condition:
            self._stopping = True
            while self._queue:
                job_id = self._queue.popleft()
                job = self._jobs.get(job_id)
                if job is None or job.state != "queued":
                    continue
                assert job.cancel_event is not None
                job.cancel_event.set()
                job.state = "cancelled"
                self._release_execution_lease(job)
                self._complete_locked(job)
                self._persist_job(job)
            for job in self._jobs.values():
                if job.state == "working" and job.cancel_event is not None:
                    job.cancel_event.set()
            self._condition.notify_all()

        if not wait:
            return not any(worker.is_alive() for worker in self._workers)
        deadline = time.monotonic() + timeout
        for worker in self._workers:
            remaining = max(0.0, deadline - time.monotonic())
            worker.join(remaining)
        return not any(worker.is_alive() for worker in self._workers)

    def _worker_loop(self) -> None:
        while True:
            with self._condition:
                while not self._queue and not self._stopping:
                    self._condition.wait()
                if not self._queue and self._stopping:
                    return
                job_id = self._queue.popleft()
                job = self._jobs.get(job_id)
                if job is None or job.state == "cancelled":
                    if job is not None:
                        self._release_execution_lease(job)
                    continue
                job.state = "working"
                job.updated_at = _now_iso()
                self._active_workers += 1
                self._persist_job(job)

            try:
                assert job.cancel_event is not None
                assert job.execution_complete is not None
                if self._execution_gate is not None and job.execution_lease is not None:
                    def delegate_and_wait():
                        try:
                            result = self._service.delegate(
                                job.caller_id,
                                job.request,
                                cancel_event=job.cancel_event,
                                completion_event=job.execution_complete,
                            )
                        finally:
                            job.execution_complete.set()
                        return result

                    result = self._execution_gate.run_reserved(job.execution_lease, delegate_and_wait)
                else:
                    try:
                        result = self._service.delegate(
                            job.caller_id,
                            job.request,
                            cancel_event=job.cancel_event,
                            completion_event=job.execution_complete,
                        )
                    finally:
                        job.execution_complete.set()
                if not isinstance(result, Mapping):
                    raise TypeError("service returned a non-object result")
                result = dict(result)
            except Exception:  # noqa: BLE001 - worker boundary must terminate the job.
                result = self._failure("worker_error", "delegation worker failed")
            finally:
                if job.execution_complete is not None:
                    job.execution_complete.set()


            assert job.execution_complete is not None
            # A service may finish its public call before a cancellable model
            # thread has actually terminated. Keep the worker slot occupied
            # until the service reports that physical execution is complete.
            job.execution_complete.wait()
            self._release_execution_lease(job)

            with self._condition:
                self._active_workers -= 1
                if job.cancel_event is not None and job.cancel_event.is_set():
                    job.state = "cancelled"
                    job.result = None
                else:
                    job.result = copy.deepcopy(result)
                    job.state = "failed" if result.get("status") == "failed" else "completed"
                job.updated_at = _now_iso()
                self._complete_locked(job)
                self._persist_job(job)
                self._condition.notify_all()


    def _complete_locked(self, job: _Job) -> None:
        assert job.completed is not None
        job.completed.set()
        self._evict_completed_locked()

    @staticmethod
    def _release_execution_lease(job: _Job) -> None:
        if job.execution_lease is not None:
            job.execution_lease.release()
            job.execution_lease = None

    def _evict_completed_locked(self) -> None:
        terminal_jobs = sum(job.state in _TERMINAL_STATES for job in self._jobs.values())
        while terminal_jobs > self._max_completed_jobs:
            candidate_id = next(
                (job_id for job_id, job in self._jobs.items() if job.state in _TERMINAL_STATES),
                None,
            )
            if candidate_id is None:
                return
            candidate = self._jobs.pop(candidate_id)
            if self._idempotency.get(candidate.request_key) == candidate_id:
                self._idempotency.pop(candidate.request_key, None)
            terminal_jobs -= 1

    def _owned_job(self, caller_id: str, job_id: str) -> _Job | None:
        job = self._jobs.get(job_id)
        if job is None or job.caller_id != caller_id:
            return None
        return job

    @staticmethod
    def _snapshot(job: _Job) -> dict[str, Any]:
        snapshot: dict[str, Any] = {
            "job_id": job.job_id,
            "status": job.state,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
        }
        if job.state in {"completed", "failed", "interrupted"} and job.result is not None:
            snapshot["result"] = copy.deepcopy(job.result)
        if job.state == "working" and job.cancel_event is not None and job.cancel_event.is_set():
            snapshot["cancellation_requested"] = True
        return snapshot

    @staticmethod
    def _fingerprint(request: DelegationRequest) -> str:
        return json.dumps(asdict(request), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _failure(kind: str, message: str) -> dict[str, Any]:
        return {
            "status": "failed",
            "error": {"kind": kind, "message": message},
            "applied": False,
        }
