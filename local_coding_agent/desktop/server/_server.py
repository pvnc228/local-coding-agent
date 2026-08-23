"""Desktop Harness embedded HTTP server with persistent storage and process orchestration."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from ...stats import DelegationStats
from ._handlers import DesktopRequestHandler


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
        self.workspace = str(Path(workspace).resolve())
        self.default_profile = default_profile
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
        self.sessions_file = Path(self.workspace) / ".local_agent_sessions.json"
        self._httpd = ThreadingHTTPServer((host, port), DesktopRequestHandler)
        self._httpd.desktop_server = self  # type: ignore[attr-defined]
        self._thread: threading.Thread | None = None

    @property
    def actual_port(self) -> int:
        return self._httpd.server_address[1]

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.actual_port}"

    def load_sessions(self) -> list[dict[str, Any]]:
        if self.sessions_file.exists():
            try:
                data = json.loads(self.sessions_file.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return data
            except Exception:
                pass
        return []

    def save_session(self, session: dict[str, Any]) -> None:
        sessions = self.load_sessions()
        sessions = [s for s in sessions if s.get("id") != session.get("id")]
        sessions.insert(0, session)
        try:
            self.sessions_file.write_text(json.dumps(sessions[:50], indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name="local-agent-desktop-http",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
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
