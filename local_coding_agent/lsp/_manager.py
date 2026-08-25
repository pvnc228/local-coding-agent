"""LspManager: High-Level Language Server Navigation & Dispatcher."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import threading
from typing import Any, Mapping, Sequence

from ._client import LspClient
from ._fallback import FallbackLspEngine
from ._types import (
    DEFAULT_SERVER_CANDIDATES,
    EXTENSION_TO_LANGUAGE,
    LspHoverResult,
    LspLocation,
    LspSymbol,
)


class LspManager:
    """High-level LSP manager routing queries to active language servers or fallback engine."""

    def __init__(
        self,
        workspace_root: str | Path | None = None,
        *,
        use_fallback_if_missing: bool = True,
        server_candidates: Mapping[str, Sequence[Sequence[str]]] | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.workspace_root = str(Path(workspace_root).resolve()) if workspace_root else os.getcwd()
        self.use_fallback_if_missing = use_fallback_if_missing
        self.timeout = timeout
        self.candidates: dict[str, list[list[str]]] = {
            k: [list(cmd) for cmd in v]
            for k, v in (server_candidates or DEFAULT_SERVER_CANDIDATES).items()
        }
        self._clients: dict[str, LspClient] = {}
        self._fallback_engine = FallbackLspEngine()
        self._lock = threading.Lock()

    def get_language_id(self, file_path: str | Path) -> str:
        """Map file extension to standard LSP language ID."""
        suffix = Path(file_path).suffix.lower()
        return EXTENSION_TO_LANGUAGE.get(suffix, "plaintext")

    def get_client(self, language_id: str) -> LspClient | None:
        """Get or spawn an active LspClient for the given language ID, or None if unavailable."""
        with self._lock:
            if language_id in self._clients:
                client = self._clients[language_id]
                if client.process is not None and client.process.poll() is None:
                    return client
                # Stale process
                client.stop()
                del self._clients[language_id]

            commands = self.candidates.get(language_id, [])
            for cmd in commands:
                binary = cmd[0]
                if shutil.which(binary) is not None:
                    try:
                        client = LspClient(cmd, workspace_root=self.workspace_root, timeout=self.timeout)
                        client.start()
                        client.initialize(self.workspace_root)
                        self._clients[language_id] = client
                        return client
                    except Exception:
                        continue
            return None

    def go_to_definition(
        self,
        file_path: str | Path,
        line: int,
        character: int,
        workspace_root: str | Path | None = None,
    ) -> list[LspLocation]:
        """Find definitions of symbol at (line, character) using LSP client or fallback."""
        root = workspace_root or self.workspace_root
        lang = self.get_language_id(file_path)
        client = self.get_client(lang)
        if client is not None:
            try:
                client.did_open(file_path)
                return client.definition(file_path, line, character)
            except Exception:
                pass
        if self.use_fallback_if_missing:
            return self._fallback_engine.go_to_definition(file_path, line, character, root)
        return []

    def find_references(
        self,
        file_path: str | Path,
        line: int,
        character: int,
        workspace_root: str | Path | None = None,
        include_declaration: bool = True,
    ) -> list[LspLocation]:
        """Find references of symbol at (line, character) using LSP client or fallback."""
        root = workspace_root or self.workspace_root
        lang = self.get_language_id(file_path)
        client = self.get_client(lang)
        if client is not None:
            try:
                client.did_open(file_path)
                return client.references(file_path, line, character, include_declaration)
            except Exception:
                pass
        if self.use_fallback_if_missing:
            return self._fallback_engine.find_references(
                file_path, line, character, root, include_declaration
            )
        return []

    def hover(
        self,
        file_path: str | Path,
        line: int,
        character: int,
        workspace_root: str | Path | None = None,
    ) -> LspHoverResult | None:
        """Hover symbol at (line, character) using LSP client or fallback."""
        root = workspace_root or self.workspace_root
        lang = self.get_language_id(file_path)
        client = self.get_client(lang)
        if client is not None:
            try:
                client.did_open(file_path)
                return client.hover(file_path, line, character)
            except Exception:
                pass
        if self.use_fallback_if_missing:
            return self._fallback_engine.hover(file_path, line, character, root)
        return None

    def document_symbols(
        self,
        file_path: str | Path,
        workspace_root: str | Path | None = None,
    ) -> list[LspSymbol]:
        """Extract symbol outline for file using LSP client or fallback."""
        root = workspace_root or self.workspace_root
        lang = self.get_language_id(file_path)
        client = self.get_client(lang)
        if client is not None:
            try:
                client.did_open(file_path)
                return client.document_symbols(file_path)
            except Exception:
                pass
        if self.use_fallback_if_missing:
            return self._fallback_engine.document_symbols(file_path)
        return []

    def close_all(self) -> None:
        """Shut down and stop all running language servers."""
        with self._lock:
            for client in self._clients.values():
                try:
                    client.stop()
                except Exception:
                    pass
            self._clients.clear()

    def __enter__(self) -> LspManager:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close_all()
