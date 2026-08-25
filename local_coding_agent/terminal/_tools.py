"""Model-facing terminal tool schemas and handlers."""

from __future__ import annotations

from typing import Any

from ..terminal._manager import TerminalManager


# ============================================================================
# Model-Facing Tool Schemas and Handlers
# ============================================================================

def get_terminal_tool_schemas() -> list[dict[str, Any]]:
    """Return JSON schemas for the 6 persistent terminal tools."""
    return [
        {
            "name": "terminal_open",
            "description": "Create a persistent interactive terminal session (e.g. bash, cmd, REPL) that survives across tool calls.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Unique identifier for this terminal session (e.g. 'main', 'build', 'gdb').",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Initial working directory. Defaults to workspace root.",
                    },
                    "shell": {
                        "type": "string",
                        "description": "Shell or interactive command to launch (e.g. 'bash', 'powershell', 'python -i'). Defaults to system shell.",
                    },
                },
                "required": ["session_id"],
            },
        },
        {
            "name": "terminal_send",
            "description": "Send text or commands to a persistent terminal and receive the resulting output delta.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Session ID returned by terminal_open.",
                    },
                    "text": {
                        "type": "string",
                        "description": "Input text or command line to send.",
                    },
                    "wait_ms": {
                        "type": "integer",
                        "description": "Milliseconds to wait for output after sending (default 500).",
                        "default": 500,
                    },
                    "submit": {
                        "type": "boolean",
                        "description": "Whether to append a newline (Enter) after text (default true).",
                        "default": True,
                    },
                },
                "required": ["session_id", "text"],
            },
        },
        {
            "name": "terminal_read",
            "description": "Read a bounded window from a terminal session's retained output buffer without sending input.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Session ID to read from.",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Character offset to start reading from (default 0; negative offsets count from end).",
                        "default": 0,
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of characters to return (default 4096).",
                        "default": 4096,
                    },
                },
                "required": ["session_id"],
            },
        },
        {
            "name": "terminal_signal",
            "description": "Deliver a control signal to the terminal (e.g. SIGINT/Ctrl+C, SIGTERM, SIGKILL).",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Target terminal session ID.",
                    },
                    "signal": {
                        "type": "string",
                        "description": "Signal to deliver ('SIGINT', 'SIGTERM', 'SIGKILL', etc.).",
                        "enum": ["SIGINT", "SIGTERM", "SIGKILL", "CTRL_C", "SIGBREAK"],
                    },
                },
                "required": ["session_id", "signal"],
            },
        },
        {
            "name": "terminal_list",
            "description": "List all active persistent terminal sessions and their current status.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
        {
            "name": "terminal_close",
            "description": "Close a persistent terminal session and terminate its entire child process tree.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Session ID to terminate and close.",
                    },
                },
                "required": ["session_id"],
            },
        },
    ]


def execute_terminal_tool(
    manager: TerminalManager,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Execute a model-facing terminal tool by name with arguments."""
    if name == "terminal_open":
        session_id = arguments.get("session_id")
        if not session_id:
            return {"ok": False, "error": "session_id is required"}
        cwd = arguments.get("cwd")
        shell = arguments.get("shell")
        try:
            session = manager.create_session(session_id, cwd=cwd, shell=shell)
            return {
                "ok": True,
                "session_id": session.session_id,
                "pid": session.pid,
                "cwd": str(session.cwd),
                "shell": session.shell_cmd,
                "status": "running",
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    elif name == "terminal_send":
        session_id = arguments.get("session_id")
        text = arguments.get("text")
        if not session_id or text is None:
            return {"ok": False, "error": "session_id and text are required"}
        wait_ms = int(arguments.get("wait_ms", 500))
        submit = bool(arguments.get("submit", True))
        try:
            output = manager.send_input(session_id, str(text), wait_ms=wait_ms, submit=submit)
            session = manager.get_session(session_id)
            return {
                "ok": True,
                "session_id": session_id,
                "output": output,
                "alive": session.is_alive(),
                "exit_code": session.exit_code,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    elif name == "terminal_read":
        session_id = arguments.get("session_id")
        if not session_id:
            return {"ok": False, "error": "session_id is required"}
        offset = int(arguments.get("offset", 0))
        limit = max(0, int(arguments.get("limit", 4096)))
        try:
            output = manager.read_buffer(session_id, offset=offset, limit=limit)
            session = manager.get_session(session_id)
            return {
                "ok": True,
                "session_id": session_id,
                "output": output,
                "total_buffer_bytes": len(session.buffer.encode("utf-8")),
                "offset": offset,
                "limit": limit,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    elif name == "terminal_signal":
        session_id = arguments.get("session_id")
        sig = arguments.get("signal")
        if not session_id or not sig:
            return {"ok": False, "error": "session_id and signal are required"}
        try:
            delivered = manager.send_signal(session_id, str(sig))
            return {
                "ok": delivered,
                "session_id": session_id,
                "signal": sig,
                "delivered": delivered,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    elif name == "terminal_list":
        try:
            sessions = manager.list_sessions()
            return {"ok": True, "sessions": sessions}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    elif name == "terminal_close":
        session_id = arguments.get("session_id")
        if not session_id:
            return {"ok": False, "error": "session_id is required"}
        try:
            manager.close_session(session_id)
            return {"ok": True, "session_id": session_id, "closed": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    return {"ok": False, "error": f"Unknown tool: {name}"}
