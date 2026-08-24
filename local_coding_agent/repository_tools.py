"""Policy-bound repository operations exposed to a local model."""

from __future__ import annotations

import json
import ctypes
from ctypes import wintypes
import math
import os
import re
import subprocess
import time
from pathlib import Path
from threading import Event, Thread
from typing import Any

from .task import TaskEnvelope
from .validators import check_patch_applies, parse_unified_diff, resolve_edits


def _fold_path(path: str) -> str:
    # ponytail: treat only Windows as case-insensitive; macOS default is
    # case-insensitive but this keeps the common Linux case strict.
    return path.casefold() if os.name == "nt" else path


# Name-based secret scrubbing for the isolated test environment.  Name
# matching only: value heuristics produce false positives that cost more
# than they catch.
_SECRET_ENV_NAME_RE = re.compile(
    r"(token|secret|password|passwd|api[_-]?key|private[_-]?key|client[_-]?secret|^aws_|azure_client)",
    re.IGNORECASE,
)


def _posix_preexec(timeout_seconds: int) -> Any:
    """Child setup hook for POSIX spawns; never invoked on Windows.

    Gives the child its own session (reliable ``os.killpg``), asks the kernel
    to SIGKILL it if we die first (PR_SET_PDEATHSIG), and clamps CPU/file
    descriptors.  Every guard degrades silently rather than crashing spawn.
    """

    def apply() -> None:
        os.setsid()
        try:
            import resource

            # prctl(PR_SET_PDEATHSIG, SIGKILL) == prctl(1, 9); literal
            # constants avoid importing signal and musl/alpine quirks must
            # never crash spawn - the wall-clock kill covers the gap.
            libc_path = ctypes.util.find_library("c")
            if libc_path:
                libc = ctypes.CDLL(libc_path, use_errno=True)
                libc.prctl(1, 9, 0, 0, 0)
        except Exception:
            pass
        try:
            import resource

            cpu_limit = timeout_seconds + 30
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit, cpu_limit))
            resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
            # ponytail: RLIMIT_AS deliberately NOT set - AS limits break
            # CPython mmaps on some libcs; CPU limit covers runaway loops,
            # wall-clock kill covers hangs.
        except Exception:
            pass

    return apply


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


class ToolPolicyError(RuntimeError):
    """A tool call rejected by the repository policy."""


class ToolCancelled(RuntimeError):
    """A running command was cancelled."""


class _BoundedPipeCollector:
    """Continuously drain one child pipe while retaining bounded output."""

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._data = bytearray()
        self.truncated = False

    def collect(self, stream: Any) -> None:
        try:
            while True:
                chunk = stream.read(8192)
                if not chunk:
                    return
                if not isinstance(chunk, bytes):
                    chunk = bytes(chunk)
                remaining = self._limit - len(self._data)
                if remaining > 0:
                    self._data.extend(chunk[:remaining])
                if len(chunk) > max(remaining, 0):
                    self.truncated = True
        except (OSError, ValueError):
            # Closing a pipe after bounded process termination is expected.
            return

    def value(self) -> bytes:
        return bytes(self._data)


class BoundedRepositoryTools:
    def __init__(
        self,
        workspace_root: str | Path,
        task: TaskEnvelope,
        *,
        max_tool_result_bytes: int = 32_000,
        max_files: int = 5,
        max_matches: int = 100,
        max_patch_bytes: int = 128_000,
        max_patch_files: int = 2,

        test_timeout_seconds: float = 60,
        cancel_event: Event | None = None,
        blocked_tools: set[str] | None = None,
    ) -> None:
        if max_tool_result_bytes <= 0:
            raise ValueError("max_tool_result_bytes must be positive")
        if (
            max_files <= 0
            or max_matches <= 0
            or max_patch_bytes <= 0
            or max_patch_files <= 0
            or test_timeout_seconds <= 0
        ):
            raise ValueError("all repository tool limits must be positive")
        self.workspace_root = Path(workspace_root).resolve()
        self.task = task
        self.max_tool_result_bytes = max_tool_result_bytes
        self.max_files = max_files
        self.max_matches = max_matches
        self.max_patch_bytes = max_patch_bytes
        self.max_patch_files = max_patch_files
        self.test_timeout_seconds = test_timeout_seconds
        self.cancel_event = cancel_event
        self.blocked_tools = set(blocked_tools or ())
        self._allowlist = {self._normalize_declared_path(path) for path in task.files}
        if len(self._allowlist) > max_files:
            raise ToolPolicyError(f"task exceeds max_files={max_files}")
        self._audit_events: list[dict[str, Any]] = []

    @property
    def audit_events(self) -> tuple[dict[str, Any], ...]:
        return tuple(dict(event) for event in self._audit_events)

    def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name in self.blocked_tools:
            self._record(name, arguments, False, f"tool '{name}' is blocked in read-only mode")
            raise ToolPolicyError(f"tool '{name}' is blocked in read-only mode")
        if name not in {"list_files", "read_file", "search_text", "propose_patch", "run_tests"}:
            self._record(name, arguments, False, "unknown tool")
            raise ToolPolicyError(f"unknown tool: {name}")
        try:
            if name == "list_files":
                result = self._list_files(arguments)
            elif name == "read_file":
                result = self._read_file(arguments)
            elif name == "propose_patch":
                result = self._propose_patch(arguments)
            elif name == "run_tests":
                result = self._run_tests(arguments)
            else:
                result = self._search_text(arguments)
        except ToolPolicyError as error:
            self._record(name, arguments, False, str(error))
            raise
        success = result.get("ok", True) is not False
        error_msg = result.get("error") if not success else None
        self._record(name, arguments, success, error_msg)
        return result

    def _run_tests(self, arguments: dict[str, Any]) -> dict[str, Any]:
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ToolPolicyError("command must be a non-empty string")
        if command not in self.task.checks:
            raise ToolPolicyError("command is not allowlisted")
        process: subprocess.Popen[bytes] = subprocess.Popen(
            command,
            cwd=self.workspace_root,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self._isolated_environment(),
            **self._process_group_options(math.ceil(self.test_timeout_seconds)),
        )
        assert process.stdout is not None
        assert process.stderr is not None
        stdout_collector = _BoundedPipeCollector(self.max_tool_result_bytes)
        stderr_collector = _BoundedPipeCollector(self.max_tool_result_bytes)
        readers = [
            Thread(target=stdout_collector.collect, args=(process.stdout,), daemon=True),
            Thread(target=stderr_collector.collect, args=(process.stderr,), daemon=True),
        ]
        for reader in readers:
            reader.start()
        deadline = time.monotonic() + self.test_timeout_seconds
        timed_out = False
        termination_warning: str | None = None
        finished = False
        try:
            while True:
                if self.cancel_event is not None and self.cancel_event.is_set():
                    termination_warning = self._terminate(process)
                    finished = True
                    raise ToolCancelled("task was cancelled")
                if process.poll() is not None:
                    finished = True
                    break
                if time.monotonic() >= deadline:
                    termination_warning = self._terminate(process)
                    timed_out = True
                    finished = True
                    break
                time.sleep(0.05)
        finally:
            try:
                if not finished and process.poll() is None:
                    termination_warning = self._terminate(process)
            finally:
                for reader in readers:
                    reader.join(timeout=2)
                process.stdout.close()
                process.stderr.close()
                for reader in readers:
                    reader.join(timeout=0.5)
        stdout = stdout_collector.value()
        stderr = stderr_collector.value()
        stdout_truncated = stdout_collector.truncated
        stderr_truncated = stderr_collector.truncated
        truncated = stdout_truncated or stderr_truncated
        if timed_out:
            result = {
                "command": command,
                "passed": False,
                "exit_code": None,
                "stdout": self._decode_process_output(stdout),
                "stderr": self._decode_process_output(stderr),
                "truncated": truncated,
                "timeout": True,
                "isolated": True,
            }
        elif process.returncode != 0:
            result = {
                "command": command,
                "passed": False,
                "exit_code": process.returncode,
                "stdout": self._decode_process_output(stdout),
                "stderr": self._decode_process_output(stderr),
                "truncated": truncated,
                "isolated": True,
            }
        else:
            result = {
                "command": command,
                "passed": True,
                "exit_code": process.returncode,
                "stdout": self._decode_process_output(stdout),
                "stderr": self._decode_process_output(stderr),
                "truncated": truncated,
                "isolated": True,
            }
        if termination_warning:
            result["termination_warning"] = termination_warning
        result = self._bounded_process_result(result)
        result["evidence"] = self._process_evidence(result)
        if self._result_size(result) > self.max_tool_result_bytes and result.get("isolated") is True:
            result.pop("isolated")
        result = self._trim_stdout_stderr(
            result, after_trim=lambda r: {**r, "evidence": self._process_evidence(r)}
        )
        if self._result_size(result) > self.max_tool_result_bytes:
            raise ToolPolicyError("max_tool_result_bytes is too small for run_tests evidence")
        return result

    def _terminate(self, process: subprocess.Popen) -> str | None:
        # ponytail: kill the whole tree so grandchild processes (cmd.exe
        # shells herding the real command) cannot hold the workspace open.
        if process.poll() is not None:
            return None
        killed, detail = self._kill_tree(process)
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except OSError as error:
                raise ToolPolicyError(f"failed to terminate process: {error}") from error
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired as error:
                raise ToolPolicyError(
                    f"process did not terminate after bounded wait: {detail or error}"
                ) from error
        if not killed and process.poll() is None:
            raise ToolPolicyError(f"failed to terminate process: {detail or 'unknown error'}")
        return detail if not killed else None

    @staticmethod
    def _kill_tree(process: subprocess.Popen) -> tuple[bool, str | None]:
        if os.name == "nt":
            # /T terminates every descendant process, /F forces it.
            system_root = os.environ.get("SystemRoot", r"C:\Windows")
            taskkill = str(Path(system_root) / "System32" / "taskkill.exe")
            if not Path(taskkill).is_file():
                taskkill = "taskkill"
            try:
                completed = subprocess.run(
                    [taskkill, "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=5,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                tree, snapshot_detail = _windows_descendants(process.pid)
                errors = [snapshot_detail] if snapshot_detail else []
                for pid in reversed(list(dict.fromkeys([*tree, process.pid]))):
                    killed, kill_detail = _terminate_windows_pid(pid)
                    if not killed and kill_detail:
                        errors.append(kill_detail)
                if process.poll() is None:
                    try:
                        process.kill()
                    except OSError as kill_error:
                        errors.append(f"process.kill failed: {kill_error}")
                detail = f"taskkill failed: {error}"
                return False, f"{detail}; {'; '.join(errors)}" if errors else detail
            tree, snapshot_detail = _windows_descendants(process.pid)
            remaining, _ = _windows_descendants(process.pid)
            if completed.returncode == 0 and not remaining:
                return True, None
            detail = f"taskkill exited with code {completed.returncode}"
            errors = [snapshot_detail] if snapshot_detail else []
            for pid in reversed(list(dict.fromkeys([*tree, *remaining, process.pid]))):
                killed, kill_detail = _terminate_windows_pid(pid)
                if not killed and kill_detail:
                    errors.append(kill_detail)
            if process.poll() is None:
                try:
                    process.kill()
                except OSError as error:
                    errors.append(f"process.kill failed: {error}")
            if errors:
                return False, f"{detail}; {'; '.join(errors)}"
            return False, detail
        else:
            try:
                os.killpg(process.pid, 9)
            except (ProcessLookupError, PermissionError):
                try:
                    process.kill()
                except OSError as error:
                    return False, f"process kill failed: {error}"
            return True, None

    @staticmethod
    def _isolated_environment() -> dict[str, str]:
        allowed_upper = {
            "APPDATA",
            "COMMONPROGRAMFILES",
            "COMMONPROGRAMFILES(X86)",
            "COMSPEC",
            "HOME",
            "HOMEDRIVE",
            "HOMEPATH",
            "LANG",
            "LC_ALL",
            "LD_LIBRARY_PATH",
            "LOCALAPPDATA",
            "PATH",
            "PATHEXT",
            "PROGRAMDATA",
            "PROGRAMFILES",
            "PROGRAMFILES(X86)",
            "PYTHONHOME",
            "PYTHONIOENCODING",
            "PYTHONPATH",
            "PYTHONUTF8",
            "SYSTEMDRIVE",
            "SYSTEMROOT",
            "TEMP",
            "TMP",
            "TMPDIR",
            "USERPROFILE",
            "VIRTUAL_ENV",
            "WINDIR",
        }
        return {
            key: value
            for key, value in os.environ.items()
            if key.upper() in allowed_upper and not _SECRET_ENV_NAME_RE.search(key)
        }


    @staticmethod
    def _process_group_options(timeout_seconds: int) -> dict[str, Any]:
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            creationflags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
            return {"creationflags": creationflags}
        return {"preexec_fn": _posix_preexec(timeout_seconds)}

    def _bounded_process_result(self, result: dict[str, Any]) -> dict[str, Any]:
        if self._result_size(result) <= self.max_tool_result_bytes:
            return result
        # Keep the externally useful process evidence available even for very
        # small result caps.  Isolation is a provenance flag, not test output,
        # so it is the first optional field we may omit when metadata itself
        # would otherwise exceed the configured limit.
        if result.get("isolated") is True:
            result = {key: value for key, value in result.items() if key != "isolated"}
        result = self._trim_stdout_stderr(result)
        if self._result_size(result) > self.max_tool_result_bytes:
            raise ToolPolicyError("max_tool_result_bytes is too small for run_tests metadata")
        return result

    def _trim_stdout_stderr(
        self, result: dict[str, Any], *, after_trim: Any = None
    ) -> dict[str, Any]:
        stdout = result.get("stdout", "")
        stderr = result.get("stderr", "")
        total_length = len(stdout) + len(stderr)
        if self._result_size(result) <= self.max_tool_result_bytes or total_length == 0:
            return result

        def candidate(length: int) -> dict[str, Any]:
            stdout_length = min(len(stdout), round(length * len(stdout) / total_length))
            stderr_length = min(len(stderr), length - stdout_length)
            retained = stdout_length + stderr_length
            if retained < length:
                stdout_length = min(len(stdout), stdout_length + length - retained)
            bounded = {
                **result,
                "stdout": stdout[:stdout_length],
                "stderr": stderr[:stderr_length],
                "truncated": True,
            }
            return after_trim(bounded) if after_trim is not None else bounded

        low = 0
        high = total_length
        best = candidate(0)
        while low <= high:
            middle = (low + high) // 2
            bounded = candidate(middle)
            if self._result_size(bounded) <= self.max_tool_result_bytes:
                best = bounded
                low = middle + 1
            else:
                high = middle - 1
        return best

    @staticmethod
    def _decode_process_output(output: bytes | str | None) -> str:
        if output is None:
            return ""
        if isinstance(output, bytes):
            return output.decode("utf-8", errors="replace")
        return output

    @staticmethod
    def _process_evidence(result: dict[str, Any]) -> str:
        return (
            f"exit_code={result['exit_code']}; passed={result['passed']}; "
            f"stdout_bytes={len(result.get('stdout', '').encode('utf-8'))}; "
            f"stderr_bytes={len(result.get('stderr', '').encode('utf-8'))}; "
            f"truncated={result.get('truncated', False)}"
        )

    def _propose_patch(self, arguments: dict[str, Any]) -> dict[str, Any]:
        patch = arguments.get("patch")
        edits = arguments.get("edits")
        has_patch = isinstance(patch, str) and patch.strip()
        has_edits = isinstance(edits, list) and bool(edits)
        if has_patch and has_edits:
            raise ToolPolicyError("provide either patch or edits, not both")
        if not has_patch and not has_edits:
            raise ToolPolicyError("patch or edits must be provided")
        if has_edits:
            allowed = {self._normalize_declared_path(path) for path in self.task.files}
            resolved, changed, edit_issues = resolve_edits(
                self.workspace_root,
                edits,
                allowed_files=allowed,
                max_files=self.max_patch_files,
                max_patch_bytes=self.max_patch_bytes,
            )
            if edit_issues:
                return {
                    "ok": False,
                    "error": f"propose_patch rejected: {'; '.join(edit_issues)}",
                    "status": "error",
                }
            applies, detail = check_patch_applies(self.workspace_root, resolved)
            if not applies:
                return {
                    "ok": False,
                    "error": f"propose_patch rejected: patch does not apply cleanly: {detail}",
                    "status": "error",
                }
            return {"patch": resolved, "edits": edits, "files": sorted(changed)}

        if len(patch.encode("utf-8")) > self.max_patch_bytes:
            raise ToolPolicyError(f"patch exceeds max_patch_bytes={self.max_patch_bytes}")
        _, diff_issues = parse_unified_diff(patch)
        if diff_issues:
            return {
                "ok": False,
                "error": f"propose_patch rejected: {'; '.join(diff_issues)}",
                "status": "error",
            }

        paths: list[str] = []
        has_hunk = False
        for line in patch.splitlines():
            if line.startswith("diff --git "):
                match = re.fullmatch(r"diff --git mechanical a/(.+) b/(.+)", line) or re.fullmatch(r"diff --git a/(.+) b/(.+)", line)
                if not match:
                    return {
                        "ok": False,
                        "error": "propose_patch rejected: patch has invalid diff header",
                        "status": "error",
                    }
                for raw_path in match.groups():
                    normalized = self._patch_path(raw_path, prefix=None)
                    if normalized is not None and normalized not in paths:
                        paths.append(normalized)
            elif line.startswith("--- ") or line.startswith("+++ "):
                raw_path = line[4:].split("\t", 1)[0].strip()
                normalized = self._patch_path(raw_path, prefix=None)
                if normalized is not None and normalized not in paths:
                    paths.append(normalized)
            elif line.startswith("@@ "):
                has_hunk = True
        if not paths or not has_hunk:
            return {
                "ok": False,
                "error": "propose_patch rejected: patch is not a unified diff",
                "status": "error",
            }
        if len(paths) > self.max_patch_files:
            raise ToolPolicyError(f"patch exceeds max_patch_files={self.max_patch_files}")
        applies, detail = check_patch_applies(self.workspace_root, patch)
        if not applies:
            return {
                "ok": False,
                "error": f"propose_patch rejected: patch does not apply cleanly: {detail}",
                "status": "error",
            }
        return {"patch": patch, "files": sorted(paths)}


    def _patch_path(self, raw_path: str, *, prefix: str | None) -> str | None:
        del prefix
        if raw_path == "/dev/null":
            return None
        candidate = raw_path[2:] if raw_path[:2] in {"a/", "b/"} else raw_path
        _, relative = self._resolve_allowlisted(candidate)
        return relative

    def _list_files(self, arguments: dict[str, Any]) -> dict[str, Any]:
        raw_path = arguments.get("path", ".")
        directory, relative_root = self._resolve_workspace_path(raw_path)
        if not directory.is_dir():
            raise ToolPolicyError(f"directory does not exist: {relative_root}")
        ignored_parts = {".git", "__pycache__", ".venv", "venv", "node_modules"}
        files = sorted(
            path.relative_to(self.workspace_root).as_posix()
            for path in directory.rglob("*")
            if path.is_file()
            and _fold_path(path.relative_to(self.workspace_root).as_posix()) in self._allowlist
            and not ignored_parts.intersection(path.relative_to(self.workspace_root).parts)
        )
        truncated = len(files) > self.max_files
        result = {"files": files[: self.max_files], "truncated": truncated}
        if self._result_size(result) <= self.max_tool_result_bytes:
            return result
        bounded = list(result["files"])
        while bounded and self._result_size({"files": bounded, "truncated": True}) > self.max_tool_result_bytes:
            bounded.pop()
        result = {"files": bounded, "truncated": True}
        if self._result_size(result) > self.max_tool_result_bytes:
            raise ToolPolicyError("max_tool_result_bytes is too small for list_files metadata")
        return result

    def _search_text(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = arguments.get("query")
        if not isinstance(query, str) or not query:
            raise ToolPolicyError("query must be a non-empty string")
        if len(query) > 256:
            raise ToolPolicyError("query exceeds 256 characters")
        raw_paths = arguments.get("paths", self.task.files)
        if isinstance(raw_paths, str) or not isinstance(raw_paths, (list, tuple)):
            raise ToolPolicyError("paths must be a list of strings")
        if not raw_paths or len(raw_paths) > self.max_files:
            raise ToolPolicyError(f"paths must contain 1..{self.max_files} files")

        matches: list[dict[str, Any]] = []
        for raw_path in raw_paths:
            path, relative = self._resolve_allowlisted(raw_path)
            if not path.is_file():
                raise ToolPolicyError(f"file does not exist: {relative}")
            if path.stat().st_size > self.max_patch_bytes:
                raise ToolPolicyError(f"file exceeds max_patch_bytes={self.max_patch_bytes}: {relative}")
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError as error:
                raise ToolPolicyError(f"file is not UTF-8 text: {relative}") from error
            for line_number, line in enumerate(content.splitlines(), start=1):
                if query in line:
                    matches.append({"path": relative, "line": line_number, "text": line})
                    if len(matches) >= self.max_matches:
                        return self._bounded_matches(matches, truncated=True)
        return self._bounded_matches(matches, truncated=False)

    def _bounded_matches(self, matches: list[dict[str, Any]], *, truncated: bool) -> dict[str, Any]:
        result = {"matches": matches, "truncated": truncated}
        if self._result_size(result) <= self.max_tool_result_bytes:
            return result
        bounded = list(matches)
        while bounded and self._result_size({"matches": bounded, "truncated": True}) > self.max_tool_result_bytes:
            bounded.pop()
        result = {"matches": bounded, "truncated": True}
        if self._result_size(result) > self.max_tool_result_bytes:
            raise ToolPolicyError("max_tool_result_bytes is too small for search_text metadata")
        return result

    def _read_file(self, arguments: dict[str, Any]) -> dict[str, Any]:
        raw_path = arguments.get("path")
        path, relative = self._resolve_allowlisted(raw_path)
        if not path.is_file():
            raise ToolPolicyError(f"file does not exist: {relative}")
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            raise ToolPolicyError(f"file is not UTF-8 text: {relative}") from error
        result = {"path": relative, "content": content, "truncated": False}
        if self._result_size(result) <= self.max_tool_result_bytes:
            return result
        low = 0
        high = len(content)
        best = ""
        while low <= high:
            middle = (low + high) // 2
            candidate = {"path": relative, "content": content[:middle], "truncated": True}
            if self._result_size(candidate) <= self.max_tool_result_bytes:
                best = candidate["content"]
                low = middle + 1
            else:
                high = middle - 1
        bounded = {"path": relative, "content": best, "truncated": True}
        if self._result_size(bounded) > self.max_tool_result_bytes:
            raise ToolPolicyError("max_tool_result_bytes is too small for read_file metadata")
        return bounded

    @staticmethod
    def _result_size(result: dict[str, Any]) -> int:
        return len(json.dumps(result, ensure_ascii=False).encode("utf-8"))

    def _resolve_allowlisted(self, raw_path: Any) -> tuple[Path, str]:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ToolPolicyError("path must be a non-empty string")
        candidate_input = Path(raw_path)
        if candidate_input.is_absolute() or candidate_input.drive or candidate_input.root or "\x00" in raw_path:
            raise ToolPolicyError("absolute or invalid path is not allowed")
        candidate = (self.workspace_root / candidate_input).resolve()
        try:
            relative = candidate.relative_to(self.workspace_root).as_posix()
        except ValueError as error:
            raise ToolPolicyError("path escapes workspace") from error
        if _fold_path(relative) not in self._allowlist:
            raise ToolPolicyError(f"path is outside task allowlist: {relative}")
        return candidate, relative

    def _resolve_workspace_path(self, raw_path: Any) -> tuple[Path, str]:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ToolPolicyError("path must be a non-empty string")
        candidate_input = Path(raw_path)
        if candidate_input.is_absolute() or candidate_input.drive or candidate_input.root or "\x00" in raw_path:
            raise ToolPolicyError("absolute or invalid path is not allowed")
        candidate = (self.workspace_root / candidate_input).resolve()
        try:
            relative = candidate.relative_to(self.workspace_root).as_posix()
        except ValueError as error:
            raise ToolPolicyError("path escapes workspace") from error
        return candidate, relative or "."

    @staticmethod
    def _normalize_declared_path(raw_path: str) -> str:
        candidate = Path(raw_path)
        if candidate.is_absolute() or candidate.drive or candidate.root or "\x00" in raw_path:
            raise ValueError(f"task file must be a relative valid path: {raw_path!r}")
        return _fold_path(candidate.as_posix())

    def _record(
        self,
        name: str,
        arguments: dict[str, Any],
        success: bool,
        error: str | None,
    ) -> None:
        event: dict[str, Any] = {
            "event": "tool_call",
            "name": name,
            "arguments": dict(arguments),
            "success": success,
        }
        if error is not None:
            event["error"] = error
        self._audit_events.append(event)
