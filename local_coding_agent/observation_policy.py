"""Filesystem Observation Policy and Prior-Observation Gate.

Adapted from DeepSeek Harness @deepseek-ai/dsh-fs-observation-policy.
Enforces the safety invariant that files must be authoritatively observed (read)
within the active session before any mutation (edit, patch, replace) is allowed.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


class FsObservationError(Exception):
    """Raised when a file system mutation violates the observation policy."""


class FsObservationGate:
    """Tracks observed files and content hashes per session.

    Enforces that local models must read/observe a file before attempting
    to propose or apply edits to it.
    """

    def __init__(self) -> None:
        # Maps session_id -> {canonical_path: sha256_hex_hash}
        self._sessions: dict[str, dict[str, str]] = {}

    def _canonical_key(self, file_path: str | Path) -> str:
        """Derive a canonical key for cross-platform matching."""
        resolved = Path(file_path).resolve()
        posix = resolved.as_posix()
        return posix.casefold() if os.name == "nt" else posix

    def observe_file(
        self,
        session_id: str,
        file_path: str | Path,
        content: str | bytes | None = None,
    ) -> str:
        """Record an authoritative observation of a file and return its SHA-256 hash.

        If content is provided, it is hashed directly.
        If content is None, the file is read from disk.
        """
        path_obj = Path(file_path).resolve()

        if content is not None:
            if isinstance(content, str):
                raw_bytes = content.encode("utf-8")
            elif isinstance(content, bytes):
                raw_bytes = content
            else:
                raise TypeError(f"Expected str, bytes, or None for content, got {type(content).__name__}")
        else:
            if not path_obj.is_file():
                raise FileNotFoundError(f"File not found for observation: {file_path}")
            raw_bytes = path_obj.read_bytes()

        sha256_hash = hashlib.sha256(raw_bytes).hexdigest()
        canonical_key = self._canonical_key(path_obj)

        if session_id not in self._sessions:
            self._sessions[session_id] = {}

        self._sessions[session_id][canonical_key] = sha256_hash
        return sha256_hash

    def is_observed(self, session_id: str, file_path: str | Path) -> bool:
        """Check if a file has been observed in the given session."""
        session_map = self._sessions.get(session_id)
        if not session_map:
            return False
        canonical_key = self._canonical_key(file_path)
        return canonical_key in session_map

    def get_observed_hash(self, session_id: str, file_path: str | Path) -> str | None:
        """Get the recorded SHA-256 hash for an observed file, or None if unobserved."""
        session_map = self._sessions.get(session_id)
        if not session_map:
            return None
        canonical_key = self._canonical_key(file_path)
        return session_map.get(canonical_key)

    def verify_edit_intent(
        self,
        session_id: str,
        file_path: str | Path,
    ) -> tuple[bool, str | None]:
        """Verify whether an edit can proceed under the observation policy.

        Returns:
            (True, None) if observed, or
            (False, "FS_NOT_OBSERVED: edit requires reading '<file>' first") if not observed.
        """
        if self.is_observed(session_id, file_path):
            return True, None

        display_name = str(file_path)
        return False, f"FS_NOT_OBSERVED: edit requires reading '{display_name}' first"

    def verify_freshness(
        self,
        session_id: str,
        file_path: str | Path,
    ) -> tuple[bool, str | None]:
        """Verify that the file on disk matches the hash recorded when observed."""
        if not self.is_observed(session_id, file_path):
            display_name = str(file_path)
            return False, f"FS_NOT_OBSERVED: edit requires reading '{display_name}' first"

        path_obj = Path(file_path).resolve()
        if not path_obj.is_file():
            return False, f"FS_DELETED: file '{file_path}' was deleted after observation"

        current_hash = hashlib.sha256(path_obj.read_bytes()).hexdigest()
        observed_hash = self.get_observed_hash(session_id, file_path)

        if current_hash != observed_hash:
            return False, f"FS_STALE: file '{file_path}' was modified on disk since last observed"

        return True, None

    def reset_session(self, session_id: str) -> None:
        """Clear all observation records for a session."""
        self._sessions.pop(session_id, None)

    def list_observed(self, session_id: str) -> list[str]:
        """List all canonical paths observed in a session."""
        return list(self._sessions.get(session_id, {}).keys())

    def clear(self) -> None:
        """Clear all session observations."""
        self._sessions.clear()


# Global default instance & convenience helpers
_DEFAULT_OBSERVATION_GATE: FsObservationGate | None = None


def get_default_observation_gate() -> FsObservationGate:
    """Retrieve or lazily initialize the default FsObservationGate."""
    global _DEFAULT_OBSERVATION_GATE
    if _DEFAULT_OBSERVATION_GATE is None:
        _DEFAULT_OBSERVATION_GATE = FsObservationGate()
    return _DEFAULT_OBSERVATION_GATE


def observe_file(
    session_id: str,
    file_path: str | Path,
    content: str | bytes | None = None,
) -> str:
    """Record file observation in the default gate."""
    return get_default_observation_gate().observe_file(
        session_id=session_id,
        file_path=file_path,
        content=content,
    )


def is_observed(session_id: str, file_path: str | Path) -> bool:
    """Check if file is observed in the default gate."""
    return get_default_observation_gate().is_observed(
        session_id=session_id,
        file_path=file_path,
    )


def verify_edit_intent(
    session_id: str,
    file_path: str | Path,
) -> tuple[bool, str | None]:
    """Verify edit intent against the default gate."""
    return get_default_observation_gate().verify_edit_intent(
        session_id=session_id,
        file_path=file_path,
    )


def reset_session(session_id: str) -> None:
    """Reset session observations in the default gate."""
    get_default_observation_gate().reset_session(session_id)
