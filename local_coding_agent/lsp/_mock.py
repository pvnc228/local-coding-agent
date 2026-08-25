"""Mock Language Server for Testing & Verification."""

from __future__ import annotations

from typing import Any, BinaryIO, Mapping

from ._types import MessageDecoder, encode_message


class MockLspServer:
    """Mock LSP server implementing standard JSON-RPC over stdio streams."""

    def __init__(self) -> None:
        self.documents: dict[str, str] = {}
        self.shutdown_received = False

    def handle_message(self, message: Mapping[str, Any]) -> dict[str, Any] | None:
        """Process one incoming message and return an optional response dict."""
        msg_id = message.get("id")
        method = message.get("method")
        params = message.get("params") or {}

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "capabilities": {
                        "definitionProvider": True,
                        "referencesProvider": True,
                        "hoverProvider": True,
                        "documentSymbolProvider": True,
                        "textDocumentSync": 1,
                    }
                },
            }

        if method == "initialized":
            return None

        if method == "textDocument/didOpen":
            doc = params.get("textDocument", {})
            uri = doc.get("uri", "")
            text = doc.get("text", "")
            self.documents[uri] = text
            return None

        if method == "textDocument/didClose":
            doc = params.get("textDocument", {})
            uri = doc.get("uri", "")
            self.documents.pop(uri, None)
            return None

        if method == "textDocument/definition":
            uri = params.get("textDocument", {}).get("uri", "")
            pos = params.get("position", {})
            line = pos.get("line", 0)
            char = pos.get("character", 0)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": [
                    {
                        "uri": uri,
                        "range": {
                            "start": {"line": line, "character": char},
                            "end": {"line": line, "character": char + 5},
                        },
                    }
                ],
            }

        if method == "textDocument/references":
            uri = params.get("textDocument", {}).get("uri", "")
            pos = params.get("position", {})
            line = pos.get("line", 0)
            char = pos.get("character", 0)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": [
                    {
                        "uri": uri,
                        "range": {
                            "start": {"line": line, "character": char},
                            "end": {"line": line, "character": char + 5},
                        },
                    },
                    {
                        "uri": uri,
                        "range": {
                            "start": {"line": line + 2, "character": 0},
                            "end": {"line": line + 2, "character": 5},
                        },
                    },
                ],
            }

        if method == "textDocument/hover":
            pos = params.get("position", {})
            line = pos.get("line", 0)
            char = pos.get("character", 0)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "contents": {
                        "kind": "markdown",
                        "value": f"```python\ndef mock_symbol() -> None\n```\nMock documentation at L{line+1}:C{char+1}.",
                    },
                    "range": {
                        "start": {"line": line, "character": char},
                        "end": {"line": line, "character": char + 5},
                    },
                },
            }

        if method == "textDocument/documentSymbol":
            uri = params.get("textDocument", {}).get("uri", "")
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": [
                    {
                        "name": "MockClass",
                        "kind": 5,
                        "range": {
                            "start": {"line": 0, "character": 0},
                            "end": {"line": 10, "character": 0},
                        },
                        "selectionRange": {
                            "start": {"line": 0, "character": 6},
                            "end": {"line": 0, "character": 15},
                        },
                        "children": [
                            {
                                "name": "mock_method",
                                "kind": 6,
                                "range": {
                                    "start": {"line": 1, "character": 4},
                                    "end": {"line": 3, "character": 0},
                                },
                                "selectionRange": {
                                    "start": {"line": 1, "character": 8},
                                    "end": {"line": 1, "character": 19},
                                },
                            }
                        ],
                    },
                    {
                        "name": "mock_function",
                        "kind": 12,
                        "range": {
                            "start": {"line": 12, "character": 0},
                            "end": {"line": 15, "character": 0},
                        },
                        "selectionRange": {
                            "start": {"line": 12, "character": 4},
                            "end": {"line": 12, "character": 17},
                        },
                    },
                ],
            }

        if method == "shutdown":
            self.shutdown_received = True
            return {"jsonrpc": "2.0", "id": msg_id, "result": None}

        if method == "exit":
            return None

        # Fallback for unknown methods
        if msg_id is not None:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }
        return None

    def serve(self, input_stream: BinaryIO, output_stream: BinaryIO) -> None:
        """Run standard stdio server loop reading from input_stream and writing to output_stream."""
        decoder = MessageDecoder()
        read_fn = getattr(input_stream, "read1", input_stream.read)
        while True:
            try:
                chunk = read_fn(4096)
            except Exception:
                break
            if not chunk:
                break
            messages = decoder.push(chunk)
            for msg in messages:
                resp = self.handle_message(msg)
                if resp is not None:
                    data = encode_message(resp)
                    output_stream.write(data)
                    output_stream.flush()
                if msg.get("method") == "exit":
                    return
