"""Unit tests for Universal Agent Client Protocol (ACP) Server & Interop Gateway (R29)."""

from __future__ import annotations

import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any

from local_coding_agent.acp_server import (
    ACP_TOOLS,
    PROTOCOL_VERSION,
    SERVER_NAME,
    SERVER_VERSION,
    AcpCodec,
    AcpServer,
    AcpSession,
)
from local_coding_agent.spill import save_text


class FakeAcpModel:
    """Mock model client for testing ACP prompt turns."""

    def __init__(self, response_text: str = "Hello from mock local model!") -> None:
        self.response_text = response_text
        self.call_count = 0
        self.last_messages: list[dict[str, Any]] = []

    def chat(self, messages: list[dict[str, Any]], *, tools: Any = None) -> dict[str, Any]:
        self.call_count += 1
        self.last_messages = list(messages)
        return {
            "message": {
                "role": "assistant",
                "content": self.response_text,
            }
        }


class SlowBlockingModel:
    """Model that blocks until explicitly released or cancelled."""

    def __init__(self, delay_sec: float = 0.5) -> None:
        self.delay_sec = delay_sec
        self.started = threading.Event()

    def chat(self, messages: list[dict[str, Any]], *, tools: Any = None) -> dict[str, Any]:
        self.started.set()
        time.sleep(self.delay_sec)
        return {
            "message": {
                "role": "assistant",
                "content": "Slow response",
            }
        }


class AcpServerTests(unittest.TestCase):
    """Test suite for ACP Server and protocol operations."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name).resolve()
        # Create a sample file in workspace
        (self.workspace / "example.py").write_text(
            "def add(a: int, b: int) -> int:\n    return a + b\n\ndef multiply(x: int, y: int) -> int:\n    return x * y\n",
            encoding="utf-8",
        )
        self.fake_model = FakeAcpModel()
        self.server = AcpServer(
            default_workspace=self.workspace,
            default_profile="qwen2.5-1.5b",
            model_factory=lambda prof: self.fake_model,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # ------------------------------------------------------------------------
    # 1. Initialize Handshake & Ping
    # ------------------------------------------------------------------------

    def test_initialize_handshake(self) -> None:
        req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocol_version": PROTOCOL_VERSION,
                "client_info": {"name": "zed-editor", "version": "0.140.0"},
                "capabilities": {"streaming": True},
            },
        }
        res = self.server.process_request(req)
        self.assertIsNotNone(res)
        self.assertEqual(res["id"], 1)
        self.assertEqual(res["jsonrpc"], "2.0")

        result = res["result"]
        self.assertEqual(result["protocol_version"], PROTOCOL_VERSION)
        self.assertEqual(result["server_info"]["name"], SERVER_NAME)
        self.assertEqual(result["server_info"]["version"], SERVER_VERSION)
        self.assertTrue(result["capabilities"]["sessions"])
        self.assertTrue(result["capabilities"]["tools"])
        self.assertTrue(result["capabilities"]["streaming"])
        self.assertTrue(result["capabilities"]["cancellation"])

    def test_ping(self) -> None:
        req = {"jsonrpc": "2.0", "id": "ping-1", "method": "ping", "params": {}}
        res = self.server.process_request(req)
        self.assertIsNotNone(res)
        self.assertEqual(res["result"]["pong"], True)
        self.assertEqual(res["result"]["status"], "ok")

    # ------------------------------------------------------------------------
    # 2. Session Lifecycle & Prompting
    # ------------------------------------------------------------------------

    def test_session_new_and_load(self) -> None:
        # Create session
        req_new = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "session/new",
            "params": {
                "workspace_path": str(self.workspace),
                "profile": "qwen2.5-coder",
            },
        }
        res_new = self.server.process_request(req_new)
        self.assertIsNotNone(res_new)
        session_id = res_new["result"]["session_id"]
        self.assertTrue(session_id)
        self.assertEqual(res_new["result"]["profile"], "qwen2.5-coder")
        self.assertEqual(res_new["result"]["workspace_path"], str(self.workspace))

        # Load session
        req_load = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "session/load",
            "params": {"session_id": session_id},
        }
        res_load = self.server.process_request(req_load)
        self.assertIsNotNone(res_load)
        self.assertEqual(res_load["result"]["session_id"], session_id)
        self.assertEqual(res_load["result"]["history"], [])

    def test_session_prompt_execution(self) -> None:
        # Create session
        res_new = self.server.process_request({
            "jsonrpc": "2.0",
            "id": 10,
            "method": "session/new",
            "params": {"workspace_path": str(self.workspace)},
        })
        session_id = res_new["result"]["session_id"]

        # Prompt turn
        req_prompt = {
            "jsonrpc": "2.0",
            "id": 11,
            "method": "session/prompt",
            "params": {
                "session_id": session_id,
                "prompt": "How do I use add()?",
            },
        }
        res_prompt = self.server.process_request(req_prompt)
        self.assertIsNotNone(res_prompt)
        result = res_prompt["result"]
        self.assertEqual(result["stop_reason"], "end_turn")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["content"], "Hello from mock local model!")
        self.assertEqual(result["turn"], 1)

        # Check session history persisted
        res_load = self.server.process_request({
            "jsonrpc": "2.0",
            "id": 12,
            "method": "session/load",
            "params": {"session_id": session_id},
        })
        history = res_load["result"]["history"]
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["role"], "user")
        self.assertEqual(history[0]["content"], "How do I use add()?")
        self.assertEqual(history[1]["role"], "assistant")
        self.assertEqual(history[1]["content"], "Hello from mock local model!")

    def test_session_prompt_with_acp_content_blocks(self) -> None:
        res_new = self.server.process_request({
            "jsonrpc": "2.0",
            "id": 20,
            "method": "session/new",
            "params": {"workspace_path": str(self.workspace)},
        })
        session_id = res_new["result"]["session_id"]

        req_prompt = {
            "jsonrpc": "2.0",
            "id": 21,
            "method": "session/prompt",
            "params": {
                "sessionId": session_id,
                "prompt": [{"type": "text", "text": "First line"}, {"type": "text", "text": "Second line"}],
            },
        }
        res_prompt = self.server.process_request(req_prompt)
        self.assertIsNotNone(res_prompt)
        self.assertEqual(res_prompt["result"]["stop_reason"], "end_turn")
        self.assertEqual(self.fake_model.last_messages[-1]["content"], "First line\nSecond line")

    # ------------------------------------------------------------------------
    # 3. Tool Listing & Tool Invocation
    # ------------------------------------------------------------------------

    def test_tools_list(self) -> None:
        req = {"jsonrpc": "2.0", "id": 30, "method": "tools/list", "params": {}}
        res = self.server.process_request(req)
        self.assertIsNotNone(res)
        tools = res["result"]["tools"]
        tool_names = {t["name"] for t in tools}
        expected_tools = {
            "spill_read",
            "grep",
            "lsp",
            "skeletonize",
            "lint_patch",
            "read_file",
            "list_files",
            "run_tests",
            "propose_patch",
        }
        self.assertTrue(expected_tools.issubset(tool_names))

    def test_tools_call_read_and_list_files(self) -> None:
        # read_file
        req_read = {
            "jsonrpc": "2.0",
            "id": 31,
            "method": "tools/call",
            "params": {
                "name": "read_file",
                "arguments": {"path": "example.py"},
                "workspace": str(self.workspace),
            },
        }
        res_read = self.server.process_request(req_read)
        self.assertFalse(res_read["result"]["is_error"])
        self.assertIn("def add", res_read["result"]["content"][0]["text"])

        # list_files
        req_list = {
            "jsonrpc": "2.0",
            "id": 32,
            "method": "tools/call",
            "params": {
                "name": "list_files",
                "arguments": {"path": "."},
                "workspace": str(self.workspace),
            },
        }
        res_list = self.server.process_request(req_list)
        self.assertFalse(res_list["result"]["is_error"])
        self.assertIn("example.py", res_list["result"]["result"]["files"])

    def test_tools_call_grep(self) -> None:
        req_grep = {
            "jsonrpc": "2.0",
            "id": 33,
            "method": "tools/call",
            "params": {
                "name": "grep",
                "arguments": {"query": "multiply"},
                "workspace": str(self.workspace),
            },
        }
        res_grep = self.server.process_request(req_grep)
        self.assertFalse(res_grep["result"]["is_error"])
        self.assertGreaterEqual(res_grep["result"]["result"]["count"], 1)
        self.assertIn("multiply", res_grep["result"]["content"][0]["text"])

    def test_tools_call_skeletonize(self) -> None:
        req_skel = {
            "jsonrpc": "2.0",
            "id": 34,
            "method": "tools/call",
            "params": {
                "name": "skeletonize",
                "arguments": {"file": "example.py", "symbols": ["add"]},
                "workspace": str(self.workspace),
            },
        }
        res_skel = self.server.process_request(req_skel)
        self.assertFalse(res_skel["result"]["is_error"])
        skeleton = res_skel["result"]["result"]["skeleton"]
        self.assertIn("def add", skeleton)

    def test_tools_call_lint_patch(self) -> None:
        patch_content = (
            "--- a/example.py\n"
            "+++ b/example.py\n"
            "@@ -1,2 +1,2 @@\n"
            "-def add(a: int, b: int) -> int:\n"
            "+def add(a: int, b: int) -> int:\n"
            "     return a + b\n"
        )
        req_lint = {
            "jsonrpc": "2.0",
            "id": 35,
            "method": "tools/call",
            "params": {
                "name": "lint_patch",
                "arguments": {"patch": patch_content},
                "workspace": str(self.workspace),
            },
        }
        res_lint = self.server.process_request(req_lint)
        self.assertFalse(res_lint["result"]["is_error"])
        self.assertTrue(res_lint["result"]["result"]["valid"])

    def test_tools_call_spill_read(self) -> None:
        spill_ref = save_text(session_id="test_acp_session", content="Line 1\nLine 2\nLine 3\n")
        self.assertIsNotNone(spill_ref)
        req_spill = {
            "jsonrpc": "2.0",
            "id": 36,
            "method": "tools/call",
            "params": {
                "name": "spill_read",
                "arguments": {"locator": spill_ref.locator, "offset": 0, "limit": 2},
            },
        }
        res_spill = self.server.process_request(req_spill)
        self.assertFalse(res_spill["result"]["is_error"])
        self.assertIn("Line 1", res_spill["result"]["content"][0]["text"])

    def test_tools_path_traversal_defense(self) -> None:
        req_escape = {
            "jsonrpc": "2.0",
            "id": 37,
            "method": "tools/call",
            "params": {
                "name": "read_file",
                "arguments": {"path": "../../../etc/passwd"},
                "workspace": str(self.workspace),
            },
        }
        res_escape = self.server.process_request(req_escape)
        self.assertTrue(res_escape["result"]["is_error"])
        self.assertIn("escapes workspace boundary", res_escape["result"]["error"])

    # ------------------------------------------------------------------------
    # 4. Cancellation Handling
    # ------------------------------------------------------------------------

    def test_session_cancel_active_run(self) -> None:
        blocking_model = SlowBlockingModel(delay_sec=1.0)
        server = AcpServer(
            default_workspace=self.workspace,
            model_factory=lambda prof: blocking_model,
        )

        res_new = server.process_request({
            "jsonrpc": "2.0",
            "id": 40,
            "method": "session/new",
            "params": {"workspace_path": str(self.workspace)},
        })
        session_id = res_new["result"]["session_id"]

        prompt_result_holder = []

        def _run_prompt():
            res = server.process_request({
                "jsonrpc": "2.0",
                "id": 41,
                "method": "session/prompt",
                "params": {"session_id": session_id, "prompt": "Calculate universe"},
            })
            prompt_result_holder.append(res)

        t = threading.Thread(target=_run_prompt)
        t.start()

        # Wait until model execution starts
        blocking_model.started.wait(timeout=2.0)

        # Send cancellation
        res_cancel = server.process_request({
            "jsonrpc": "2.0",
            "id": 42,
            "method": "session/cancel",
            "params": {"session_id": session_id},
        })
        self.assertTrue(res_cancel["result"]["cancelled"])

        t.join(timeout=3.0)
        self.assertEqual(len(prompt_result_holder), 1)
        res_p = prompt_result_holder[0]
        self.assertEqual(res_p["result"]["stop_reason"], "cancelled")
        self.assertEqual(res_p["result"]["status"], "cancelled")

    # ------------------------------------------------------------------------
    # 5. Framing & Transport (JSONL and Content-Length)
    # ------------------------------------------------------------------------

    def test_codec_jsonl_framing(self) -> None:
        input_data = b'{"jsonrpc":"2.0","id":100,"method":"ping","params":{}}\n'
        stream = io.BytesIO(input_data)
        msg, framing = AcpCodec.read_message(stream)
        self.assertEqual(framing, "jsonl")
        self.assertEqual(msg["method"], "ping")
        self.assertEqual(msg["id"], 100)

        formatted = AcpCodec.format_message({"jsonrpc": "2.0", "id": 100, "result": "pong"}, framing="jsonl")
        self.assertTrue(formatted.endswith(b"\n"))
        self.assertIn(b'"pong"', formatted)

    def test_codec_content_length_framing(self) -> None:
        payload = json.dumps({"jsonrpc": "2.0", "id": 101, "method": "ping", "params": {}}).encode("utf-8")
        header = f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii")
        input_data = header + payload

        stream = io.BytesIO(input_data)
        msg, framing = AcpCodec.read_message(stream)
        self.assertEqual(framing, "content-length")
        self.assertEqual(msg["method"], "ping")
        self.assertEqual(msg["id"], 101)

        formatted = AcpCodec.format_message({"jsonrpc": "2.0", "id": 101, "result": "pong"}, framing="content-length")
        self.assertTrue(formatted.startswith(b"Content-Length: "))
        self.assertIn(b"\r\n\r\n", formatted)

    def test_stdio_server_stream_execution(self) -> None:
        # Build stream with initialize, session/new, session/prompt, and shutdown
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "session/new", "params": {"workspace_path": str(self.workspace), "session_id": "stdio_sess_1"}},
            {"jsonrpc": "2.0", "id": 3, "method": "session/prompt", "params": {"session_id": "stdio_sess_1", "prompt": "Hi", "stream": True}},
            {"jsonrpc": "2.0", "id": 4, "method": "shutdown", "params": {}},
        ]
        input_bytes = b"".join(json.dumps(r).encode("utf-8") + b"\n" for r in requests)
        in_stream = io.BytesIO(input_bytes)
        out_stream = io.BytesIO()

        self.server.serve(input_stream=in_stream, output_stream=out_stream)

        out_stream.seek(0)
        output_lines = [json.loads(line) for line in out_stream.read().decode("utf-8").splitlines() if line.strip()]

        # Responses for id=1, 2, notification, id=3, id=4
        resp_map = {msg.get("id"): msg for msg in output_lines if "id" in msg}
        self.assertIn(1, resp_map)
        self.assertIn(2, resp_map)
        self.assertIn(3, resp_map)
        self.assertIn(4, resp_map)
        self.assertEqual(resp_map[1]["result"]["server_info"]["name"], SERVER_NAME)
        self.assertEqual(resp_map[2]["result"]["session_id"], "stdio_sess_1")
        self.assertEqual(resp_map[3]["result"]["stop_reason"], "end_turn")
        self.assertEqual(resp_map[4]["result"]["status"], "shutdown_acknowledged")

        # Check streaming notification
        notifications = [msg for msg in output_lines if msg.get("method") == "session/update"]
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0]["params"]["sessionId"], "stdio_sess_1")

    # ------------------------------------------------------------------------
    # 6. Adversarial JSON-RPC 2.0 Compliance & Error Codes
    # ------------------------------------------------------------------------

    def test_adversarial_jsonrpc_parse_error(self) -> None:
        res = self.server.process_request("{invalid_json_payload:")
        self.assertIsNotNone(res)
        self.assertEqual(res["error"]["code"], -32700)
        self.assertIn("Parse error", res["error"]["message"])

    def test_adversarial_jsonrpc_invalid_request_primitives(self) -> None:
        for prim in [123, '"quoted_string"', True, [1, 2], "123", "true", "[1, 2]"]:
            res = self.server.process_request(prim)  # type: ignore
            self.assertIsNotNone(res)
            self.assertEqual(res["error"]["code"], -32600)
            self.assertIn("Message must be an object", res["error"]["message"])

    def test_adversarial_jsonrpc_version_check(self) -> None:
        req = {"jsonrpc": "1.0", "id": 99, "method": "ping", "params": {}}
        res = self.server.process_request(req)
        self.assertIsNotNone(res)
        self.assertEqual(res["error"]["code"], -32600)
        self.assertIn("protocol version", res["error"]["message"])

    def test_adversarial_jsonrpc_method_not_found(self) -> None:
        req = {"jsonrpc": "2.0", "id": 100, "method": "non_existent_method_xyz", "params": {}}
        res = self.server.process_request(req)
        self.assertIsNotNone(res)
        self.assertEqual(res["error"]["code"], -32601)
        self.assertIn("Method not found", res["error"]["message"])

    def test_adversarial_jsonrpc_invalid_params_type(self) -> None:
        req = {"jsonrpc": "2.0", "id": 101, "method": "ping", "params": 42}
        res = self.server.process_request(req)
        self.assertIsNotNone(res)
        self.assertEqual(res["error"]["code"], -32602)
        self.assertIn("'params' must be an object or array", res["error"]["message"])

    def test_adversarial_jsonrpc_notification_error_suppression(self) -> None:
        # Notifications (no id) should never return error responses to client per JSON-RPC 2.0
        res_unknown = self.server.process_request({"jsonrpc": "2.0", "method": "unknown_notif", "params": {}})
        self.assertIsNone(res_unknown)

        res_bad_params = self.server.process_request({"jsonrpc": "2.0", "method": "ping", "params": 42})
        self.assertIsNone(res_bad_params)

        res_missing_meth = self.server.process_request({"jsonrpc": "2.0", "params": {}})
        self.assertIsNone(res_missing_meth)

    # ------------------------------------------------------------------------
    # 7. Adversarial Framing Decoder Resilience
    # ------------------------------------------------------------------------

    def test_adversarial_framing_negative_content_length(self) -> None:
        stream = io.BytesIO(b"Content-Length: -10\r\n\r\n{}")
        with self.assertRaises(ValueError) as ctx:
            AcpCodec.read_message(stream)
        self.assertIn("non-negative", str(ctx.exception))

    def test_adversarial_framing_excessive_content_length(self) -> None:
        stream = io.BytesIO(b"Content-Length: 99999999\r\n\r\n{}")
        with self.assertRaises(ValueError) as ctx:
            AcpCodec.read_message(stream, max_bytes=1024)
        self.assertIn("exceeds max allowed", str(ctx.exception))

    def test_adversarial_framing_header_bomb(self) -> None:
        flood = b"".join(f"X-Header-{i}: value\r\n".encode("ascii") for i in range(100))
        stream = io.BytesIO(b"Content-Length: 10\r\n" + flood + b"\r\n{}")
        with self.assertRaises(ValueError) as ctx:
            AcpCodec.read_message(stream)
        self.assertIn("Too many header lines", str(ctx.exception))

    def test_adversarial_framing_premature_eof(self) -> None:
        # Declares 100 bytes but only sends 5
        stream = io.BytesIO(b"Content-Length: 100\r\n\r\n12345")
        with self.assertRaises(ValueError) as ctx:
            AcpCodec.read_message(stream)
        self.assertIn("Unexpected EOF", str(ctx.exception))

    def test_adversarial_mixed_framing_stream(self) -> None:
        # Stream contains: JSONL ping -> Content-Length ping -> JSONL ping
        req1 = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}}).encode("utf-8") + b"\n"
        req2_body = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping", "params": {}}).encode("utf-8")
        req2 = f"Content-Length: {len(req2_body)}\r\n\r\n".encode("ascii") + req2_body
        req3 = json.dumps({"jsonrpc": "2.0", "id": 3, "method": "ping", "params": {}}).encode("utf-8") + b"\n"

        in_stream = io.BytesIO(req1 + req2 + req3)
        out_stream = io.BytesIO()

        self.server.serve(input_stream=in_stream, output_stream=out_stream)

        out_bytes = out_stream.getvalue()
        self.assertTrue(len(out_bytes) > 0)
        self.assertIn(b'"pong": true', out_bytes)

    # ------------------------------------------------------------------------
    # 8. Adversarial Tool Execution & Path Traversal Defense
    # ------------------------------------------------------------------------

    def test_adversarial_skeletonize_path_escape(self) -> None:
        req = {
            "jsonrpc": "2.0",
            "id": 201,
            "method": "tools/call",
            "params": {
                "name": "skeletonize",
                "arguments": {"file": "../../../outside.py"},
                "workspace": str(self.workspace),
            },
        }
        res = self.server.process_request(req)
        self.assertTrue(res["result"]["is_error"])
        self.assertIn("escapes workspace boundary", res["result"]["error"])

    def test_adversarial_lsp_path_escape(self) -> None:
        req = {
            "jsonrpc": "2.0",
            "id": 202,
            "method": "tools/call",
            "params": {
                "name": "lsp",
                "arguments": {"operation": "definition", "file": "../../outside.py"},
                "workspace": str(self.workspace),
            },
        }
        res = self.server.process_request(req)
        self.assertTrue(res["result"]["is_error"])
        self.assertIn("escapes workspace boundary", res["result"]["error"])

    def test_adversarial_grep_path_escape(self) -> None:
        req = {
            "jsonrpc": "2.0",
            "id": 203,
            "method": "tools/call",
            "params": {
                "name": "grep",
                "arguments": {"query": "secret", "paths": ["../../../*"]},
                "workspace": str(self.workspace),
            },
        }
        res = self.server.process_request(req)
        self.assertTrue(res["result"]["is_error"])
        self.assertIn("escapes workspace boundary", res["result"]["error"])

    def test_adversarial_lint_patch_path_escape(self) -> None:
        malicious_patch = (
            "--- a/../../outside.py\n"
            "+++ b/../../outside.py\n"
            "@@ -1,1 +1,1 @@\n"
            "-x = 1\n"
            "+x = 2\n"
        )
        req = {
            "jsonrpc": "2.0",
            "id": 204,
            "method": "tools/call",
            "params": {
                "name": "lint_patch",
                "arguments": {"patch": malicious_patch},
                "workspace": str(self.workspace),
            },
        }
        res = self.server.process_request(req)
        # Returns invalid lint report or error blocking escape
        self.assertFalse(res["result"]["result"]["valid"])
        self.assertTrue(any("outside.py" in p or "недопустимый путь" in p for p in res["result"]["result"]["prescriptions"]))

    # ------------------------------------------------------------------------
    # 9. Adversarial Cancellation & Concurrency Resilience
    # ------------------------------------------------------------------------

    def test_adversarial_multi_turn_cancellation_recovery(self) -> None:
        blocking_model = SlowBlockingModel(delay_sec=0.8)
        server = AcpServer(
            default_workspace=self.workspace,
            model_factory=lambda prof: blocking_model,
        )

        res_new = server.process_request({
            "jsonrpc": "2.0",
            "id": 301,
            "method": "session/new",
            "params": {"workspace_path": str(self.workspace)},
        })
        session_id = res_new["result"]["session_id"]

        # Turn 1: Cancelled
        turn1_res = []
        def _run_turn1():
            turn1_res.append(server.process_request({
                "jsonrpc": "2.0",
                "id": 302,
                "method": "session/prompt",
                "params": {"session_id": session_id, "prompt": "Long calculation"},
            }))

        t1 = threading.Thread(target=_run_turn1)
        t1.start()
        blocking_model.started.wait(timeout=2.0)

        # Cancel turn 1
        res_cancel = server.process_request({
            "jsonrpc": "2.0",
            "id": 303,
            "method": "session/cancel",
            "params": {"session_id": session_id},
        })
        self.assertTrue(res_cancel["result"]["cancelled"])
        t1.join(timeout=3.0)
        self.assertEqual(turn1_res[0]["result"]["stop_reason"], "cancelled")

        # Turn 2: Fresh prompt succeeds cleanly without leftover cancel flag
        server.model_factory = lambda prof: FakeAcpModel(response_text="Clean next turn")
        res_turn2 = server.process_request({
            "jsonrpc": "2.0",
            "id": 304,
            "method": "session/prompt",
            "params": {"session_id": session_id, "prompt": "Follow-up question"},
        })
        self.assertEqual(res_turn2["result"]["stop_reason"], "end_turn")
        self.assertEqual(res_turn2["result"]["content"], "Clean next turn")


if __name__ == "__main__":
    unittest.main()
