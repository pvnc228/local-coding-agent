"""Process tree termination helpers."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import shlex
import signal
import subprocess


# ============================================================================
# Process Tree Termination Helpers
# ============================================================================

def _windows_descendants(root_pid: int) -> tuple[list[int], str | None]:
    if os.name != "nt":
        return [root_pid], None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    invalid_handle = ctypes.c_void_p(-1).value
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot == invalid_handle:
        return [], f"process snapshot failed: {ctypes.get_last_error()}"
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(ProcessEntry)
        parents: dict[int, int] = {}
        if kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            while True:
                parents[entry.th32ProcessID] = entry.th32ParentProcessID
                if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    break
        children: dict[int, list[int]] = {}
        for pid, parent in parents.items():
            children.setdefault(parent, []).append(pid)
        descendants: list[int] = []
        pending = list(children.get(root_pid, []))
        while pending:
            pid = pending.pop()
            descendants.append(pid)
            pending.extend(children.get(pid, []))
        return descendants, None
    finally:
        kernel32.CloseHandle(snapshot)


def _terminate_windows_pid(pid: int) -> tuple[bool, str | None]:
    if os.name != "nt":
        return True, None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(0x0001, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        if error in {2, 3, 87, 1168}:
            return True, None
        return False, f"OpenProcess({pid}) failed: {error}"
    try:
        if kernel32.TerminateProcess(handle, 1):
            return True, None
        return False, f"TerminateProcess({pid}) failed: {ctypes.get_last_error()}"
    finally:
        kernel32.CloseHandle(handle)


def kill_process_tree(pid: int, timeout: float = 3.0) -> tuple[bool, str | None]:
    """Force-terminate a process and all its descendants cross-platform."""
    if pid <= 0 or (os.name != "nt" and pid == os.getpid()):
        return True, None

    if os.name == "nt":
        if pid == os.getpid():
            return True, None
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        taskkill = str(Path(system_root) / "System32" / "taskkill.exe")
        if not Path(taskkill).is_file():
            taskkill = "taskkill"
        try:
            completed = subprocess.run(
                [taskkill, "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=timeout,
            )
            if completed.returncode == 0:
                return True, None
        except (OSError, subprocess.TimeoutExpired):
            pass

        tree, _ = _windows_descendants(pid)
        # Kill descendants first (bottom-up), then root pid
        all_pids = list(dict.fromkeys([*tree, pid]))
        for p in all_pids:
            if p != os.getpid():
                _terminate_windows_pid(p)
        return True, None
    else:
        try:
            my_pgid = os.getpgid(0)
            pgid = os.getpgid(pid)
            if pgid != my_pgid and pgid > 1:
                os.killpg(pgid, signal.SIGKILL)
            else:
                os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass
        return True, None


def _parse_command(cmd_str: str) -> list[str]:
    if os.name == "nt":
        try:
            tokens = shlex.split(cmd_str, posix=False)
            cleaned: list[str] = []
            for token in tokens:
                if len(token) >= 2 and (
                    (token.startswith('"') and token.endswith('"'))
                    or (token.startswith("'") and token.endswith("'"))
                ):
                    token = token[1:-1]
                cleaned.append(token)
            return cleaned or cmd_str.split()
        except Exception:
            return cmd_str.split()
    try:
        return shlex.split(cmd_str, posix=True)
    except Exception:
        return cmd_str.split()
