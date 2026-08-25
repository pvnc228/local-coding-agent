"""Tool Output Spill Store for bounding model context size.

Adapted from DeepSeek Harness @deepseek-ai/dsh-spill and @deepseek-ai/dsh-spill-policy.
Manages session-scoped artifact persistence for oversized tool outputs with
path-traversal protection and secure permissions.
"""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path


DEFAULT_MAX_BYTES: int = 30 * 1024  # 30KB
DEFAULT_MAX_LINES: int = 1000


@dataclass(frozen=True)
class SpillRef:
    """Descriptor for a persisted tool output spill artifact."""

    locator: str
    bytes: int
    lines: int
    preview_head: str
    preview_tail: str
    retrieval_hint: str


class SpillStore:
    """Session-scoped storage backend for oversized tool outputs."""

    def __init__(self, root_dir: Path | str | None = None) -> None:
        if root_dir is None:
            self.root_dir = (Path.cwd() / ".local_agent" / "spill").resolve()
        else:
            self.root_dir = Path(root_dir).resolve()

    def _sanitize_session_id(self, session_id: str) -> str:
        """Derive a safe, injective directory segment from an untrusted session ID."""
        if not session_id or session_id in (".", ".."):
            return "session_default"
        # Remove path separators and traversal tokens
        cleaned = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id).strip(".")
        if not cleaned:
            cleaned = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:16]
        return f"session_{cleaned}"

    def _sanitize_file_name(self, name: str) -> str:
        """Derive a safe filename without path separators."""
        base_name = Path(name).name
        cleaned = re.sub(r"[^A-Za-z0-9_.-]", "_", base_name).strip(".")
        return cleaned or "output.txt"

    def _get_session_dir(self, session_id: str) -> Path:
        """Get the absolute session directory and assert it stays within root_dir."""
        safe_seg = self._sanitize_session_id(session_id)
        session_path = (self.root_dir / safe_seg).resolve()
        try:
            session_path.relative_to(self.root_dir)
        except ValueError as err:
            raise ValueError(f"Path traversal detected for session_id '{session_id}'") from err
        return session_path

    def save_text(
        self,
        session_id: str,
        content: str,
        source_tool: str = "tool",
        suggested_name: str = "output.txt",
    ) -> SpillRef:
        """Persist text content to a session-scoped spill file and return a SpillRef."""
        session_dir = self._get_session_dir(session_id)
        session_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            try:
                os.chmod(session_dir, 0o700)
            except OSError:
                pass

        safe_name = self._sanitize_file_name(suggested_name)
        file_id = uuid.uuid4().hex[:12]
        target_file = (session_dir / f"{file_id}_{safe_name}").resolve()

        try:
            target_file.relative_to(self.root_dir)
        except ValueError as err:
            raise ValueError(f"Path traversal detected for target file '{target_file}'") from err

        encoded = content.encode("utf-8")
        byte_count = len(encoded)

        # Split preserving newlines for accurate reconstruction
        raw_lines = content.splitlines(keepends=True)
        line_count = len(raw_lines)

        # Write securely with exclusive creation / owner-only permissions (0600 on POSIX)
        flags = os.O_CREAT | os.O_WRONLY | os.O_TRUNC
        if hasattr(os, "O_EXCL"):
            flags |= os.O_EXCL
        fd = os.open(str(target_file), flags, 0o600)
        with open(fd, "wb") as f:
            f.write(encoded)

        if os.name != "nt":
            try:
                os.chmod(target_file, 0o600)
            except OSError:
                pass

        # Build head & tail previews (up to 20 lines each)
        preview_limit = 20
        preview_head = "".join(raw_lines[:preview_limit])
        preview_tail = "".join(raw_lines[-preview_limit:])

        locator = target_file.as_posix()
        retrieval_hint = f"read_spill(locator='{locator}', offset_line=0, limit_lines=1000)"

        return SpillRef(
            locator=locator,
            bytes=byte_count,
            lines=line_count,
            preview_head=preview_head,
            preview_tail=preview_tail,
            retrieval_hint=retrieval_hint,
        )

    def read_spill(self, locator: str, offset_line: int = 0, limit_lines: int = 1000) -> str:
        """Read a slice of lines from a persisted spill artifact."""
        target_path = Path(locator).resolve()
        try:
            target_path.relative_to(self.root_dir)
        except ValueError as err:
            raise ValueError(
                f"Path traversal detected: locator '{locator}' is outside spill root '{self.root_dir}'"
            ) from err

        if not target_path.is_file():
            raise FileNotFoundError(f"Spill artifact not found: {locator}")

        if offset_line < 0:
            offset_line = 0
        if limit_lines < 0:
            limit_lines = 0

        with open(target_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

        selected = lines[offset_line : offset_line + limit_lines]
        return "".join(selected)

    def maybe_spill(
        self,
        session_id: str,
        content: str,
        source_tool: str = "tool",
        max_bytes: int = DEFAULT_MAX_BYTES,
        max_lines: int = DEFAULT_MAX_LINES,
    ) -> tuple[bool, str, SpillRef | None]:
        """Check content against byte and line thresholds, spilling if exceeded.

        Returns:
            (spilled: bool, resulting_message: str, ref: SpillRef | None)
        """
        encoded_len = len(content.encode("utf-8"))
        raw_lines = content.splitlines(keepends=True)
        line_count = len(raw_lines)

        if encoded_len <= max_bytes and line_count <= max_lines:
            return False, content, None

        ref = self.save_text(
            session_id=session_id,
            content=content,
            source_tool=source_tool,
            suggested_name=f"{source_tool}_output.txt",
        )

        omitted_lines = max(0, line_count - 40)
        notice = (
            f"[OUTPUT TRUNCATED & SPILLED TO STORE]\n"
            f"Tool '{source_tool}' produced {ref.bytes} bytes ({ref.lines} lines), "
            f"exceeding limits ({max_bytes} bytes / {max_lines} lines).\n"
            f"Full output was saved to locator: {ref.locator}\n\n"
            f"--- BEGIN PREVIEW (HEAD) ---\n"
            f"{ref.preview_head.rstrip()}\n"
            f"--- END PREVIEW (HEAD) ---\n"
            f"... [{omitted_lines} lines omitted] ...\n"
            f"--- BEGIN PREVIEW (TAIL) ---\n"
            f"{ref.preview_tail.rstrip()}\n"
            f"--- END PREVIEW (TAIL) ---\n\n"
            f"Retrieval guidance: {ref.retrieval_hint}"
        )

        return True, notice, ref


# Global default instance & convenience helpers
_DEFAULT_SPILL_STORE: SpillStore | None = None


def get_default_spill_store() -> SpillStore:
    """Retrieve or lazily initialize the default SpillStore instance."""
    global _DEFAULT_SPILL_STORE
    if _DEFAULT_SPILL_STORE is None:
        _DEFAULT_SPILL_STORE = SpillStore()
    return _DEFAULT_SPILL_STORE


def save_text(
    session_id: str,
    content: str,
    source_tool: str = "tool",
    suggested_name: str = "output.txt",
) -> SpillRef:
    """Save text content to the default spill store."""
    return get_default_spill_store().save_text(
        session_id=session_id,
        content=content,
        source_tool=source_tool,
        suggested_name=suggested_name,
    )


def read_spill(locator: str, offset_line: int = 0, limit_lines: int = 1000) -> str:
    """Read a slice of lines from a spill locator."""
    # Check if locator is under an explicit custom root or default root
    store = get_default_spill_store()
    target_path = Path(locator).resolve()
    try:
        target_path.relative_to(store.root_dir)
        return store.read_spill(locator, offset_line, limit_lines)
    except ValueError:
        # Fall back to resolving within locator's parent spill directory
        parent_dir = target_path.parent
        custom_store = SpillStore(root_dir=parent_dir.parent)
        return custom_store.read_spill(locator, offset_line, limit_lines)


def maybe_spill(
    session_id: str,
    content: str,
    source_tool: str = "tool",
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_lines: int = DEFAULT_MAX_LINES,
) -> tuple[bool, str, SpillRef | None]:
    """Check content against thresholds using the default spill store."""
    return get_default_spill_store().maybe_spill(
        session_id=session_id,
        content=content,
        source_tool=source_tool,
        max_bytes=max_bytes,
        max_lines=max_lines,
    )
