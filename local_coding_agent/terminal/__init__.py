"""Persistent PTY Terminal Seam & Interactive Process Control (R26).

Adapted from DeepSeek Harness @deepseek-ai/dsh-terminal and @deepseek-ai/dsh-tool-terminal.
Provides persistent background interactive shell / process sessions, non-blocking I/O drainers,
circular scrollback buffers, cross-platform signal propagation, and robust process tree cleanup.
"""

from __future__ import annotations

from ..terminal._process import kill_process_tree
from ..terminal._session import TerminalSession
from ..terminal._types import (
    TerminalError,
    TerminalProcessExitedError,
    TerminalSessionExistsError,
    TerminalSessionInfo,
    TerminalSessionNotFoundError,
    TerminalTimeoutError,
)
from ..terminal._manager import TerminalManager
from ..terminal._tools import execute_terminal_tool, get_terminal_tool_schemas

__all__ = [
    "TerminalError",
    "TerminalManager",
    "TerminalProcessExitedError",
    "TerminalSession",
    "TerminalSessionExistsError",
    "TerminalSessionInfo",
    "TerminalSessionNotFoundError",
    "TerminalTimeoutError",
    "execute_terminal_tool",
    "get_terminal_tool_schemas",
    "kill_process_tree",
]
