"""Regression tests for bounded HTTP request framing in the desktop server."""

from __future__ import annotations

import json
import socket
import time
from pathlib import Path

import pytest

from local_coding_agent.desktop.server import DesktopRequestHandler, DesktopServer


_MAX_REQUEST_BODY = 8 * 1024 * 1024


def _raw_post(server: DesktopServer, path: str, headers: dict[str, str], body: bytes = b"") -> bytes:
    host, port = "127.0.0.1", server.actual_port
    request_headers = {
        "Host": f"{host}:{port}",
        "Connection": "close",
        **headers,
    }
    wire = [f"POST {path} HTTP/1.1\r\n".encode("ascii")]
    wire.extend(f"{name}: {value}\r\n".encode("ascii") for name, value in request_headers.items())
    wire.extend((b"\r\n", body))

    return _raw_wire(server, b"".join(wire))


def _raw_wire(server: DesktopServer, wire: bytes) -> bytes:
    host, port = "127.0.0.1", server.actual_port

    with socket.create_connection((host, port), timeout=3.0) as connection:
        connection.sendall(wire)
        connection.shutdown(socket.SHUT_WR)
        chunks: list[bytes] = []
        while True:
            chunk = connection.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
    return b"".join(chunks)


def _raw_wire_with_eof(server: DesktopServer, wire: bytes) -> tuple[bytes, bool]:
    host, port = "127.0.0.1", server.actual_port
    with socket.create_connection((host, port), timeout=3.0) as connection:
        connection.sendall(wire)
        connection.shutdown(socket.SHUT_WR)
        chunks: list[bytes] = []
        while True:
            chunk = connection.recv(4096)
            if not chunk:
                return b"".join(chunks), True
            chunks.append(chunk)


def _status(response: bytes) -> int:
    return int(response.split(b" ", 2)[1])


def _json_body(response: bytes) -> dict:
    return json.loads(response.split(b"\r\n\r\n", 1)[1].decode("utf-8"))


@pytest.mark.parametrize("content_length", ["not-a-number", "-1", ""])
def test_malformed_or_negative_content_length_returns_400_without_mutation(tmp_path: Path, content_length: str):
    with DesktopServer(workspace=tmp_path) as server:
        headers = {
            "Content-Type": "application/json",
            "X-Desktop-Token": server.mutation_token,
        }
        if content_length:
            headers["Content-Length"] = content_length
        else:
            headers["Content-Length"] = ""
        response = _raw_post(server, "/api/sessions", headers, b"{}");

        assert _status(response) == 400
        assert _json_body(response)["status"] == "rejected"
        assert server.load_sessions() == []


def test_oversized_content_length_returns_413_without_reading_or_mutating(tmp_path: Path):
    with DesktopServer(workspace=tmp_path) as server:
        response = _raw_post(
            server,
            "/api/sessions",
            {
                "Content-Type": "application/json",
                "Content-Length": str(_MAX_REQUEST_BODY + 1),
                "X-Desktop-Token": server.mutation_token,
            },
            b'{"id":"must-not-persist"}',
        )

        assert _status(response) == 413
        assert _json_body(response)["status"] == "rejected"
        assert server.load_sessions() == []


def test_partial_content_length_returns_400_without_mutation(tmp_path: Path):
    body = b'{"id":"partial"}'
    with DesktopServer(workspace=tmp_path) as server:
        response = _raw_post(
            server,
            "/api/sessions",
            {
                "Content-Type": "application/json",
                "Content-Length": str(len(body) + 10),
                "X-Desktop-Token": server.mutation_token,
            },
            body,
        )

        assert _status(response) == 400
        assert _json_body(response)["status"] == "rejected"
        assert server.load_sessions() == []


def test_partial_content_length_idle_timeout_is_bounded(tmp_path: Path):
    host = "127.0.0.1"
    with DesktopServer(workspace=tmp_path) as server:
        connection = socket.create_connection((host, server.actual_port), timeout=3.0)
        try:
            request = (
                f"POST /api/sessions HTTP/1.1\r\n"
                f"Host: {host}:{server.actual_port}\r\n"
                "Connection: close\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: 32\r\n"
                f"X-Desktop-Token: {server.mutation_token}\r\n\r\n"
            ).encode("ascii")
            connection.sendall(request)
            started = time.monotonic()
            response = connection.recv(4096)
            elapsed = time.monotonic() - started
        finally:
            connection.close()

        assert elapsed < 2.5
        assert _status(response) == 400
        assert server.load_sessions() == []


def test_duplicate_content_length_returns_400_and_closes_connection(tmp_path: Path):
    with DesktopServer(workspace=tmp_path) as server:
        request = (
            f"POST /api/sessions HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{server.actual_port}\r\n"
            "Connection: keep-alive\r\n"
            "Content-Type: application/json\r\n"
            "Content-Length: 2\r\n"
            "Content-Length: 2\r\n"
            f"X-Desktop-Token: {server.mutation_token}\r\n\r\n{{}}"
        ).encode("ascii")
        response, reached_eof = _raw_wire_with_eof(server, request)

        assert _status(response) == 400
        assert b"connection: close" in response.lower()
        assert reached_eof
        assert server.load_sessions() == []


def test_transfer_encoding_returns_400_and_closes_connection(tmp_path: Path):
    with DesktopServer(workspace=tmp_path) as server:
        request = (
            f"POST /api/sessions HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{server.actual_port}\r\n"
            "Connection: keep-alive\r\n"
            "Transfer-Encoding: chunked\r\n"
            "Content-Length: 2\r\n"
            f"X-Desktop-Token: {server.mutation_token}\r\n\r\n{{}}"
        ).encode("ascii")
        response, reached_eof = _raw_wire_with_eof(server, request)

        assert _status(response) == 400
        assert b"connection: close" in response.lower()
        assert reached_eof
        assert server.load_sessions() == []


def test_rejected_body_drain_is_capped(tmp_path: Path):
    class _Reader:
        def __init__(self) -> None:
            self.sizes: list[int] = []

        def read(self, size: int) -> bytes:
            self.sizes.append(size)
            return b"x" * size

    with DesktopServer(workspace=tmp_path) as server:
        handler = DesktopRequestHandler.__new__(DesktopRequestHandler)
        handler.server = server._httpd
        handler.headers = {"Content-Length": str(_MAX_REQUEST_BODY)}
        handler.rfile = _Reader()
        handler._discard_request_body()

        assert handler.rfile.sizes
        assert sum(handler.rfile.sizes) <= 64 * 1024
