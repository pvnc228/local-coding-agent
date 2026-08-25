"""Tests for Persistent PTY Terminal Seam & Interactive Process Control (R26)."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import time
import pytest

from local_coding_agent.terminal import (
    TerminalError,
    TerminalManager,
    TerminalProcessExitedError,
    TerminalSession,
    TerminalSessionExistsError,
    TerminalSessionInfo,
    TerminalSessionNotFoundError,
    TerminalTimeoutError,
    execute_terminal_tool,
    get_terminal_tool_schemas,
    kill_process_tree,
)


# ============================================================================
# 1. TerminalSession Direct Interactive Tests
# ============================================================================

def test_terminal_session_python_repl(tmp_path: Path) -> None:
    """Test interactive input and output exchange using an interactive Python REPL."""
    cmd = [sys.executable, "-u", "-q", "-i"]
    session = TerminalSession("repl-1", cwd=tmp_path, shell=cmd)

    try:
        assert session.is_alive()
        assert session.pid > 0
        assert session.session_id == "repl-1"

        # Send a calculation
        out1 = session.send_input("print(12345 + 67890)", wait_ms=600)
        assert "80235" in out1

        # Send another variable definition and access
        session.send_input("x = 'ANTIGRAVITY_TERMINAL'", wait_ms=300)
        out2 = session.send_input("print(x.lower())", wait_ms=600)
        assert "antigravity_terminal" in out2

        # Read buffer slice
        full_buf = session.read_buffer(0, 10000)
        assert "80235" in full_buf
        assert "antigravity_terminal" in full_buf

        # Negative offset (last 50 chars)
        tail = session.read_buffer(-50, 50)
        assert len(tail) <= 50

        # Snapshot check
        info = session.snapshot()
        assert isinstance(info, TerminalSessionInfo)
        assert info.session_id == "repl-1"
        assert info.alive is True
        assert info.pid == session.pid
        assert info.buffer_size > 0
    finally:
        session.close()
        assert not session.is_alive()


def test_terminal_session_default_shell(tmp_path: Path) -> None:
    """Test session creation with the default system shell (cmd.exe or bash/sh)."""
    session = TerminalSession("shell-default", cwd=tmp_path)
    try:
        assert session.is_alive()
        if os.name == "nt":
            out = session.send_input("echo HELLO_SHELL", wait_ms=600)
            assert "HELLO_SHELL" in out
        else:
            out = session.send_input("echo HELLO_SHELL", wait_ms=600)
            assert "HELLO_SHELL" in out
    finally:
        session.close()


def test_terminal_session_exited_process(tmp_path: Path) -> None:
    """Test that sending input to an exited process raises TerminalProcessExitedError."""
    # A script that exits immediately
    cmd = [sys.executable, "-c", "print('done'); sys.exit(0)"]
    session = TerminalSession("quick-exit", cwd=tmp_path, shell=cmd)

    # Wait for child to exit
    time.sleep(0.5)
    assert not session.is_alive()

    with pytest.raises(TerminalProcessExitedError, match="has exited"):
        session.send_input("print('too late')", wait_ms=100)

    session.close()


def test_terminal_session_buffer_bounds(tmp_path: Path) -> None:
    """Test that the internal output buffer respects max_buffer_bytes capacity."""
    cmd = [sys.executable, "-u", "-q", "-i"]
    session = TerminalSession("buf-limit", cwd=tmp_path, shell=cmd, max_buffer_bytes=200)

    try:
        # Output lots of characters
        session.send_input("print('A' * 500)", wait_ms=600)
        # Buffer should be bounded
        assert len(session.buffer) <= 200
        # Offset beyond buffer length returns empty string
        assert session.read_buffer(9999, 100) == ""
    finally:
        session.close()


def test_terminal_session_signals(tmp_path: Path) -> None:
    """Test signal delivery (SIGINT/Ctrl+C, SIGTERM, SIGKILL)."""
    # A script that runs an infinite loop
    script = (
        "import time, sys\n"
        "try:\n"
        "    while True:\n"
        "        time.sleep(0.1)\n"
        "except KeyboardInterrupt:\n"
        "    print('CAUGHT_CTRL_C', flush=True)\n"
        "    sys.exit(0)\n"
    )
    test_script = tmp_path / "sig_test.py"
    test_script.write_text(script, encoding="utf-8")

    cmd = [sys.executable, "-u", str(test_script)]
    session = TerminalSession("sig-session", cwd=tmp_path, shell=cmd)

    try:
        assert session.is_alive()
        time.sleep(0.2)

        # Send SIGINT / Ctrl+C
        delivered = session.send_signal("SIGINT")
        assert delivered is True

        # Wait a moment for process to handle interrupt or terminate
        time.sleep(0.5)

        # If not exited by SIGINT, test SIGTERM / SIGKILL
        if session.is_alive():
            session.send_signal("SIGTERM")
            time.sleep(0.3)
        if session.is_alive():
            session.send_signal("SIGKILL")
            time.sleep(0.3)

        assert not session.is_alive()
    finally:
        session.close()


def test_terminal_session_invalid_cwd(tmp_path: Path) -> None:
    """Test that initializing a session with non-existent directory raises TerminalError."""
    invalid_dir = tmp_path / "non_existent_folder_xyz"
    with pytest.raises(TerminalError, match="Working directory does not exist"):
        TerminalSession("bad-cwd", cwd=invalid_dir)


# ============================================================================
# 2. TerminalManager Tests
# ============================================================================

def test_terminal_manager_crud_and_lifecycle(tmp_path: Path) -> None:
    """Test full TerminalManager lifecycle: create, list, input, read, signal, close."""
    with TerminalManager(workspace_root=tmp_path) as mgr:
        # Create session 1
        cmd = [sys.executable, "-u", "-q", "-i"]
        s1 = mgr.create_session("s1", shell=cmd)
        assert s1.session_id == "s1"
        assert s1.is_alive()

        # Cannot create duplicate active session
        with pytest.raises(TerminalSessionExistsError, match="already exists"):
            mgr.create_session("s1", shell=cmd)

        # Create session 2
        s2 = mgr.create_session("s2", shell=cmd)
        assert s2.session_id == "s2"

        # List sessions
        sessions = mgr.list_sessions()
        assert len(sessions) == 2
        session_ids = {s["session_id"] for s in sessions}
        assert session_ids == {"s1", "s2"}

        # Send input through manager
        out = mgr.send_input("s1", "print(99 * 88)", wait_ms=600)
        assert "8712" in out

        # Read buffer through manager
        buf = mgr.read_buffer("s1", offset=0, limit=2048)
        assert "8712" in buf

        # Close s1
        mgr.close_session("s1")
        assert len(mgr.list_sessions()) == 1

        # Query closed session raises not found
        with pytest.raises(TerminalSessionNotFoundError, match="not found"):
            mgr.get_session("s1")

        with pytest.raises(TerminalSessionNotFoundError, match="not found"):
            mgr.send_input("s1", "test")

        with pytest.raises(TerminalSessionNotFoundError, match="not found"):
            mgr.read_buffer("s1")

        with pytest.raises(TerminalSessionNotFoundError, match="not found"):
            mgr.send_signal("s1", "SIGINT")

        # Replacing a closed/dead session succeeds
        s1_new = mgr.create_session("s1", shell=cmd)
        assert s1_new.is_alive()

    # Outside with-block, close_all was called
    assert len(mgr.list_sessions()) == 0


def test_terminal_manager_empty_session_id(tmp_path: Path) -> None:
    """Test that empty session ID is rejected."""
    mgr = TerminalManager(workspace_root=tmp_path)
    with pytest.raises(TerminalError, match="non-empty string"):
        mgr.create_session("")


# ============================================================================
# 3. Model-Facing Tool Schemas and Dispatcher Tests
# ============================================================================

def test_terminal_tool_schemas() -> None:
    """Test that tool schemas conform to the expected format."""
    schemas = get_terminal_tool_schemas()
    assert len(schemas) == 6
    names = {s["name"] for s in schemas}
    assert names == {
        "terminal_open",
        "terminal_send",
        "terminal_read",
        "terminal_signal",
        "terminal_list",
        "terminal_close",
    }
    for s in schemas:
        assert "name" in s
        assert "description" in s
        assert "parameters" in s


def test_execute_terminal_tools(tmp_path: Path) -> None:
    """Test execute_terminal_tool dispatcher with all 6 tools."""
    with TerminalManager(workspace_root=tmp_path) as mgr:
        # 1. terminal_open
        res_open = execute_terminal_tool(
            mgr,
            "terminal_open",
            {
                "session_id": "tool-session",
                "cwd": str(tmp_path),
                "shell": f'"{sys.executable}" -u -q -i',
            },
        )
        assert res_open["ok"] is True
        assert res_open["session_id"] == "tool-session"
        assert res_open["pid"] > 0
        assert res_open["status"] == "running"

        # terminal_open missing session_id
        err_open = execute_terminal_tool(mgr, "terminal_open", {})
        assert err_open["ok"] is False

        # 2. terminal_list
        res_list = execute_terminal_tool(mgr, "terminal_list", {})
        assert res_list["ok"] is True
        assert len(res_list["sessions"]) == 1

        # 3. terminal_send
        res_send = execute_terminal_tool(
            mgr,
            "terminal_send",
            {"session_id": "tool-session", "text": "print('AGENT_TOOL_TEST')", "wait_ms": 600},
        )
        assert res_send["ok"] is True
        assert "AGENT_TOOL_TEST" in res_send["output"]
        assert res_send["alive"] is True

        # terminal_send missing parameters
        err_send = execute_terminal_tool(mgr, "terminal_send", {"session_id": "tool-session"})
        assert err_send["ok"] is False

        # 4. terminal_read
        res_read = execute_terminal_tool(
            mgr,
            "terminal_read",
            {"session_id": "tool-session", "offset": 0, "limit": 1000},
        )
        assert res_read["ok"] is True
        assert "AGENT_TOOL_TEST" in res_read["output"]
        assert res_read["total_buffer_bytes"] > 0

        # terminal_read missing session_id
        err_read = execute_terminal_tool(mgr, "terminal_read", {})
        assert err_read["ok"] is False

        # 5. terminal_signal
        res_sig = execute_terminal_tool(
            mgr,
            "terminal_signal",
            {"session_id": "tool-session", "signal": "SIGINT"},
        )
        assert res_sig["ok"] is True
        assert res_sig["delivered"] is True

        # terminal_signal missing signal
        err_sig = execute_terminal_tool(mgr, "terminal_signal", {"session_id": "tool-session"})
        assert err_sig["ok"] is False

        # 6. terminal_close
        res_close = execute_terminal_tool(
            mgr,
            "terminal_close",
            {"session_id": "tool-session"},
        )
        assert res_close["ok"] is True
        assert res_close["closed"] is True

        # terminal_close missing session_id
        err_close = execute_terminal_tool(mgr, "terminal_close", {})
        assert err_close["ok"] is False

        # Unknown tool
        res_unk = execute_terminal_tool(mgr, "unknown_tool", {})
        assert res_unk["ok"] is False
        assert "Unknown tool" in res_unk["error"]


# ============================================================================
# 4. Process Tree Termination Tests
# ============================================================================

def test_kill_process_tree(tmp_path: Path) -> None:
    """Test that kill_process_tree terminates a parent and all child/grandchild processes."""
    child_script = (
        "import subprocess, sys, time\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        "p.wait()\n"
    )
    script_path = tmp_path / "parent_proc.py"
    script_path.write_text(child_script, encoding="utf-8")

    proc = subprocess.Popen([sys.executable, str(script_path)])
    time.sleep(0.5)

    assert proc.poll() is None
    success, detail = kill_process_tree(proc.pid, timeout=2.0)
    assert success is True

    # Process should be reaped
    time.sleep(0.2)
    proc.poll()


# ============================================================================
# 5. Adversarial & Edge-Case Reliability Tests
# ============================================================================

def test_terminal_buffer_saturation_and_delta_extraction(tmp_path: Path) -> None:
    """Adversarial test: verify send_input returns correct delta even when buffer is saturated."""
    cmd = [sys.executable, "-u", "-q", "-i"]
    session = TerminalSession("sat-session", cwd=tmp_path, shell=cmd, max_buffer_bytes=120)

    try:
        # Fill buffer to capacity (300 chars > 120 limit)
        out1 = session.send_input("print('X' * 300)", wait_ms=600)
        assert len(session.buffer) <= 120

        # Subsequent command when buffer is already 100% full
        out2 = session.send_input("print('DELTA_SECRET_KEY')", wait_ms=600)
        assert "DELTA_SECRET_KEY" in out2
        assert len(session.buffer) <= 120
    finally:
        session.close()


def test_terminal_pipe_drainer_burst_no_deadlock(tmp_path: Path) -> None:
    """Adversarial test: verify that massive stdout bursts (>500KB) do not deadlock OS pipe."""
    cmd = [sys.executable, "-u", "-q", "-i"]
    session = TerminalSession("burst-session", cwd=tmp_path, shell=cmd, max_buffer_bytes=4096)

    try:
        # Emit 200,000 characters in one command
        out = session.send_input("print('BURST_START' + 'A' * 200000 + 'BURST_END')", wait_ms=1000)
        # Should finish without pipe deadlock
        assert len(session.buffer) <= 4096
        # Retained buffer should contain latest part of burst
        assert "BURST_END" in session.buffer
    finally:
        session.close()


def test_terminal_manager_relative_cwd_and_strict_workspace(tmp_path: Path) -> None:
    """Adversarial test: verify relative cwd is anchored to workspace_root and strict_workspace prevents traversal."""
    sub_dir = tmp_path / "subproject"
    sub_dir.mkdir()

    # 1. Standard manager anchors relative cwd to workspace_root
    mgr = TerminalManager(workspace_root=tmp_path)
    cmd = [sys.executable, "-u", "-q", "-i"]
    s1 = mgr.create_session("s-rel", cwd="subproject", shell=cmd)
    assert s1.cwd == sub_dir.resolve()
    s1.close()

    # 2. Strict manager rejects path traversal outside workspace
    strict_mgr = TerminalManager(workspace_root=sub_dir, strict_workspace=True)
    with pytest.raises(TerminalError, match="Path traversal denied"):
        strict_mgr.create_session("s-escape", cwd="../", shell=cmd)


def test_terminal_concurrent_send_input(tmp_path: Path) -> None:
    """Adversarial test: verify thread-safety when multiple threads interact with the same session."""
    import threading

    cmd = [sys.executable, "-u", "-q", "-i"]
    session = TerminalSession("concurrent-session", cwd=tmp_path, shell=cmd)

    results: list[str] = []
    errors: list[Exception] = []

    def worker(num: int) -> None:
        try:
            res = session.send_input(f"print('THREAD_MAGIC_{num}')", wait_ms=800)
            results.append(res)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    session.close()

    assert not errors
    assert len(results) == 3
    combined = session.buffer
    assert "THREAD_MAGIC_0" in combined
    assert "THREAD_MAGIC_1" in combined
    assert "THREAD_MAGIC_2" in combined


def test_terminal_tool_dispatcher_adversarial_arguments(tmp_path: Path) -> None:
    """Adversarial test: verify tool dispatcher tolerates non-standard / hostile argument types."""
    with TerminalManager(workspace_root=tmp_path) as mgr:
        # Open session
        res_open = execute_terminal_tool(
            mgr,
            "terminal_open",
            {
                "session_id": "edge-session",
                "cwd": str(tmp_path),
                "shell": [sys.executable, "-u", "-q", "-i"],
            },
        )
        assert res_open["ok"] is True

        # Send with non-string text (e.g. numeric integer)
        res_send = execute_terminal_tool(
            mgr,
            "terminal_send",
            {"session_id": "edge-session", "text": 123456, "wait_ms": 400},
        )
        assert res_send["ok"] is True

        # Read with negative limit and out-of-bounds offset
        res_read = execute_terminal_tool(
            mgr,
            "terminal_read",
            {"session_id": "edge-session", "offset": 999999, "limit": -50},
        )
        assert res_read["ok"] is True
        assert res_read["output"] == ""

        # Signal with unsupported / invalid signal name
        res_sig = execute_terminal_tool(
            mgr,
            "terminal_signal",
            {"session_id": "edge-session", "signal": "INVALID_NON_EXISTENT_SIGNAL_XYZ"},
        )
        assert res_sig["ok"] is False or res_sig.get("delivered") is False

        # Guard against killing self/invalid PID
        ok, err = kill_process_tree(os.getpid())
        assert ok is True
        ok, err = kill_process_tree(-999)
        assert ok is True
