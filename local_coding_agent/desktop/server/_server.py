"""Desktop Harness embedded HTTP server with persistent storage and process orchestration."""

from __future__ import annotations

import ipaddress
import json
import os
import secrets
import subprocess
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from ...stats import DelegationStats
from ...task import TaskEnvelope
from ._handlers import (
    DesktopRequestHandler,
    build_queue_controller,
    detect_relevant_files,
    detect_test_checks,
)

# How often the background task worker polls the store for queued records.
_TASK_POLL_INTERVAL = 0.3


class DesktopServer:
    """Desktop Harness embedded HTTP server with persistent storage and process orchestration."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 0,
        workspace: str | Path = ".",
        default_profile: str = "qwen2.5-coder",
        stats: DelegationStats | None = None,
    ) -> None:
        self.host = host
        self.port = port
        # Hard security boundary: the mutation token is injected into the served
        # HTML, so a non-loopback bind would hand it to every LAN peer.
        self._require_loopback(host)
        self.workspace = str(Path(workspace).resolve())
        self.default_profile = default_profile
        self.mutation_token = secrets.token_urlsafe(32)
        self.stats = stats or DelegationStats()
        self.started_at = time.monotonic()
        self.spawned_processes: dict[str, subprocess.Popen[Any]] = {}
        # ponytail: single-slot "last apply" tracking — a second apply overwrites
        # the first, so rollback undoes only the most recent apply, not a full
        # undo stack.
        self.last_applied_files: list[str] = []
        self.last_applied_patch: str = ""
        self.apply_lock = threading.Lock()
        # ponytail: monotonically-increasing counter drives the hybrid classifier's
        # periodic re-evaluation cadence (classify_mode counter % n_every == 0).
        self.hybrid_counter = 0
        # Context window used when spawning llama-server (-c). Overridable per
        # chat/model-load request; the running server must be relaunched to
        # apply a change, so remember what is currently loaded.
        self.llama_num_ctx = 8192
        self.llama_gguf_path: str | None = None
        self.llama_gguf_label: str | None = None
        # Read back from llama-server /props after launch: what the server
        # actually serves may be clamped to the model's native context length.
        self.llama_effective_ctx: int | None = None
        self.sessions_file = Path(self.workspace) / ".local_agent_sessions.json"
        self.sessions_lock = threading.Lock()
        # Background task queue (R23): persisted proposal-only delegation jobs.
        self.tasks_file = Path(self.workspace) / ".local_agent_tasks.json"
        self.task_queue_lock = threading.Lock()
        self._task_events: dict[str, threading.Event] = {}
        self._task_stop = threading.Event()
        self._task_thread: threading.Thread | None = None
        # Test seam: when set, the worker builds its controller through
        # controller_factory(profile, workspace, cancel_event=...) -> object
        # with .run(task). When None it mirrors /api/delegate's construction.
        self.controller_factory: Any = None
        self._recover_interrupted_tasks()
        self._httpd = ThreadingHTTPServer((host, port), DesktopRequestHandler)
        self._httpd.desktop_server = self  # type: ignore[attr-defined]
        self._thread: threading.Thread | None = None

    @staticmethod
    def _require_loopback(host: str) -> None:
        candidate = "127.0.0.1" if host == "localhost" else host
        try:
            addr = ipaddress.ip_address(candidate)
        except ValueError as error:
            raise ValueError(f"Desktop server host must be a loopback address, got {host!r}") from error
        if not addr.is_loopback:
            raise ValueError(
                f"Desktop server refuses non-loopback bind ({host}); the UI page embeds "
                "the mutation token and must stay local-only"
            )

    @property
    def actual_port(self) -> int:
        return self._httpd.server_address[1]

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.actual_port}"

    def load_sessions(self) -> list[dict[str, Any]]:
        with self.sessions_lock:
            return self._load_sessions_unlocked()

    def _load_sessions_unlocked(self) -> list[dict[str, Any]]:
        if self.sessions_file.exists():
            try:
                data = json.loads(self.sessions_file.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return data
            except Exception:
                pass
        return []

    def save_session(self, session: dict[str, Any]) -> None:
        with self.sessions_lock:
            sessions = self._load_sessions_unlocked()
            sessions = [s for s in sessions if s.get("id") != session.get("id")]
            sessions.insert(0, session)
            try:
                self.sessions_file.write_text(json.dumps(sessions[:50], indent=2, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass

    # ---- Background task queue store (newest first, capped at 100) ----

    def load_tasks(self) -> list[dict[str, Any]]:
        if self.tasks_file.exists():
            try:
                data = json.loads(self.tasks_file.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return data
            except Exception:
                pass
        return []

    def _write_tasks(self, tasks: list[dict[str, Any]]) -> None:
        try:
            self.tasks_file.write_text(json.dumps(tasks[:100], indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def save_task(self, task: dict[str, Any]) -> None:
        tasks = [t for t in self.load_tasks() if t.get("id") != task.get("id")]
        tasks.insert(0, task)
        self._write_tasks(tasks)

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        return next((t for t in self.load_tasks() if t.get("id") == task_id), None)

    def update_task(self, task_id: str, **fields: Any) -> dict[str, Any] | None:
        """Patch a stored record; callers hold ``task_queue_lock``."""
        tasks = self.load_tasks()
        updated: dict[str, Any] | None = None
        for index, record in enumerate(tasks):
            if record.get("id") == task_id:
                record.update(fields)
                tasks[index] = record
                updated = record
                break
        if updated is not None:
            self._write_tasks(tasks)
        return updated

    def _recover_interrupted_tasks(self) -> None:
        """Fail records left 'running' by a previous process.

        Queued tasks stay queued and are picked up on start() (persistence
        gives this for free); a running Controller cannot survive its process,
        so its record fails honestly instead of hanging forever.
        """
        tasks = self.load_tasks()
        stuck = [t for t in tasks if t.get("status") == "running"]
        if not stuck:
            return
        for record in stuck:
            record["status"] = "failed"
            record["finished_at"] = time.time()
            record["error"] = "server restarted while the task was running"
        self._write_tasks(tasks)

    # ---- Background task worker ----

    def _task_worker_loop(self) -> None:
        """Sequential background executor: one queued task at a time.

        # ponytail: sequential queue — concurrent pool only when batch
        throughput measurably matters. Survives any exception by failing the
        record and continuing. Polls every 0.3s; queued tasks persist across
        restarts, so anything left queued is simply picked up next start().
        """
        while not self._task_stop.wait(_TASK_POLL_INTERVAL):
            with self.task_queue_lock:
                queued = [t for t in self.load_tasks() if t.get("status") == "queued"]
                record = dict(queued[-1]) if queued else None  # newest-first -> last is oldest
            if record is None:
                continue
            try:
                self._execute_task(record)
            except Exception as error:  # never let the worker thread die
                with self.task_queue_lock:
                    self.update_task(record["id"], status="failed", finished_at=time.time(), error=str(error))

    def _execute_task(self, record: dict[str, Any]) -> None:
        """Run one delegated task proposal-only and store the outcome."""
        task_id = record["id"]
        cancel_event = threading.Event()
        with self.task_queue_lock:
            current = self.get_task(task_id)
            if current is None or current.get("status") != "queued":
                return  # cancelled between pick-up and execution
            self._task_events[task_id] = cancel_event
            self.update_task(task_id, status="running", started_at=time.time())
        try:
            files = [str(f) for f in (record.get("files") or [])]
            if not files:
                files = detect_relevant_files(self.workspace, record["goal"])
            checks = [str(c) for c in (record.get("checks") or [])]
            if not checks:
                checks = detect_test_checks(self.workspace)
            envelope = TaskEnvelope(
                id=task_id,
                goal=record["goal"],
                files=tuple(files),
                checks=tuple(checks),
            )
            factory = self.controller_factory or build_queue_controller
            controller = factory(
                record.get("profile") or self.default_profile,
                self.workspace,
                cancel_event=cancel_event,
            )
            result = controller.run(envelope) or {}
            if cancel_event.is_set():
                final_status = "cancelled"
            elif result.get("status") == "accepted":
                final_status = "accepted"
            else:
                final_status = "failed"
            error = result.get("error") or ""
            if final_status == "failed" and not error:
                error = str(result.get("summary") or f"task ended with status {result.get('status')}")
            with self.task_queue_lock:
                self.update_task(
                    task_id,
                    status=final_status,
                    finished_at=time.time(),
                    files=files,
                    summary=str(result.get("summary") or ""),
                    patch=str(result.get("patch") or ""),
                    checks_results=result.get("checks") if isinstance(result.get("checks"), list) else [],
                    error=error,
                )
        except Exception as error:
            status = "cancelled" if cancel_event.is_set() else "failed"
            with self.task_queue_lock:
                self.update_task(task_id, status=status, finished_at=time.time(), error=str(error))
        finally:
            with self.task_queue_lock:
                self._task_events.pop(task_id, None)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name="local-agent-desktop-http",
            daemon=True,
        )
        self._thread.start()
        self._task_stop.clear()
        self._task_thread = threading.Thread(
            target=self._task_worker_loop,
            name="local-agent-desktop-tasks",
            daemon=True,
        )
        self._task_thread.start()

    def stop(self) -> None:
        # Wake/join the task worker briefly: a running Controller finishes in
        # its daemon thread (never killed mid-write); queued tasks remain
        # queued on restart via the persisted store.
        self._task_stop.set()
        if self._task_thread is not None:
            self._task_thread.join(timeout=2.0)
            self._task_thread = None

        for name, proc in list(self.spawned_processes.items()):
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, check=False)
                else:
                    proc.terminate()
                    proc.wait(timeout=2.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self.spawned_processes.clear()

        if self._thread is None:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=3.0)
        self._thread = None

    def __enter__(self) -> DesktopServer:
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.stop()
