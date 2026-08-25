"""LspClient: Stdio JSON-RPC Client."""

from __future__ import annotations

import concurrent.futures
import os
from pathlib import Path
import subprocess
import sys
import threading
from typing import Any, Mapping, Sequence

from ._types import (
    EXTENSION_TO_LANGUAGE,
    LspConnectionError,
    LspError,
    LspHoverResult,
    LspLocation,
    LspResponseError,
    LspSymbol,
    LspTimeoutError,
    MessageDecoder,
    encode_message,
    path_to_uri,
)


class LspClient:
    """JSON-RPC Language Server client over stdio."""

    def __init__(
        self,
        command: Sequence[str],
        workspace_root: str | Path | None = None,
        *,
        timeout: float = 10.0,
    ) -> None:
        self.command = list(command)
        self.workspace_root = str(Path(workspace_root).resolve()) if workspace_root else os.getcwd()
        self.timeout = timeout
        self.process: subprocess.Popen[bytes] | None = None
        self._next_id = 1
        self._lock = threading.Lock()
        self._pending: dict[int, concurrent.futures.Future[dict[str, Any]]] = {}
        self._reader_thread: threading.Thread | None = None
        self._stopped = threading.Event()
        self.server_capabilities: dict[str, Any] = {}

    def start(self) -> None:
        """Start language server child process and communication threads."""
        if self.process is not None:
            return
        try:
            self.process = subprocess.Popen(
                self.command,
                cwd=self.workspace_root,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except OSError as e:
            raise LspConnectionError(f"Failed to spawn LSP server {self.command!r}: {e}") from e

        self._stopped.clear()
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

    def _read_loop(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        decoder = MessageDecoder()
        read_fn = getattr(self.process.stdout, "read1", self.process.stdout.read)
        try:
            while not self._stopped.is_set():
                chunk = read_fn(4096)
                if not chunk:
                    break
                messages = decoder.push(chunk)
                for msg in messages:
                    self._handle_incoming(msg)
        except (OSError, ValueError):
            pass
        finally:
            self._fail_all_pending("LSP server connection closed")

    def _handle_incoming(self, message: dict[str, Any]) -> None:
        if "id" in message and message["id"] is not None:
            msg_id = message["id"]
            with self._lock:
                future = self._pending.pop(msg_id, None)
            if future is not None and not future.done():
                if "error" in message and message["error"] is not None:
                    err = message["error"]
                    future.set_exception(
                        LspResponseError(
                            code=err.get("code", -1),
                            message=err.get("message", "Unknown LSP error"),
                            data=err.get("data"),
                        )
                    )
                else:
                    future.set_result(message)

    def _fail_all_pending(self, reason: str) -> None:
        with self._lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for fut in pending:
            if not fut.done():
                fut.set_exception(LspConnectionError(reason))

    def send_request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        """Send a JSON-RPC request and wait for the response."""
        if self.process is None or self.process.poll() is not None:
            raise LspConnectionError("LSP server process is not running")

        with self._lock:
            req_id = self._next_id
            self._next_id += 1
            future: concurrent.futures.Future[dict[str, Any]] = concurrent.futures.Future()
            self._pending[req_id] = future

        request_obj = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params if params is not None else {},
        }
        data = encode_message(request_obj)
        try:
            assert self.process.stdin is not None
            self.process.stdin.write(data)
            self.process.stdin.flush()
        except OSError as e:
            with self._lock:
                self._pending.pop(req_id, None)
            raise LspConnectionError(f"Failed to write to LSP server stdin: {e}") from e

        effective_timeout = timeout if timeout is not None else self.timeout
        try:
            response = future.result(timeout=effective_timeout)
            return response.get("result")
        except concurrent.futures.TimeoutError as e:
            with self._lock:
                self._pending.pop(req_id, None)
            raise LspTimeoutError(f"LSP request '{method}' timed out after {effective_timeout}s") from e

    def send_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        if self.process is None or self.process.poll() is not None:
            return
        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params if params is not None else {},
        }
        data = encode_message(notification)
        try:
            assert self.process.stdin is not None
            self.process.stdin.write(data)
            self.process.stdin.flush()
        except OSError:
            pass

    def initialize(self, workspace_root: str | Path | None = None) -> dict[str, Any]:
        """Perform standard LSP initialize handshake."""
        root = str(Path(workspace_root).resolve()) if workspace_root else self.workspace_root
        root_uri = path_to_uri(root)
        params = {
            "processId": os.getpid(),
            "rootUri": root_uri,
            "rootPath": root,
            "capabilities": {
                "textDocument": {
                    "definition": {"dynamicRegistration": False, "linkSupport": True},
                    "references": {"dynamicRegistration": False},
                    "hover": {"contentFormat": ["markdown", "plaintext"]},
                    "documentSymbol": {"hierarchicalDocumentSymbolSupport": True},
                },
                "workspace": {
                    "workspaceFolders": True,
                },
            },
            "workspaceFolders": [
                {"uri": root_uri, "name": Path(root).name}
            ],
        }
        res = self.send_request("initialize", params)
        if isinstance(res, Mapping):
            self.server_capabilities = dict(res.get("capabilities", {}))
        self.send_notification("initialized", {})
        return self.server_capabilities

    def did_open(
        self,
        file_path: str | Path,
        language_id: str | None = None,
        content: str | None = None,
        version: int = 1,
    ) -> None:
        """Send textDocument/didOpen notification."""
        path = Path(file_path).resolve()
        uri = path_to_uri(path)
        if content is None:
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                content = ""
        lang = language_id or EXTENSION_TO_LANGUAGE.get(path.suffix.lower(), "plaintext")
        self.send_notification(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": lang,
                    "version": version,
                    "text": content,
                }
            },
        )

    def did_close(self, file_path: str | Path) -> None:
        """Send textDocument/didClose notification."""
        uri = path_to_uri(Path(file_path).resolve())
        self.send_notification("textDocument/didClose", {"textDocument": {"uri": uri}})

    def definition(self, file_path: str | Path, line: int, character: int) -> list[LspLocation]:
        """Query textDocument/definition for symbol locations."""
        uri = path_to_uri(Path(file_path).resolve())
        params = {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
        }
        res = self.send_request("textDocument/definition", params)
        if res is None:
            return []
        if isinstance(res, Mapping):
            return [LspLocation.from_wire(res)]
        if isinstance(res, Sequence):
            return [LspLocation.from_wire(item) for item in res if isinstance(item, Mapping)]
        return []

    def references(
        self,
        file_path: str | Path,
        line: int,
        character: int,
        include_declaration: bool = True,
    ) -> list[LspLocation]:
        """Query textDocument/references for symbol occurrences."""
        uri = path_to_uri(Path(file_path).resolve())
        params = {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
            "context": {"includeDeclaration": include_declaration},
        }
        res = self.send_request("textDocument/references", params)
        if not res or not isinstance(res, Sequence):
            return []
        return [LspLocation.from_wire(item) for item in res if isinstance(item, Mapping)]

    def hover(self, file_path: str | Path, line: int, character: int) -> LspHoverResult | None:
        """Query textDocument/hover for symbol type and docstring information."""
        uri = path_to_uri(Path(file_path).resolve())
        params = {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
        }
        res = self.send_request("textDocument/hover", params)
        return LspHoverResult.from_wire(res)

    def document_symbols(self, file_path: str | Path) -> list[LspSymbol]:
        """Query textDocument/documentSymbol for file symbol outline."""
        uri = path_to_uri(Path(file_path).resolve())
        params = {"textDocument": {"uri": uri}}
        res = self.send_request("textDocument/documentSymbol", params)
        return LspSymbol.from_wire(res, file_uri=uri)

    def shutdown(self) -> None:
        """Gracefully shut down the language server."""
        if self.process is None or self.process.poll() is not None:
            return
        try:
            self.send_request("shutdown", None, timeout=3.0)
        except Exception:
            pass
        self.send_notification("exit", None)

    def stop(self) -> None:
        """Terminate process and release reader resources."""
        self._stopped.set()
        if self.process is not None:
            try:
                self.shutdown()
            except Exception:
                pass
            try:
                if self.process.poll() is None:
                    self.process.terminate()
                    self.process.wait(timeout=2.0)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
            self.process = None
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=1.0)
            self._reader_thread = None

    def __enter__(self) -> LspClient:
        self.start()
        self.initialize()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.stop()
