"""Continuable background subagents and coordinator (R30).

Provides lightweight in-process subagent coordination, isolated TaskEnvelopes,
mailbox-based inter-agent messaging, status reporting, and continuable worker loops.
"""

from __future__ import annotations

import copy
import math
import queue
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .task import TaskEnvelope


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_copy(val: Any) -> Any:
    """Safely copy or serialize object payloads, handling uncopyable types gracefully."""
    try:
        return copy.deepcopy(val)
    except Exception:
        if isinstance(val, dict):
            return {str(k): _safe_copy(v) for k, v in val.items()}
        elif isinstance(val, (list, tuple, set)):
            return [_safe_copy(x) for x in val]
        return str(val)


@dataclass(frozen=True)
class MailboxMessage:
    """A message routed between subagents or the coordinator."""

    id: str
    sender_id: str
    target_id: str
    content: Any
    timestamp: str = field(default_factory=_now_iso)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "sender_id": self.sender_id,
            "target_id": self.target_id,
            "content": _safe_copy(self.content),
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True)
class SubagentReport:
    """A progress or status report emitted by a subagent."""

    report_id: str
    agent_id: str
    status: str
    payload: dict[str, Any]
    timestamp: str = field(default_factory=_now_iso)

    def as_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "agent_id": self.agent_id,
            "status": self.status,
            "payload": _safe_copy(self.payload),
            "timestamp": self.timestamp,
        }


@dataclass
class SubagentContext:
    """Runtime execution context provided to child subagent workers."""

    agent_id: str
    role: str
    goal: str
    task: TaskEnvelope
    profile: str
    allowed_tools: tuple[str, ...]
    coordinator: "SubagentCoordinator"
    cancel_event: threading.Event
    workspace_ref: str = "default"
    parent_id: str | None = None

    def send_message(self, target_agent_id: str, message: Any) -> bool:
        """Send a message to another agent or the coordinator."""
        return self.coordinator.send_message(target_agent_id, message, sender_id=self.agent_id)

    def receive_messages(self, clear: bool = True) -> list[dict[str, Any]]:
        """Receive pending messages from this agent's mailbox."""
        return self.coordinator.receive_messages(self.agent_id, clear=clear)

    def report(self, status: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Report intermediate progress or results to the coordinator."""
        return self.coordinator.report(self.agent_id, status, payload)

    def is_cancelled(self) -> bool:
        """True if cooperative cancellation was requested."""
        return self.cancel_event.is_set()


@dataclass
class _SubagentRecord:
    agent_id: str
    role: str
    goal: str
    profile: str
    task: TaskEnvelope
    allowed_tools: tuple[str, ...]
    workspace_ref: str
    parent_id: str | None
    status: str  # "pending", "running", "paused", "completed", "failed", "cancelled"
    created_at: str
    updated_at: str
    cancel_event: threading.Event
    pause_event: threading.Event
    step_event: threading.Event
    completed_event: threading.Event
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    thread: threading.Thread | None = None


class SubagentCoordinator:
    """Coordinates continuable background subagents with isolated context and mailboxes."""

    DEFAULT_TOOLS = ("list_files", "read_file", "search_text", "propose_patch")

    def __init__(
        self,
        *,
        workspaces: Mapping[str, str | Path] | None = None,
        max_workers: int = 8,
        default_profile: str = "default",
    ) -> None:
        if max_workers <= 0:
            raise ValueError("max_workers must be positive")
        self._max_workers = max_workers
        self._default_profile = default_profile
        self._workspaces: dict[str, Path] = {}
        if workspaces:
            for ref, path in workspaces.items():
                self._workspaces[ref] = Path(path).resolve()
        
        self._lock = threading.RLock()
        self._agents: dict[str, _SubagentRecord] = {}
        self._mailboxes: dict[str, list[MailboxMessage]] = {}
        self._reports: dict[str, list[SubagentReport]] = {}
        self._stopping = False

    def spawn_subagent(
        self,
        role: str,
        goal: str,
        files: Sequence[str] | tuple[str, ...],
        profile: str | None = None,
        *,
        allowed_tools: Sequence[str] | None = None,
        context: str = "",
        constraints: Sequence[str] = (),
        checks: Sequence[str] = (),
        acceptance: Sequence[str] = (),
        workspace_ref: str = "default",
        parent_id: str | None = None,
        worker_loop: Callable[[SubagentContext], dict[str, Any] | None] | None = None,
        agent_id: str | None = None,
    ) -> str:
        """Spawn a new background subagent with an isolated TaskEnvelope and mailbox."""
        if not isinstance(role, str) or not role.strip():
            raise ValueError("role must be a non-empty string")
        if not isinstance(goal, str) or not goal.strip():
            raise ValueError("goal must be a non-empty string")
        if not files:
            raise ValueError("files must not be empty")

        selected_id = agent_id or f"subagent-{uuid.uuid4().hex[:12]}"
        selected_profile = profile or self._default_profile
        tools_subset = tuple(allowed_tools) if allowed_tools is not None else self.DEFAULT_TOOLS

        task = TaskEnvelope(
            id=selected_id,
            goal=goal,
            files=tuple(files),
            context=context,
            constraints=tuple(constraints),
            checks=tuple(checks),
            acceptance=tuple(acceptance),
        )

        cancel_evt = threading.Event()
        pause_evt = threading.Event()
        pause_evt.set()  # Not paused by default
        step_evt = threading.Event()
        completed_evt = threading.Event()

        record = _SubagentRecord(
            agent_id=selected_id,
            role=role,
            goal=goal,
            profile=selected_profile,
            task=task,
            allowed_tools=tools_subset,
            workspace_ref=workspace_ref,
            parent_id=parent_id,
            status="pending",
            created_at=_now_iso(),
            updated_at=_now_iso(),
            cancel_event=cancel_evt,
            pause_event=pause_evt,
            step_event=step_evt,
            completed_event=completed_evt,
        )

        with self._lock:
            if self._stopping:
                raise RuntimeError("SubagentCoordinator is shutting down")
            active_count = sum(1 for r in self._agents.values() if r.thread is not None and r.thread.is_alive())
            if active_count >= self._max_workers:
                raise RuntimeError(f"Maximum active workers limit reached ({self._max_workers})")
            if selected_id in self._agents:
                raise ValueError(f"subagent '{selected_id}' already exists")
            self._agents[selected_id] = record
            self._mailboxes[selected_id] = []
            self._reports[selected_id] = []

        subagent_ctx = SubagentContext(
            agent_id=selected_id,
            role=role,
            goal=goal,
            task=task,
            profile=selected_profile,
            allowed_tools=tools_subset,
            coordinator=self,
            cancel_event=cancel_evt,
            workspace_ref=workspace_ref,
            parent_id=parent_id,
        )

        thread = threading.Thread(
            target=self._run_child_worker,
            args=(record, subagent_ctx, worker_loop),
            name=f"dsh-subagent-{selected_id}",
            daemon=True,
        )
        record.thread = thread
        thread.start()

        return selected_id

    def _run_child_worker(
        self,
        record: _SubagentRecord,
        ctx: SubagentContext,
        custom_loop: Callable[[SubagentContext], dict[str, Any] | None] | None,
    ) -> None:
        """In-process execution loop for child subagents."""
        with self._lock:
            if record.cancel_event.is_set():
                record.status = "cancelled"
                record.updated_at = _now_iso()
                record.completed_event.set()
                return
            record.status = "running"
            record.updated_at = _now_iso()

        try:
            if custom_loop is not None:
                outcome = custom_loop(ctx)
                with self._lock:
                    if record.cancel_event.is_set():
                        record.status = "cancelled"
                    else:
                        record.status = "completed" if outcome and outcome.get("status") != "failed" else "failed"
                        record.result = outcome if isinstance(outcome, dict) else {"status": "completed"}
                    record.updated_at = _now_iso()
            else:
                # Default continuable in-process execution loop
                self._default_continuable_loop(record, ctx)
        except Exception as exc:
            with self._lock:
                record.status = "failed"
                record.error = {"kind": "subagent_exception", "message": str(exc)}
                record.result = {"status": "failed", "error": record.error}
                record.updated_at = _now_iso()
        finally:
            record.completed_event.set()

    def _default_continuable_loop(self, record: _SubagentRecord, ctx: SubagentContext) -> None:
        """Default iterative turns execution with mailbox check and reporting."""
        max_turns = 5
        turn = 0
        while turn < max_turns:
            if record.cancel_event.is_set():
                with self._lock:
                    record.status = "cancelled"
                    record.updated_at = _now_iso()
                return

            # Respect pause/step
            record.pause_event.wait()
            if record.cancel_event.is_set():
                with self._lock:
                    record.status = "cancelled"
                    record.updated_at = _now_iso()
                return

            turn += 1
            incoming = ctx.receive_messages(clear=True)
            ctx.report(
                "working",
                {
                    "turn": turn,
                    "processed_messages": len(incoming),
                    "goal": record.goal,
                    "role": record.role,
                },
            )

            # Check if any incoming message commands termination or conclusion
            for msg in incoming:
                content = msg.get("content")
                if isinstance(content, dict) and content.get("action") == "finish":
                    with self._lock:
                        record.status = "completed"
                        record.result = {
                            "status": "accepted",
                            "summary": f"Completed subagent task for role {record.role}",
                            "patch": content.get("patch", ""),
                        }
                        record.updated_at = _now_iso()
                    return

            time.sleep(0.01)

        # Default completion
        with self._lock:
            record.status = "completed"
            record.result = {
                "status": "accepted",
                "summary": f"Subagent {record.agent_id} ({record.role}) completed goal: {record.goal}",
                "patch": "",
            }
            record.updated_at = _now_iso()

    def send_message(self, target_agent_id: str, message: Any, sender_id: str = "coordinator") -> bool:
        """Post a message to a subagent's mailbox."""
        msg_id = f"msg-{uuid.uuid4().hex[:8]}"
        msg = MailboxMessage(
            id=msg_id,
            sender_id=sender_id,
            target_id=target_agent_id,
            content=message,
        )
        with self._lock:
            if target_agent_id not in self._mailboxes and target_agent_id != "coordinator":
                return False
            if target_agent_id not in self._mailboxes:
                self._mailboxes[target_agent_id] = []
            self._mailboxes[target_agent_id].append(msg)
            return True

    def receive_messages(self, agent_id: str, clear: bool = True) -> list[dict[str, Any]]:
        """Retrieve and optionally drain messages for the specified agent."""
        with self._lock:
            queue_list = self._mailboxes.get(agent_id, [])
            if clear:
                self._mailboxes[agent_id] = []
            return [m.as_dict() for m in queue_list]

    def report(self, agent_id: str, status: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Record an intermediate status report from an agent."""
        rep_id = f"rep-{uuid.uuid4().hex[:8]}"
        rep = SubagentReport(
            report_id=rep_id,
            agent_id=agent_id,
            status=status,
            payload=payload or {},
        )
        with self._lock:
            if agent_id in self._reports:
                self._reports[agent_id].append(rep)
            else:
                self._reports[agent_id] = [rep]

            agent = self._agents.get(agent_id)
            if agent is not None:
                agent.updated_at = _now_iso()
        return rep.as_dict()

    def get_reports(self, agent_id: str | None = None) -> list[dict[str, Any]]:
        """Retrieve reports for a specific agent or across all agents."""
        with self._lock:
            if agent_id is not None:
                return [r.as_dict() for r in self._reports.get(agent_id, [])]
            all_reports = []
            for reports in self._reports.values():
                all_reports.extend([r.as_dict() for r in reports])
            return all_reports

    def get_subagent_status(self, agent_id: str) -> dict[str, Any]:
        """Query the current lifecycle state and results of a subagent."""
        with self._lock:
            record = self._agents.get(agent_id)
            if record is None:
                return {
                    "status": "unknown",
                    "agent_id": agent_id,
                    "error": {"kind": "unknown_subagent", "message": f"no subagent found with id {agent_id!r}"},
                }
            snapshot: dict[str, Any] = {
                "agent_id": record.agent_id,
                "role": record.role,
                "goal": record.goal,
                "profile": record.profile,
                "status": record.status,
                "allowed_tools": list(record.allowed_tools),
                "created_at": record.created_at,
                "updated_at": record.updated_at,
                "parent_id": record.parent_id,
                "pending_messages_count": len(self._mailboxes.get(agent_id, [])),
                "reports_count": len(self._reports.get(agent_id, [])),
            }
            if record.result is not None:
                snapshot["result"] = copy.deepcopy(record.result)
            if record.error is not None:
                snapshot["error"] = copy.deepcopy(record.error)
            return snapshot

    def cancel_subagent(self, agent_id: str) -> bool:
        """Cancel a running or pending subagent."""
        with self._lock:
            record = self._agents.get(agent_id)
            if record is None:
                return False
            record.cancel_event.set()
            record.pause_event.set()  # Unpause so it can exit
            if record.status in ("pending", "paused"):
                record.status = "cancelled"
                record.updated_at = _now_iso()
                record.completed_event.set()
            return True

    def pause_subagent(self, agent_id: str) -> bool:
        """Pause a running subagent."""
        with self._lock:
            record = self._agents.get(agent_id)
            if record is None or record.status not in ("running", "pending"):
                return False
            record.pause_event.clear()
            record.status = "paused"
            record.updated_at = _now_iso()
            return True

    def resume_subagent(self, agent_id: str) -> bool:
        """Resume a paused subagent."""
        with self._lock:
            record = self._agents.get(agent_id)
            if record is None or record.status != "paused":
                return False
            record.pause_event.set()
            record.status = "running"
            record.updated_at = _now_iso()
            return True

    def wait_subagent(self, agent_id: str, timeout: float | None = None) -> dict[str, Any]:
        """Wait for a subagent to finish, with optional timeout."""
        with self._lock:
            record = self._agents.get(agent_id)
            if record is None:
                return self.get_subagent_status(agent_id)
            evt = record.completed_event

        evt.wait(timeout=timeout)
        return self.get_subagent_status(agent_id)

    def wait_all(self, timeout: float | None = None) -> dict[str, dict[str, Any]]:
        """Wait for all active subagents to finish within the specified timeout."""
        with self._lock:
            records = list(self._agents.values())

        deadline = (time.monotonic() + timeout) if timeout is not None else None
        for r in records:
            rem = max(0.0, deadline - time.monotonic()) if deadline is not None else None
            r.completed_event.wait(timeout=rem)

        with self._lock:
            return {r.agent_id: self.get_subagent_status(r.agent_id) for r in self._agents.values()}

    def list_subagents(self) -> list[dict[str, Any]]:
        """List all subagents registered with this coordinator."""
        with self._lock:
            return [self.get_subagent_status(aid) for aid in self._agents]

    def shutdown(self, timeout: float = 5.0) -> None:
        """Cancel all subagents and wait for threads to join."""
        with self._lock:
            self._stopping = True
            for r in self._agents.values():
                r.cancel_event.set()
                r.pause_event.set()

        deadline = time.monotonic() + timeout
        for r in list(self._agents.values()):
            if r.thread is not None and r.thread.is_alive():
                rem = max(0.0, deadline - time.monotonic())
                r.thread.join(timeout=rem)
