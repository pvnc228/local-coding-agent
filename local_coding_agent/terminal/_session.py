"""Persistent interactive session wrapper."""

from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import threading
import time
from typing import Any, Mapping, Sequence

from ..terminal._process import _parse_command, kill_process_tree
from ..terminal._types import (
    TerminalError,
    TerminalProcessExitedError,
    TerminalSessionInfo,
)


# ============================================================================
# TerminalSession
# ============================================================================

class TerminalSession:
    """Persistent interactive background shell / process wrapper with non-blocking I/O drainer."""

    def __init__(
        self,
        session_id: str,
        cwd: str | Path,
        shell: str | Sequence[str] | None = None,
        *,
        max_buffer_bytes: int = 1_048_576,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.session_id = session_id
        self.cwd = Path(cwd).resolve()
        if not self.cwd.is_dir():
            raise TerminalError(f"Working directory does not exist: {self.cwd}")

        self.max_buffer_bytes = max_buffer_bytes
        self.created_at = time.time()
        self._buffer = ""
        self._total_chars_written = 0
        self._buffer_lock = threading.Lock()
        self._io_lock = threading.Lock()
        self._output_event = threading.Event()
        self._last_output_time = time.monotonic()
        self._closed = False
        self._master_fd: int | None = None
        self._slave_fd: int | None = None

        # Resolve shell command
        if shell is None:
            if os.name == "nt":
                default_shell = os.environ.get("COMSPEC", "cmd.exe")
                self.shell_cmd = default_shell
                cmd_args: list[str] = [default_shell]
            else:
                default_shell = os.environ.get("SHELL", "/bin/bash")
                if not Path(default_shell).exists():
                    default_shell = "/bin/sh"
                self.shell_cmd = default_shell
                cmd_args = [default_shell]
        elif isinstance(shell, str):
            self.shell_cmd = shell
            cmd_args = _parse_command(shell)
        else:
            cmd_args = list(shell)
            self.shell_cmd = " ".join(cmd_args)

        # Environment
        spawn_env = dict(os.environ)
        if env:
            spawn_env.update(env)
        # Ensure interactive Python doesn't buffer and UTF-8 is forced
        spawn_env["PYTHONUNBUFFERED"] = "1"
        spawn_env["PYTHONIOENCODING"] = "utf-8"

        # Platform PTY or Pipe Subprocess
        self.use_pty = False
        if os.name != "nt":
            try:
                import pty
                import termios
                import tty

                master, slave = pty.openpty()
                self._master_fd = master
                self._slave_fd = slave
                self.use_pty = True

                self.process = subprocess.Popen(
                    cmd_args,
                    cwd=str(self.cwd),
                    stdin=slave,
                    stdout=slave,
                    stderr=slave,
                    env=spawn_env,
                    start_new_session=True,
                    close_fds=True,
                )
                os.close(slave)
                self._slave_fd = None
            except Exception:
                # Fallback to standard pipes on failure
                self.use_pty = False
                if self._master_fd is not None:
                    try:
                        os.close(self._master_fd)
                    except OSError:
                        pass
                    self._master_fd = None
                if self._slave_fd is not None:
                    try:
                        os.close(self._slave_fd)
                    except OSError:
                        pass
                    self._slave_fd = None

        if not self.use_pty:
            kwargs: dict[str, Any] = {
                "cwd": str(self.cwd),
                "stdin": subprocess.PIPE,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.STDOUT,
                "env": spawn_env,
                "bufsize": 0,
            }
            if os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            else:
                kwargs["start_new_session"] = True

            self.process = subprocess.Popen(cmd_args, **kwargs)

        self.pid = self.process.pid

        # Start drainer thread
        self._reader_thread = threading.Thread(
            target=self._drain_loop,
            name=f"terminal-drainer-{self.session_id}",
            daemon=True,
        )
        self._reader_thread.start()

        # Brief warmup sleep to capture initial prompt/motd
        time.sleep(0.05)

    def _drain_loop(self) -> None:
        """Continuously drain child output and append to bounded buffer."""
        if self.use_pty and self._master_fd is not None:
            fd = self._master_fd
            while not self._closed:
                try:
                    chunk = os.read(fd, 4096)
                    if not chunk:
                        break
                    self._append_bytes(chunk)
                except (OSError, ValueError):
                    break
        else:
            assert self.process.stdout is not None
            read_fn = getattr(self.process.stdout, "read1", self.process.stdout.read)
            while not self._closed:
                try:
                    chunk = read_fn(4096)
                    if not chunk:
                        break
                    self._append_bytes(chunk)
                except (OSError, ValueError):
                    break

        self._output_event.set()

    def _append_bytes(self, data: bytes) -> None:
        text = data.decode("utf-8", errors="replace")
        with self._buffer_lock:
            self._total_chars_written += len(text)
            self._buffer += text
            if len(self._buffer) > self.max_buffer_bytes:
                drop_len = len(self._buffer) - self.max_buffer_bytes
                self._buffer = self._buffer[drop_len:]
            self._last_output_time = time.monotonic()
        self._output_event.set()

    def is_alive(self) -> bool:
        """Return True if the wrapped terminal process is currently running."""
        if self._closed:
            return False
        return self.process.poll() is None

    @property
    def exit_code(self) -> int | None:
        """Return process exit code if terminated, or None if still running."""
        return self.process.poll()

    @property
    def buffer(self) -> str:
        """Return entire current retained buffer."""
        with self._buffer_lock:
            return self._buffer

    def read_buffer(self, offset: int = 0, limit: int = 4096) -> str:
        """Read a slice of retained terminal output.

        If offset is negative, reads relative to the end of the buffer.
        """
        limit = max(0, limit)
        with self._buffer_lock:
            buf_len = len(self._buffer)
            if offset < 0:
                start = max(0, buf_len + offset)
                return self._buffer[start : start + limit]
            if offset >= buf_len:
                return ""
            return self._buffer[offset : offset + limit]

    def send_input(
        self,
        text: str,
        wait_ms: int = 500,
        submit: bool = True,
    ) -> str:
        """Send text input to the terminal and wait up to wait_ms to collect output delta."""
        with self._io_lock:
            if not self.is_alive():
                raise TerminalProcessExitedError(
                    f"Terminal session '{self.session_id}' (PID {self.pid}) has exited with code {self.exit_code}"
                )

            with self._buffer_lock:
                start_pos = self._total_chars_written

            data = str(text)
            if submit and not data.endswith(("\n", "\r\n")):
                data += "\r\n" if (os.name == "nt" and not self.use_pty) else "\n"

            encoded = data.encode("utf-8")
            try:
                if self.use_pty and self._master_fd is not None:
                    os.write(self._master_fd, encoded)
                else:
                    assert self.process.stdin is not None
                    self.process.stdin.write(encoded)
                    self.process.stdin.flush()
            except (BrokenPipeError, OSError) as e:
                if not self.is_alive():
                    raise TerminalProcessExitedError(
                        f"Terminal session '{self.session_id}' exited during write"
                    ) from e
                raise TerminalError(f"Failed to write to terminal stdin: {e}") from e

            if wait_ms <= 0:
                with self._buffer_lock:
                    buf_len = len(self._buffer)
                    buffer_start_pos = self._total_chars_written - buf_len
                    if start_pos <= buffer_start_pos:
                        return self._buffer
                    return self._buffer[start_pos - buffer_start_pos :]

            # Wait loop for output settling
            deadline = time.monotonic() + (wait_ms / 1000.0)
            last_pos = start_pos
            last_change = time.monotonic()

            while time.monotonic() < deadline:
                self._output_event.wait(timeout=0.03)
                self._output_event.clear()
                with self._buffer_lock:
                    curr_pos = self._total_chars_written
                if curr_pos != last_pos:
                    last_pos = curr_pos
                    last_change = time.monotonic()
                elif curr_pos > start_pos and (time.monotonic() - last_change) >= 0.15:
                    # Output has settled for 150ms after receiving new bytes
                    break

                if not self.is_alive():
                    # Allow drainer to finish reading remaining stream
                    time.sleep(0.05)
                    break

            with self._buffer_lock:
                buf_len = len(self._buffer)
                buffer_start_pos = self._total_chars_written - buf_len
                if start_pos <= buffer_start_pos:
                    return self._buffer
                return self._buffer[start_pos - buffer_start_pos :]

    def send_signal(self, sig: str) -> bool:
        """Deliver a signal (e.g. SIGINT, SIGTERM, SIGKILL, CTRL_C) to the terminal process."""
        if not self.is_alive():
            return False

        sig_upper = sig.strip().upper()

        if sig_upper in {"SIGINT", "CTRL_C", "INT", "2"}:
            # Send Ctrl+C
            try:
                if self.use_pty and self._master_fd is not None:
                    os.write(self._master_fd, b"\x03")
                elif self.process.stdin is not None:
                    self.process.stdin.write(b"\x03")
                    self.process.stdin.flush()
            except OSError:
                pass

            if os.name == "nt":
                try:
                    self.process.send_signal(signal.CTRL_BREAK_EVENT)
                except (OSError, ValueError):
                    try:
                        self.process.send_signal(signal.CTRL_C_EVENT)
                    except (OSError, ValueError):
                        pass
            else:
                my_pgid = os.getpgid(0)
                try:
                    pgid = os.getpgid(self.process.pid)
                    if pgid != my_pgid and pgid > 1:
                        os.killpg(pgid, signal.SIGINT)
                    else:
                        self.process.send_signal(signal.SIGINT)
                except (OSError, ProcessLookupError):
                    try:
                        self.process.send_signal(signal.SIGINT)
                    except OSError:
                        pass
            return True

        elif sig_upper in {"SIGTERM", "TERM", "15"}:
            if os.name == "nt":
                kill_process_tree(self.process.pid, timeout=1.0)
            else:
                my_pgid = os.getpgid(0)
                try:
                    pgid = os.getpgid(self.process.pid)
                    if pgid != my_pgid and pgid > 1:
                        os.killpg(pgid, signal.SIGTERM)
                    else:
                        self.process.terminate()
                except (OSError, ProcessLookupError):
                    self.process.terminate()
            return True

        elif sig_upper in {"SIGKILL", "KILL", "9"}:
            kill_process_tree(self.process.pid, timeout=1.0)
            return True

        elif sig_upper in {"SIGBREAK", "BREAK"}:
            if os.name == "nt":
                try:
                    self.process.send_signal(signal.CTRL_BREAK_EVENT)
                    return True
                except (OSError, ValueError):
                    pass
            return False

        else:
            if hasattr(signal, sig_upper):
                sig_num = getattr(signal, sig_upper)
                try:
                    if os.name == "nt":
                        self.process.send_signal(sig_num)
                    else:
                        my_pgid = os.getpgid(0)
                        pgid = os.getpgid(self.process.pid)
                        if pgid != my_pgid and pgid > 1:
                            os.killpg(pgid, sig_num)
                        else:
                            self.process.send_signal(sig_num)
                    return True
                except (OSError, ValueError):
                    return False
            return False

    def close(self, timeout: float = 2.0) -> None:
        """Gracefully shut down and terminate the process tree and reader resources."""
        if self._closed:
            return
        self._closed = True

        # Close stdin to signal EOF
        if not self.use_pty and self.process.stdin is not None:
            try:
                self.process.stdin.close()
            except (OSError, ValueError):
                pass

        # Terminate process if still running
        if self.process.poll() is None:
            try:
                self.process.terminate()
                self.process.wait(timeout=timeout)
            except Exception:
                kill_process_tree(self.process.pid, timeout=timeout)
                try:
                    self.process.wait(timeout=0.5)
                except Exception:
                    pass

        # Close pty master fd
        if self._master_fd is not None:
            try:
                os.close(self._master_fd)
            except OSError:
                pass
            self._master_fd = None

        if self._slave_fd is not None:
            try:
                os.close(self._slave_fd)
            except OSError:
                pass
            self._slave_fd = None

        # Close stdout pipe
        if not self.use_pty and self.process.stdout is not None:
            try:
                self.process.stdout.close()
            except (OSError, ValueError):
                pass

        if self._reader_thread.is_alive():
            self._reader_thread.join(timeout=1.0)

    def snapshot(self) -> TerminalSessionInfo:
        """Return an immutable snapshot of session state."""
        return TerminalSessionInfo(
            session_id=self.session_id,
            pid=self.pid,
            alive=self.is_alive(),
            exit_code=self.exit_code,
            cwd=str(self.cwd),
            shell=self.shell_cmd,
            buffer_size=len(self.buffer),
            created_at=self.created_at,
        )

    def __enter__(self) -> TerminalSession:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close(timeout=0.5)
        except Exception:
            pass
