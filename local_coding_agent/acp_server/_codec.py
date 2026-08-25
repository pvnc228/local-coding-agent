"""Stdio framing codec (Content-Length / JSONL)."""

from __future__ import annotations

import json
from typing import Any


class AcpCodec:
    """Header & line framing codec for JSON-RPC 2.0 stdio."""

    @staticmethod
    def read_message(stream: Any, max_bytes: int = 10 * 1024 * 1024) -> tuple[dict[str, Any] | None, str]:
        """Read a single JSON-RPC message from a binary or text stream.

        Returns:
            (message_dict, framing_type) where framing_type is "content-length" or "jsonl".
            Returns (None, "") on EOF.
        """
        while True:
            line = stream.readline()
            if not line:
                return None, ""
            if isinstance(line, bytes):
                line_str = line.decode("utf-8", errors="replace").strip()
            else:
                line_str = str(line).strip()
            if line_str:
                break

        # Check for Content-Length header
        if line_str.lower().startswith("content-length:"):
            try:
                parts = line_str.split(":", 1)
                if len(parts) < 2:
                    raise ValueError(f"Invalid Content-Length header: {line_str}")
                content_length = int(parts[1].strip())
            except ValueError:
                raise ValueError(f"Invalid Content-Length header: {line_str}")

            if content_length < 0:
                raise ValueError(f"Content-Length must be non-negative: {content_length}")
            if content_length > max_bytes:
                raise ValueError(f"Content-Length {content_length} exceeds max allowed {max_bytes} bytes")

            # Read remaining headers until empty line (bounded against header floods)
            header_count = 0
            while True:
                header_line = stream.readline()
                if not header_line:
                    break
                header_count += 1
                if header_count > 50:
                    raise ValueError("Too many header lines in request (max 50)")
                if isinstance(header_line, bytes):
                    h_str = header_line.decode("utf-8", errors="replace").strip()
                else:
                    h_str = str(header_line).strip()
                if not h_str:
                    break

            # Read exact content_length bytes
            body = stream.read(content_length)
            if isinstance(body, bytes):
                if len(body) < content_length:
                    raise ValueError(f"Unexpected EOF reading message body: expected {content_length} bytes, got {len(body)}")
                body_str = body.decode("utf-8", errors="replace")
            else:
                body_str = str(body)
                if len(body_str.encode("utf-8")) < content_length and len(body_str) < content_length:
                    raise ValueError(f"Unexpected EOF reading message body: expected {content_length} bytes")

            parsed = json.loads(body_str)
            if not isinstance(parsed, dict):
                raise ValueError("JSON-RPC message must be an object")
            return parsed, "content-length"
        else:
            # JSONL framing
            if len(line_str.encode("utf-8")) > max_bytes:
                raise ValueError(f"JSONL line exceeds max allowed {max_bytes} bytes")
            parsed = json.loads(line_str)
            if not isinstance(parsed, dict):
                raise ValueError("JSON-RPC message must be an object")
            return parsed, "jsonl"

    @staticmethod
    def format_message(message: dict[str, Any], framing: str = "jsonl") -> bytes:
        """Format a JSON-RPC message dictionary into framed bytes."""
        raw_json = json.dumps(message, ensure_ascii=False)
        encoded = raw_json.encode("utf-8")
        if framing == "content-length":
            header = f"Content-Length: {len(encoded)}\r\n\r\n".encode("ascii")
            return header + encoded
        else:
            return encoded + b"\n"
