"""LSP wire enums, exceptions, data models, and protocol helpers."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence
import urllib.parse
import urllib.request


# ============================================================================
# Constants & LSP Wire Enums
# ============================================================================

HEADER_SEPARATOR = b"\r\n\r\n"
MAX_HEADER_BYTES = 64 * 1024
MAX_MESSAGE_BYTES = 10 * 1024 * 1024

SYMBOL_KIND_NAMES: dict[int, str] = {
    1: "File",
    2: "Module",
    3: "Namespace",
    4: "Package",
    5: "Class",
    6: "Method",
    7: "Property",
    8: "Field",
    9: "Constructor",
    10: "Enum",
    11: "Interface",
    12: "Function",
    13: "Variable",
    14: "Constant",
    15: "String",
    16: "Number",
    17: "Boolean",
    18: "Array",
    19: "Object",
    20: "Key",
    21: "Null",
    22: "EnumMember",
    23: "Struct",
    24: "Event",
    25: "Operator",
    26: "TypeParameter",
}

EXTENSION_TO_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".rs": "rust",
    ".go": "go",
}

DEFAULT_SERVER_CANDIDATES: dict[str, list[list[str]]] = {
    "python": [
        ["pyright-langserver", "--stdio"],
        ["pyright", "--stdio"],
        ["basedpyright-langserver", "--stdio"],
        ["pylsp"],
    ],
    "typescript": [
        ["typescript-language-server", "--stdio"],
    ],
    "javascript": [
        ["typescript-language-server", "--stdio"],
    ],
    "rust": [
        ["rust-analyzer"],
    ],
    "go": [
        ["gopls"],
    ],
}


# ============================================================================
# Exceptions
# ============================================================================

class LspError(RuntimeError):
    """Base exception for LSP errors."""


class LspTimeoutError(LspError):
    """An LSP request timed out waiting for server response."""


class LspConnectionError(LspError):
    """LSP server process failed to start or crashed."""


class LspResponseError(LspError):
    """Server returned an LSP JSON-RPC error response."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(f"LSP error {code}: {message}")
        self.code = code
        self.message = message
        self.data = data


# ============================================================================
# Data Models
# ============================================================================

@dataclass(frozen=True)
class LspPosition:
    """A zero-based line and character cursor coordinate."""

    line: int
    character: int

    def to_dict(self) -> dict[str, int]:
        return {"line": self.line, "character": self.character}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> LspPosition:
        return cls(line=int(data["line"]), character=int(data["character"]))


@dataclass(frozen=True)
class LspRange:
    """A half-open range `[start, end)` within a document."""

    start: LspPosition
    end: LspPosition

    def to_dict(self) -> dict[str, Any]:
        return {"start": self.start.to_dict(), "end": self.end.to_dict()}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> LspRange:
        return cls(
            start=LspPosition.from_dict(data["start"]),
            end=LspPosition.from_dict(data["end"]),
        )


@dataclass(frozen=True)
class LspLocation:
    """A document URI, range within it, and local filesystem path."""

    uri: str
    range: LspRange
    file_path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "uri": self.uri,
            "range": self.range.to_dict(),
            "file_path": self.file_path,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> LspLocation:
        uri = str(data["uri"])
        file_path = str(data.get("file_path") or uri_to_path(uri))
        return cls(
            uri=uri,
            range=LspRange.from_dict(data["range"]),
            file_path=file_path,
        )

    @classmethod
    def from_wire(cls, data: Mapping[str, Any]) -> LspLocation:
        # Handles Location (uri, range) and LocationLink (targetUri, targetSelectionRange/targetRange)
        if "targetUri" in data:
            uri = str(data["targetUri"])
            raw_range = data.get("targetSelectionRange") or data.get("targetRange") or {}
            range_obj = LspRange.from_dict(raw_range)
        else:
            uri = str(data["uri"])
            range_obj = LspRange.from_dict(data["range"])
        return cls(uri=uri, range=range_obj, file_path=uri_to_path(uri))


@dataclass(frozen=True)
class LspHoverResult:
    """Hover documentation or signature information."""

    contents: str
    range: LspRange | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "contents": self.contents,
            "range": self.range.to_dict() if self.range is not None else None,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> LspHoverResult:
        raw_range = data.get("range")
        range_obj = LspRange.from_dict(raw_range) if raw_range is not None else None
        return cls(contents=str(data.get("contents", "")), range=range_obj)

    @classmethod
    def from_wire(cls, data: Any) -> LspHoverResult | None:
        if not data or not isinstance(data, Mapping):
            return None
        raw_contents = data.get("contents")
        contents = _render_hover_contents(raw_contents)
        if not contents.strip():
            return None
        raw_range = data.get("range")
        range_obj = LspRange.from_dict(raw_range) if isinstance(raw_range, Mapping) else None
        return cls(contents=contents, range=range_obj)


@dataclass(frozen=True)
class LspSymbol:
    """A symbol outline entry (function, class, variable, etc.)."""

    name: str
    kind: int
    kind_name: str
    location: LspLocation

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "kind_name": self.kind_name,
            "location": self.location.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> LspSymbol:
        kind = int(data.get("kind", 12))
        kind_name = str(data.get("kind_name") or SYMBOL_KIND_NAMES.get(kind, f"Kind({kind})"))
        return cls(
            name=str(data["name"]),
            kind=kind,
            kind_name=kind_name,
            location=LspLocation.from_dict(data["location"]),
        )

    @classmethod
    def from_wire(cls, data: Any, file_uri: str = "") -> list[LspSymbol]:
        if not data or not isinstance(data, Sequence):
            return []
        symbols: list[LspSymbol] = []
        for item in data:
            if not isinstance(item, Mapping):
                continue
            name = str(item.get("name", ""))
            kind = int(item.get("kind", 12))
            kind_name = SYMBOL_KIND_NAMES.get(kind, f"Kind({kind})")

            # Hierarchical DocumentSymbol
            if "range" in item or "selectionRange" in item:
                raw_range = item.get("selectionRange") or item.get("range")
                range_obj = LspRange.from_dict(raw_range) if isinstance(raw_range, Mapping) else LspRange(
                    LspPosition(0, 0), LspPosition(0, 0)
                )
                loc = LspLocation(uri=file_uri, range=range_obj, file_path=uri_to_path(file_uri))
                symbols.append(cls(name=name, kind=kind, kind_name=kind_name, location=loc))
                # Recurse into children
                children = item.get("children")
                if children and isinstance(children, Sequence):
                    symbols.extend(cls.from_wire(children, file_uri=file_uri))
            # Flat SymbolInformation
            elif "location" in item and isinstance(item["location"], Mapping):
                loc = LspLocation.from_wire(item["location"])
                symbols.append(cls(name=name, kind=kind, kind_name=kind_name, location=loc))
        return symbols


# ============================================================================
# Protocol Helpers & URI Conversion
# ============================================================================

def path_to_uri(file_path: str | Path) -> str:
    """Convert a filesystem path to a standard file:// URI."""
    path = Path(file_path).resolve()
    return path.as_uri()


def uri_to_path(uri: str) -> str:
    """Convert a file:// URI to a normalized filesystem path."""
    if not uri.startswith("file://"):
        return uri
    parsed = urllib.parse.urlparse(uri)
    path = urllib.request.url2pathname(urllib.parse.unquote(parsed.path))
    if os.name == "nt" and path.startswith("\\") and len(path) > 2 and path[2] == ":":
        path = path[1:]
    return os.path.normpath(path)


def _render_hover_contents(contents: Any) -> str:
    """Normalize LSP hover contents into markdown text."""
    if contents is None:
        return ""
    if isinstance(contents, str):
        return contents
    if isinstance(contents, Sequence) and not isinstance(contents, (bytes, bytearray)):
        return "\n\n".join(_render_hover_contents(item) for item in contents if item)
    if isinstance(contents, Mapping):
        # MarkedString object: { language: string, value: string }
        if "language" in contents and "value" in contents:
            lang = contents.get("language", "")
            val = contents.get("value", "")
            return f"```{lang}\n{val}\n```"
        # MarkupContent: { kind: 'markdown' | 'plaintext', value: string }
        if "value" in contents and isinstance(contents["value"], str):
            return contents["value"]
    return str(contents)


# ============================================================================
# Base Protocol Framing (encode/decode)
# ============================================================================

def encode_message(message: dict[str, Any]) -> bytes:
    """Encode a JSON-RPC message as a Content-Length framed byte sequence."""
    body = json.dumps(message, ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    return header + body


class MessageDecoder:
    """Streaming decoder for Content-Length framed JSON-RPC messages."""

    def __init__(
        self,
        max_message_bytes: int = MAX_MESSAGE_BYTES,
        max_header_bytes: int = MAX_HEADER_BYTES,
    ) -> None:
        self.max_message_bytes = max_message_bytes
        self.max_header_bytes = max_header_bytes
        self._buffer = bytearray()

    def push(self, chunk: bytes) -> list[dict[str, Any]]:
        """Append raw incoming bytes and yield all newly completed JSON-RPC messages."""
        self._buffer.extend(chunk)
        messages: list[dict[str, Any]] = []
        while True:
            msg = self._next()
            if msg is None:
                break
            messages.append(msg)
        return messages

    def _next(self) -> dict[str, Any] | None:
        sep_idx = self._buffer.find(HEADER_SEPARATOR)
        if sep_idx < 0:
            if len(self._buffer) > self.max_header_bytes:
                raise LspError(f"LSP header exceeded {self.max_header_bytes} bytes without terminator")
            return None
        if sep_idx > self.max_header_bytes:
            raise LspError(f"LSP header exceeded {self.max_header_bytes} bytes")

        header_text = self._buffer[:sep_idx].decode("ascii", errors="replace")
        content_length = self._parse_content_length(header_text)
        if content_length > self.max_message_bytes:
            raise LspError(
                f"LSP message length {content_length} exceeds limit {self.max_message_bytes}"
            )

        body_start = sep_idx + len(HEADER_SEPARATOR)
        body_end = body_start + content_length
        if len(self._buffer) < body_end:
            return None

        body_bytes = bytes(self._buffer[body_start:body_end])
        del self._buffer[:body_end]

        try:
            return json.loads(body_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise LspError(f"LSP message body was not valid JSON: {e}") from e

    @staticmethod
    def _parse_content_length(header_text: str) -> int:
        for line in header_text.split("\r\n"):
            colon = line.find(":")
            if colon < 0:
                continue
            name = line[:colon].strip().lower()
            if name == "content-length":
                val = line[colon + 1:].strip()
                try:
                    length = int(val)
                    if length < 0:
                        raise ValueError
                    return length
                except ValueError:
                    raise LspError(f"Invalid Content-Length header: {line!r}")
        raise LspError(f"LSP header block missing Content-Length: {header_text!r}")
