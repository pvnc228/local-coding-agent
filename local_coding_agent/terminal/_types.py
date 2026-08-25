"""Exceptions and data models for the terminal package."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# ============================================================================
# Exceptions
# ============================================================================

class TerminalError(RuntimeError):
    """Base exception for terminal failures."""


class TerminalSessionNotFoundError(TerminalError):
    """Raised when a requested terminal session does not exist."""


class TerminalSessionExistsError(TerminalError):
    """Raised when creating a session with an ID that is already active."""


class TerminalProcessExitedError(TerminalError):
    """Raised when attempting to interact with a terminal process that has exited."""


class TerminalTimeoutError(TerminalError):
    """Raised when a terminal operation times out."""


# ============================================================================
# Data Models
# ============================================================================

@dataclass(frozen=True)
class TerminalSessionInfo:
    """Snapshot metadata describing a persistent terminal session."""

    session_id: str
    pid: int
    alive: bool
    exit_code: int | None
    cwd: str
    shell: str
    buffer_size: int
    created_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "pid": self.pid,
            "alive": self.alive,
            "exit_code": self.exit_code,
            "cwd": self.cwd,
            "shell": self.shell,
            "buffer_size": self.buffer_size,
            "created_at": self.created_at,
        }
