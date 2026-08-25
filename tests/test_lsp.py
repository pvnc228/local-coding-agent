"""Tests for Generic LSP Stdio Code Intelligence Seam (R25)."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import pytest

from local_coding_agent.lsp import (
    DEFAULT_SERVER_CANDIDATES,
    EXTENSION_TO_LANGUAGE,
    FallbackLspEngine,
    LspClient,
    LspConnectionError,
    LspError,
    LspHoverResult,
    LspLocation,
    LspManager,
    LspPosition,
    LspRange,
    LspResponseError,
    LspSymbol,
    LspTimeoutError,
    MessageDecoder,
    MockLspServer,
    encode_message,
    path_to_uri,
    uri_to_path,
)


# ============================================================================
# 1. Framing Tests (encode_message & MessageDecoder)
# ============================================================================

def test_encode_message() -> None:
    msg = {"jsonrpc": "2.0", "id": 1, "method": "test"}
    encoded = encode_message(msg)
    assert encoded.startswith(b"Content-Length: ")
    assert b"\r\n\r\n" in encoded
    body_str = json.dumps(msg, ensure_ascii=False)
    expected_len = len(body_str.encode("utf-8"))
    assert encoded.startswith(f"Content-Length: {expected_len}\r\n\r\n".encode("ascii"))
    assert encoded.endswith(body_str.encode("utf-8"))


def test_message_decoder_single_message() -> None:
    decoder = MessageDecoder()
    msg = {"jsonrpc": "2.0", "id": 42, "result": {"value": "hello"}}
    data = encode_message(msg)
    decoded = decoder.push(data)
    assert len(decoded) == 1
    assert decoded[0] == msg


def test_message_decoder_chunked_and_multiple_messages() -> None:
    decoder = MessageDecoder()
    msg1 = {"jsonrpc": "2.0", "id": 1, "method": "msg1"}
    msg2 = {"jsonrpc": "2.0", "id": 2, "method": "msg2"}
    data = encode_message(msg1) + encode_message(msg2)

    # Push half of data
    half = len(data) // 2
    res1 = decoder.push(data[:half])
    assert len(res1) <= 1

    # Push remainder
    res2 = decoder.push(data[half:])
    all_msgs = res1 + res2
    assert len(all_msgs) == 2
    assert all_msgs[0] == msg1
    assert all_msgs[1] == msg2


def test_message_decoder_errors() -> None:
    # Header too large without terminator
    decoder = MessageDecoder(max_header_bytes=100)
    with pytest.raises(LspError, match="without terminator"):
        decoder.push(b"A" * 150)

    # Missing Content-Length header
    decoder2 = MessageDecoder()
    with pytest.raises(LspError, match="missing Content-Length"):
        decoder2.push(b"Content-Type: application/json\r\n\r\n{}")

    # Invalid Content-Length number
    decoder3 = MessageDecoder()
    with pytest.raises(LspError, match="Invalid Content-Length"):
        decoder3.push(b"Content-Length: not-a-number\r\n\r\n{}")

    # Message exceeds max_message_bytes
    decoder4 = MessageDecoder(max_message_bytes=10)
    with pytest.raises(LspError, match="exceeds limit"):
        decoder4.push(b"Content-Length: 100\r\n\r\n" + b"X" * 100)

    # Invalid JSON body
    decoder5 = MessageDecoder()
    with pytest.raises(LspError, match="not valid JSON"):
        decoder5.push(b"Content-Length: 5\r\n\r\n{bad}")


# ============================================================================
# 2. Data Models & URI Conversions
# ============================================================================

def test_lsp_position_and_range() -> None:
    pos1 = LspPosition(0, 5)
    pos2 = LspPosition(2, 10)
    rng = LspRange(pos1, pos2)

    assert pos1.to_dict() == {"line": 0, "character": 5}
    assert LspPosition.from_dict({"line": 0, "character": 5}) == pos1

    rng_dict = rng.to_dict()
    assert rng_dict == {
        "start": {"line": 0, "character": 5},
        "end": {"line": 2, "character": 10},
    }
    assert LspRange.from_dict(rng_dict) == rng


def test_uri_conversion(tmp_path: Path) -> None:
    test_file = tmp_path / "sample.py"
    test_file.write_text("print('hello')", encoding="utf-8")

    uri = path_to_uri(test_file)
    assert uri.startswith("file://")

    recovered_path = uri_to_path(uri)
    assert Path(recovered_path).resolve() == test_file.resolve()


def test_lsp_location_from_wire() -> None:
    # Standard Location
    wire_loc = {
        "uri": "file:///workspace/test.py",
        "range": {
            "start": {"line": 1, "character": 2},
            "end": {"line": 1, "character": 10},
        },
    }
    loc = LspLocation.from_wire(wire_loc)
    assert loc.uri == "file:///workspace/test.py"
    assert loc.range.start.line == 1
    assert loc.range.start.character == 2

    # LocationLink
    wire_link = {
        "targetUri": "file:///workspace/target.py",
        "targetSelectionRange": {
            "start": {"line": 5, "character": 0},
            "end": {"line": 5, "character": 8},
        },
    }
    loc_link = LspLocation.from_wire(wire_link)
    assert loc_link.uri == "file:///workspace/target.py"
    assert loc_link.range.start.line == 5


def test_lsp_hover_result_from_wire() -> None:
    # None
    assert LspHoverResult.from_wire(None) is None
    assert LspHoverResult.from_wire({}) is None

    # Plain string contents
    h1 = LspHoverResult.from_wire({"contents": "Simple doc"})
    assert h1 is not None
    assert h1.contents == "Simple doc"

    # MarkupContent
    h2 = LspHoverResult.from_wire({
        "contents": {"kind": "markdown", "value": "# Title\nDetails"},
        "range": {
            "start": {"line": 0, "character": 0},
            "end": {"line": 0, "character": 5},
        },
    })
    assert h2 is not None
    assert h2.contents == "# Title\nDetails"
    assert h2.range is not None and h2.range.start.line == 0

    # MarkedString object
    h3 = LspHoverResult.from_wire({
        "contents": {"language": "python", "value": "def foo() -> int"}
    })
    assert h3 is not None
    assert "```python\ndef foo() -> int\n```" in h3.contents

    # MarkedString array
    h4 = LspHoverResult.from_wire({
        "contents": [
            {"language": "python", "value": "def bar()"},
            "Bar docstring explanation",
        ]
    })
    assert h4 is not None
    assert "```python\ndef bar()\n```" in h4.contents
    assert "Bar docstring explanation" in h4.contents


def test_lsp_symbol_from_wire() -> None:
    wire_symbols = [
        {
            "name": "MyClass",
            "kind": 5,
            "range": {
                "start": {"line": 0, "character": 0},
                "end": {"line": 10, "character": 0},
            },
            "selectionRange": {
                "start": {"line": 0, "character": 6},
                "end": {"line": 0, "character": 13},
            },
            "children": [
                {
                    "name": "my_method",
                    "kind": 6,
                    "range": {
                        "start": {"line": 1, "character": 4},
                        "end": {"line": 3, "character": 0},
                    },
                }
            ],
        },
        {
            "name": "global_func",
            "kind": 12,
            "location": {
                "uri": "file:///workspace/funcs.py",
                "range": {
                    "start": {"line": 12, "character": 4},
                    "end": {"line": 12, "character": 15},
                },
            },
        },
    ]

    symbols = LspSymbol.from_wire(wire_symbols, file_uri="file:///workspace/main.py")
    assert len(symbols) == 3
    assert symbols[0].name == "MyClass"
    assert symbols[0].kind_name == "Class"
    assert symbols[1].name == "my_method"
    assert symbols[1].kind_name == "Method"
    assert symbols[2].name == "global_func"
    assert symbols[2].kind_name == "Function"


# ============================================================================
# 3. MockLspServer Tests
# ============================================================================

def test_mock_lsp_server_handlers() -> None:
    server = MockLspServer()

    # initialize
    init_res = server.handle_message({"id": 1, "method": "initialize", "params": {}})
    assert init_res is not None
    assert init_res["result"]["capabilities"]["definitionProvider"] is True

    # initialized (notification)
    assert server.handle_message({"method": "initialized"}) is None

    # didOpen
    doc_uri = "file:///workspace/test.py"
    server.handle_message({
        "method": "textDocument/didOpen",
        "params": {"textDocument": {"uri": doc_uri, "text": "def test(): pass"}},
    })
    assert server.documents[doc_uri] == "def test(): pass"

    # definition
    def_res = server.handle_message({
        "id": 2,
        "method": "textDocument/definition",
        "params": {"textDocument": {"uri": doc_uri}, "position": {"line": 0, "character": 4}},
    })
    assert def_res is not None
    assert len(def_res["result"]) == 1

    # references
    ref_res = server.handle_message({
        "id": 3,
        "method": "textDocument/references",
        "params": {"textDocument": {"uri": doc_uri}, "position": {"line": 0, "character": 4}},
    })
    assert ref_res is not None
    assert len(ref_res["result"]) == 2

    # hover
    hov_res = server.handle_message({
        "id": 4,
        "method": "textDocument/hover",
        "params": {"textDocument": {"uri": doc_uri}, "position": {"line": 0, "character": 4}},
    })
    assert hov_res is not None
    assert "mock_symbol" in hov_res["result"]["contents"]["value"]

    # documentSymbol
    sym_res = server.handle_message({
        "id": 5,
        "method": "textDocument/documentSymbol",
        "params": {"textDocument": {"uri": doc_uri}},
    })
    assert sym_res is not None
    assert len(sym_res["result"]) == 2

    # shutdown & exit
    shut_res = server.handle_message({"id": 6, "method": "shutdown"})
    assert shut_res is not None and shut_res["result"] is None
    assert server.shutdown_received is True

    assert server.handle_message({"method": "exit"}) is None

    # Unknown method
    err_res = server.handle_message({"id": 7, "method": "unknown/method"})
    assert err_res is not None
    assert "error" in err_res


def test_mock_lsp_server_serve_stream() -> None:
    server = MockLspServer()

    # Create input stream with initialize and exit
    req1 = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    req2 = {"jsonrpc": "2.0", "method": "exit"}
    input_bytes = encode_message(req1) + encode_message(req2)

    in_stream = io.BytesIO(input_bytes)
    out_stream = io.BytesIO()

    server.serve(in_stream, out_stream)
    out_bytes = out_stream.getvalue()

    decoder = MessageDecoder()
    responses = decoder.push(out_bytes)
    assert len(responses) == 1
    assert responses[0]["id"] == 1
    assert "capabilities" in responses[0]["result"]


# ============================================================================
# 4. LspClient Lifecycle with Mock Subprocess
# ============================================================================

def test_lsp_client_with_mock_server(tmp_path: Path) -> None:
    # Run a python one-liner running MockLspServer.serve
    script = (
        "import sys; from local_coding_agent.lsp import MockLspServer; "
        "MockLspServer().serve(sys.stdin.buffer, sys.stdout.buffer)"
    )
    cmd = [sys.executable, "-u", "-c", script]

    test_file = tmp_path / "sample.py"
    test_file.write_text("class Sample:\n    def run(self):\n        pass\n", encoding="utf-8")

    client = LspClient(cmd, workspace_root=tmp_path, timeout=5.0)
    with client:
        # initialize was run in __enter__
        assert "definitionProvider" in client.server_capabilities

        # did_open
        client.did_open(test_file)

        # definition
        defs = client.definition(test_file, line=1, character=8)
        assert len(defs) == 1
        assert defs[0].range.start.line == 1

        # references
        refs = client.references(test_file, line=1, character=8)
        assert len(refs) == 2

        # hover
        hov = client.hover(test_file, line=1, character=8)
        assert hov is not None
        assert "mock_symbol" in hov.contents

        # document symbols
        syms = client.document_symbols(test_file)
        assert len(syms) >= 2
        sym_names = {s.name for s in syms}
        assert "MockClass" in sym_names
        assert "mock_method" in sym_names

        # did_close
        client.did_close(test_file)


def test_lsp_client_error_responses(tmp_path: Path) -> None:
    script = (
        "import sys; from local_coding_agent.lsp import MockLspServer; "
        "MockLspServer().serve(sys.stdin.buffer, sys.stdout.buffer)"
    )
    cmd = [sys.executable, "-u", "-c", script]

    client = LspClient(cmd, workspace_root=tmp_path, timeout=5.0)
    client.start()
    try:
        with pytest.raises(LspResponseError, match="Method not found"):
            client.send_request("non_existent_method", {})
    finally:
        client.stop()


def test_lsp_client_timeout(tmp_path: Path) -> None:
    # A script that sleeps forever and ignores input
    script = "import time, sys; time.sleep(10)"
    cmd = [sys.executable, "-c", script]

    client = LspClient(cmd, workspace_root=tmp_path, timeout=0.3)
    client.start()
    try:
        with pytest.raises(LspTimeoutError, match="timed out"):
            client.send_request("any_method", {})
    finally:
        client.stop()


def test_lsp_client_spawn_failure() -> None:
    client = LspClient(["non_existent_binary_12345_xyz"], timeout=1.0)
    with pytest.raises(LspConnectionError, match="Failed to spawn"):
        client.start()


# ============================================================================
# 5. FallbackLspEngine Tests (Python AST & Regex)
# ============================================================================

def test_fallback_engine_python_ast(tmp_path: Path) -> None:
    py_code = '''"""Module docstring."""

GLOBAL_CONST = 100
app_name = "test_app"

class Calculator:
    """A calculator class."""

    def add(self, a: int, b: int) -> int:
        """Add two integers."""
        return a + b

def calculate_tax(amount: float) -> float:
    """Calculate tax for amount."""
    calc = Calculator()
    return calc.add(int(amount), 10)
'''
    py_file = tmp_path / "calc.py"
    py_file.write_text(py_code, encoding="utf-8")

    engine = FallbackLspEngine()

    # 1. Document Symbols
    symbols = engine.document_symbols(py_file)
    sym_map = {s.name: s for s in symbols}

    assert "GLOBAL_CONST" in sym_map
    assert sym_map["GLOBAL_CONST"].kind_name == "Constant"

    assert "app_name" in sym_map
    assert sym_map["app_name"].kind_name == "Variable"

    assert "Calculator" in sym_map
    assert sym_map["Calculator"].kind_name == "Class"

    assert "add" in sym_map
    assert sym_map["add"].kind_name == "Method"

    assert "calculate_tax" in sym_map
    assert sym_map["calculate_tax"].kind_name == "Function"

    # 2. Go To Definition
    # Cursor on 'Calculator' inside calculate_tax (line 14, col 12: 'calc = Calculator()')
    defs = engine.go_to_definition(py_file, line=14, character=12, workspace_root=tmp_path)
    assert len(defs) == 1
    assert defs[0].file_path == str(py_file)
    assert defs[0].range.start.line == 5  # class Calculator is at line 5 (0-based)

    # 3. Find References
    refs = engine.find_references(py_file, line=5, character=8, workspace_root=tmp_path)
    # Calculator appears at definition and at instantiation
    assert len(refs) >= 2

    # 4. Hover
    # Hover on Calculator
    hov_class = engine.hover(py_file, line=5, character=8, workspace_root=tmp_path)
    assert hov_class is not None
    assert "class Calculator" in hov_class.contents
    assert "A calculator class." in hov_class.contents

    # Hover on add method (line 8)
    hov_func = engine.hover(py_file, line=8, character=10, workspace_root=tmp_path)
    assert hov_func is not None
    assert "def add(self, a, b)" in hov_func.contents
    assert "Add two integers." in hov_func.contents


def test_fallback_engine_regex_polyglot(tmp_path: Path) -> None:
    # TypeScript file
    ts_code = '''
export class UserService {
    getUser(id: string): User {
        return { id };
    }
}

export function formatUser(name: string): string {
    return `User: ${name}`;
}
'''
    ts_file = tmp_path / "user.ts"
    ts_file.write_text(ts_code, encoding="utf-8")

    engine = FallbackLspEngine()
    symbols = engine.document_symbols(ts_file)
    names = {s.name for s in symbols}
    assert "UserService" in names
    assert "formatUser" in names

    # Hover on UserService
    hov = engine.hover(ts_file, line=1, character=15, workspace_root=tmp_path)
    assert hov is not None
    assert "UserService" in hov.contents


# ============================================================================
# 6. LspManager Tests
# ============================================================================

def test_lsp_manager_language_detection() -> None:
    mgr = LspManager()
    assert mgr.get_language_id("test.py") == "python"
    assert mgr.get_language_id("main.ts") == "typescript"
    assert mgr.get_language_id("index.js") == "javascript"
    assert mgr.get_language_id("lib.rs") == "rust"
    assert mgr.get_language_id("server.go") == "go"
    assert mgr.get_language_id("notes.txt") == "plaintext"


def test_lsp_manager_fallback_flow(tmp_path: Path) -> None:
    test_py = tmp_path / "app.py"
    test_py.write_text(
        "def main():\n    print('Running')\n\nif __name__ == '__main__':\n    main()\n",
        encoding="utf-8",
    )

    with LspManager(workspace_root=tmp_path, use_fallback_if_missing=True) as mgr:
        # Document symbols
        symbols = mgr.document_symbols(test_py)
        assert len(symbols) >= 1
        assert symbols[0].name == "main"

        # Definition of main at callsite (line 4, character 5)
        defs = mgr.go_to_definition(test_py, line=4, character=5)
        assert len(defs) == 1
        assert defs[0].range.start.line == 0

        # References
        refs = mgr.find_references(test_py, line=0, character=4)
        assert len(refs) >= 2

        # Hover
        hov = mgr.hover(test_py, line=0, character=4)
        assert hov is not None
        assert "def main()" in hov.contents
