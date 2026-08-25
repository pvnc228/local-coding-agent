"""Registry and lifecycle manager for persistent terminal sessions."""

from __future__ import annotations

from pathlib import Path
import threading
from typing import Any, Mapping, Sequence

from ..terminal._session import TerminalSession
from ..terminal._types import (
    TerminalError,
    TerminalSessionExistsError,
    TerminalSessionNotFoundError,
)


# ============================================================================
# TerminalManager
# ============================================================================

class TerminalManager:
    """Registry and lifecycle manager for multiple persistent terminal sessions."""

    def __init__(
        self,
        workspace_root: str | Path | None = None,
        *,
        default_max_buffer: int = 1_048_576,
        strict_workspace: bool = False,
    ) -> None:
        self.workspace_root = (
            Path(workspace_root).resolve() if workspace_root else Path.cwd()
        )
        self.default_max_buffer = default_max_buffer
        self.strict_workspace = strict_workspace
        self._sessions: dict[str, TerminalSession] = {}
        self._lock = threading.Lock()

    def create_session(
        self,
        session_id: str,
        cwd: str | Path | None = None,
        shell: str | Sequence[str] | None = None,
        *,
        max_buffer_bytes: int | None = None,
        env: Mapping[str, str] | None = None,
    ) -> TerminalSession:
        """Create and start a new interactive persistent terminal session."""
        session_id = str(session_id).strip()
        if not session_id:
            raise TerminalError("session_id must be a non-empty string")

        if cwd is not None:
            raw_cwd = Path(cwd)
            if raw_cwd.is_absolute():
                target_cwd = raw_cwd.resolve()
            else:
                target_cwd = (self.workspace_root / raw_cwd).resolve()
        else:
            target_cwd = self.workspace_root

        if self.strict_workspace:
            try:
                target_cwd.relative_to(self.workspace_root)
            except ValueError as e:
                raise TerminalError(
                    f"Path traversal denied: working directory '{target_cwd}' is outside workspace root '{self.workspace_root}'"
                ) from e

        with self._lock:
            existing = self._sessions.get(session_id)
            if existing is not None:
                if existing.is_alive():
                    raise TerminalSessionExistsError(
                        f"Terminal session '{session_id}' already exists and is active"
                    )
                # Cleanup dead session before replacing
                existing.close()
                del self._sessions[session_id]

            buf_limit = max_buffer_bytes or self.default_max_buffer

            session = TerminalSession(
                session_id=session_id,
                cwd=target_cwd,
                shell=shell,
                max_buffer_bytes=buf_limit,
                env=env,
            )
            self._sessions[session_id] = session
            return session

    def get_session(self, session_id: str) -> TerminalSession:
        """Retrieve an existing terminal session by ID."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise TerminalSessionNotFoundError(
                    f"Terminal session '{session_id}' not found"
                )
            return session

    def send_input(
        self,
        session_id: str,
        text: str,
        wait_ms: int = 500,
        submit: bool = True,
    ) -> str:
        """Send input to an identified session and collect response output slice."""
        session = self.get_session(session_id)
        return session.send_input(text, wait_ms=wait_ms, submit=submit)

    def read_buffer(
        self,
        session_id: str,
        offset: int = 0,
        limit: int = 4096,
    ) -> str:
        """Read a slice from the session's retained output buffer."""
        session = self.get_session(session_id)
        return session.read_buffer(offset=offset, limit=limit)

    def send_signal(self, session_id: str, sig: str) -> bool:
        """Deliver a signal to the identified session."""
        session = self.get_session(session_id)
        return session.send_signal(sig)

    def list_sessions(self) -> list[dict[str, Any]]:
        """List summary info for all tracked terminal sessions."""
        with self._lock:
            return [s.snapshot().to_dict() for s in self._sessions.values()]

    def close_session(self, session_id: str) -> None:
        """Shut down and unregister a terminal session."""
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is not None:
            session.close()

    def close_all(self) -> None:
        """Shut down and clean up all active terminal sessions."""
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for s in sessions:
            try:
                s.close()
            except Exception:
                pass

    def __enter__(self) -> TerminalManager:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close_all()
