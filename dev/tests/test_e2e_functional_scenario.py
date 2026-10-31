"""
End-to-End Real-World Scenario Test Suite for Local Coding Agent.
Exercises Milestones R24 through R30 in a unified, realistic developer workflow:
1. Ripgrep symbol discovery & Fast Search (R24)
2. Filesystem Observation Policy Gate (R24)
3. Tool Output Spill Store for verbose traces (R24)
4. LSP Code Intelligence (symbols, definitions, references, hover) (R25)
5. Persistent PTY Terminal interactive session (R26)
6. Plan Mode State Machine, Structured Questions, and Dynamic Todo Checklist (R27)
7. Event-Sourced Session Log & SQLite FTS5 Full-Text Search Index (R28)
8. Universal Agent Client Protocol (ACP) JSON-RPC Server & Tool Dispatch (R29)
9. Continuable Background Subagents with Mailbox Routing & Lifecycle Hook Bridges (R30)
"""

import io
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from local_coding_agent.acp_server import PROTOCOL_VERSION, AcpCodec, AcpServer
from local_coding_agent.hooks import ClaudeCodeHookAdapter, CodexHookAdapter, HookBridge, HookDecision
from local_coding_agent.lsp import LspManager
from local_coding_agent.observation_policy import FsObservationError, FsObservationGate
from local_coding_agent.plan_mode import (
    AskUserQuestionTool,
    PlanArtifact,
    PlanModeController,
    PlanModeError,
    PlanModePolicyError,
    PlanModeState,
    QuestionItem,
    TodoChecklist,
)
from local_coding_agent.ripgrep import ripgrep_search
from local_coding_agent.session_events import (
    ModelTurnEvent,
    SessionCompletedEvent,
    SessionCreatedEvent,
    SessionLog,
    ToolCallEvent,
    ToolResultEvent,
    UserPromptEvent,
    derive_messages,
    fork_session,
)
from local_coding_agent.session_query import SessionQueryEngine
from local_coding_agent.spill import SpillStore, read_spill
from local_coding_agent.subagent import MailboxMessage, SubagentCoordinator
from local_coding_agent.terminal import TerminalManager, execute_terminal_tool


class TestE2EFunctionalScenario(unittest.TestCase):
    """End-to-end functional test executing a real-world coding task across all new subsystems."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name).resolve()

        # Create a multi-module workspace representing a math library under development
        self.src_dir = self.workspace / "src"
        self.tests_dir = self.workspace / "tests"
        self.src_dir.mkdir(parents=True, exist_ok=True)
        self.tests_dir.mkdir(parents=True, exist_ok=True)

        self.matrix_file = self.src_dir / "matrix.py"
        self.matrix_file.write_text(
            '"""Matrix mathematics module."""\n\n'
            'class Matrix2D:\n'
            '    def __init__(self, a: float, b: float, c: float, d: float):\n'
            '        self.a = a\n'
            '        self.b = b\n'
            '        self.c = c\n'
            '        self.d = d\n\n'
            '    def determinant(self) -> float:\n'
            '        return self.a * self.d - self.b * self.c\n\n'
            '    def inverse(self) -> \'Matrix2D\':\n'
            '        det = self.determinant()\n'
            '        if det == 0:\n'
            '            raise ValueError("Matrix is singular")\n'
            '        return Matrix2D(self.d / det, -self.b / det, -self.c / det, self.a / det)\n\n'
            'def multiply_matrices(m1: Matrix2D, m2: Matrix2D) -> Matrix2D:\n'
            '    return Matrix2D(\n'
            '        m1.a * m2.a + m1.b * m2.c,\n'
            '        m1.a * m2.b + m1.b * m2.d,\n'
            '        m1.c * m2.a + m1.d * m2.c,\n'
            '        m1.c * m2.b + m1.d * m2.d,\n'
            '    )\n',
            encoding="utf-8",
        )

        self.test_file = self.tests_dir / "test_matrix.py"
        self.test_file.write_text(
            "import unittest\n"
            "from src.matrix import Matrix2D, multiply_matrices\n\n"
            "class TestMatrix(unittest.TestCase):\n"
            "    def test_determinant(self):\n"
            "        m = Matrix2D(1, 2, 3, 4)\n"
            "        self.assertEqual(m.determinant(), -2)\n",
            encoding="utf-8",
        )

        self.session_id = "sess-e2e-realworld-001"

    def tearDown(self):
        try:
            self.temp_dir.cleanup()
        except Exception:
            shutil.rmtree(self.temp_dir.name, ignore_errors=True)

    def test_complete_real_world_task_workflow(self):
        """Execute complete real-world task scenario combining R24-R30."""
        print("\n=== STARTING E2E REAL-WORLD TASK WORKFLOW ===")

        # Step 1: Ripgrep Discovery across repository workspace (R24)
        print("-> Step 1: Ripgrep search for Matrix2D and determinant...")
        matches = ripgrep_search("class Matrix2D", root=self.workspace)
        self.assertGreaterEqual(len(matches), 1)
        self.assertTrue(any("matrix.py" in m.file for m in matches))

        det_matches = ripgrep_search("def determinant", root=self.workspace)
        self.assertGreaterEqual(len(det_matches), 1)
        print(f"   Found {len(matches)} class match(es) and {len(det_matches)} method match(es).")

        # Step 2: Filesystem Observation Policy Enforcement (R24)
        print("-> Step 2: Testing FS Observation Policy Gate...")
        fs_gate = FsObservationGate()
        unobserved_file = self.workspace / "src" / "unobserved.py"
        unobserved_file.write_text("x = 100\n", encoding="utf-8")

        allowed, reason = fs_gate.verify_edit_intent(self.session_id, unobserved_file)
        self.assertFalse(allowed)
        self.assertIn("FS_NOT_OBSERVED", reason)

        content_hash = fs_gate.observe_file(self.session_id, self.matrix_file)
        self.assertTrue(fs_gate.is_observed(self.session_id, self.matrix_file))
        allowed, reason = fs_gate.verify_edit_intent(self.session_id, self.matrix_file)
        self.assertTrue(allowed)
        self.assertIsNone(reason)
        print(f"   Observation Gate verified: hash={content_hash[:12]}...")

        # Step 3: LSP Code Intelligence Exploration (R25)
        print("-> Step 3: Querying LSP symbols, definitions, and hover...")
        lsp = LspManager(workspace_root=self.workspace)
        symbols = lsp.document_symbols(self.matrix_file, workspace_root=self.workspace)
        symbol_names = [s.name for s in symbols]
        self.assertIn("Matrix2D", symbol_names)
        self.assertIn("multiply_matrices", symbol_names)

        defs = lsp.go_to_definition(self.matrix_file, line=2, character=8, workspace_root=self.workspace)
        self.assertGreaterEqual(len(defs), 1)

        hover_info = lsp.hover(self.matrix_file, line=2, character=8, workspace_root=self.workspace)
        self.assertIsNotNone(hover_info)
        self.assertIn("Matrix2D", hover_info.contents)
        print(f"   LSP verified: found symbols {symbol_names} and hover info.")

        # Step 4: Plan Mode, Structured Questions & Dynamic Checklist (R27)
        print("-> Step 4: Activating Plan Mode, Asking Questions & Managing Checklist...")
        plan_controller = PlanModeController()
        plan_controller.enter_plan_mode(goal="Refactor Matrix2D to support scalar multiplication and 3x3 extension")
        self.assertEqual(plan_controller.state, PlanModeState.EXPLORING)

        self.assertFalse(plan_controller.is_tool_allowed("propose_patch"))
        self.assertFalse(plan_controller.is_tool_allowed("apply"))
        self.assertTrue(plan_controller.is_tool_allowed("read_file"))
        self.assertTrue(plan_controller.is_tool_allowed("grep"))
        self.assertTrue(plan_controller.is_tool_allowed("lsp"))

        q_tool = AskUserQuestionTool(
            answer_provider=lambda q: ["Support scalar operations now, defer 3x3"]
        )
        q_results = q_tool.ask([
            {
                "question": "Should we support 3x3 matrices in this PR or only 2D scalar multiplication?",
                "options": [
                    "Support scalar operations now, defer 3x3",
                    "Implement full NxN generalized matrix class",
                ],
                "is_multi_select": False,
            }
        ])
        self.assertEqual(q_results[0]["selected"], ["Support scalar operations now, defer 3x3"])

        todo = TodoChecklist()
        todo.todo_write([
            {"id": "task-1", "content": "Explore existing Matrix2D methods", "status": "completed"},
            {"id": "task-2", "content": "Implement scalar multiplication", "status": "in_progress"},
            {"id": "task-3", "content": "Add unit tests for scalar multiplication", "status": "pending"},
        ])
        counts = todo.counts()
        self.assertEqual(counts["completed"], 1)
        self.assertEqual(counts["in_progress"], 1)
        self.assertEqual(counts["pending"], 1)

        plan_artifact = PlanArtifact(
            goal="Add scalar multiplication to Matrix2D with full unit tests",
            steps=[
                "Add __mul__ to Matrix2D supporting float and int scalars",
                "Add test_scalar_multiplication in test_matrix.py",
                "Run test runner to verify 100% pass",
            ],
            risks=["Type error when multiplying by incompatible types"],
            files_to_modify=["src/matrix.py", "tests/test_matrix.py"],
        )
        plan_controller.submit_plan(plan_artifact)
        self.assertEqual(plan_controller.state, PlanModeState.PLAN_READY)

        plan_controller.approve_plan()
        self.assertEqual(plan_controller.state, PlanModeState.APPROVED)
        self.assertTrue(plan_controller.is_tool_allowed("propose_patch"))
        print("   Plan Mode approved and checklist active.")

        # Step 5: Interactive Persistent PTY Terminal Session (R26)
        print("-> Step 5: Spawning Persistent Terminal & testing interactive commands...")
        term_manager = TerminalManager(workspace_root=self.workspace)
        term_session = term_manager.create_session("term-math-repl", cwd=self.workspace)

        res1 = execute_terminal_tool(
            term_manager,
            "terminal_send",
            {"session_id": "term-math-repl", "text": "python -c \"import sys; print('PYTHON_OK', sys.version_info.major)\"\n", "wait_ms": 1000},
        )
        self.assertTrue(res1.get("ok", False))
        self.assertIn("PYTHON_OK", res1.get("output", ""))

        sessions_list = term_manager.list_sessions()
        self.assertEqual(len(sessions_list), 1)
        self.assertEqual(sessions_list[0]["session_id"], "term-math-repl")
        term_manager.close_all()
        print("   Terminal session executed and closed cleanly.")

        # Step 6: Tool Output Spill Store for Verbose Test Traces (R24)
        print("-> Step 6: Testing Tool Output Spill Store on large trace...")
        spill_store = SpillStore(root_dir=self.workspace / ".local_agent" / "spill")
        large_test_output = "\n".join([f"Test trace line {i}: assert matrix.det() == {i * 2}" for i in range(2500)])

        spilled, preview_or_text, spill_ref = spill_store.maybe_spill(
            session_id=self.session_id,
            content=large_test_output,
            source_tool="run_tests",
            max_lines=500,
        )
        self.assertTrue(spilled)
        self.assertIsNotNone(spill_ref)
        self.assertTrue(spill_ref.locator.endswith(".txt"))
        self.assertIn(".local_agent/spill", spill_ref.locator)
        self.assertIn("[OUTPUT TRUNCATED & SPILLED TO STORE]", preview_or_text)

        paginated_content = spill_store.read_spill(spill_ref.locator, offset_line=100, limit_lines=5)
        self.assertIn("Test trace line 100", paginated_content)
        self.assertIn("Test trace line 104", paginated_content)
        print(f"   Spilled 2500 lines ({spill_ref.bytes} bytes) to locator {spill_ref.locator}")

        # Step 7: Continuable Background Subagents & Mailbox Routing (R30)
        print("-> Step 7: Spawning Background Subagent with Mailbox Communication...")
        coordinator = SubagentCoordinator(workspaces={"default": self.workspace})

        def child_task_worker(ctx):
            ctx.report("running", {"progress": "analyzing test coverage"})
            msgs = ctx.receive_messages(clear=True)
            for m in msgs:
                if m.get("content", {}).get("action") == "benchmark":
                    ctx.send_message("coordinator", {"benchmark_result": "10,000 ops/sec"})
            ctx.report("completed", {"result": "all invariants passed"})
            return {"status": "success"}

        child_id = coordinator.spawn_subagent(
            role="Math Verification Subagent",
            goal="Verify matrix inversion math properties",
            files=["src/matrix.py"],
            worker_loop=child_task_worker,
        )

        coordinator.send_message(child_id, {"action": "benchmark"}, sender_id="coordinator")
        time.sleep(0.3)

        reports = coordinator.get_reports(child_id)
        self.assertGreaterEqual(len(reports), 1)
        self.assertTrue(any(r.get("status") in ("running", "completed") for r in reports))
        coordinator.shutdown(timeout=1.0)
        print(f"   Subagent {child_id} completed task successfully.")

        # Step 8: Lifecycle Hook Bridges & Wire-Protocol Adapters (R30)
        print("-> Step 8: Intercepting tool executions via HookBridge & Codex/Claude adapters...")
        bridge = HookBridge()
        hook_logs = []

        @bridge.on_pre_tool_call()
        def audit_pre_tool(name, payload):
            hook_logs.append(f"PRE:{name}")
            return HookDecision(allowed=True, additional_context=[f"Observed pre-call {name}"])

        @bridge.on_post_tool_call()
        def audit_post_tool(name, payload):
            hook_logs.append(f"POST:{name}")
            return HookDecision(feedback=[f"Tool {name} executed OK"])

        pre_dec = bridge.trigger_pre_tool_call("lsp_definition", {"file": str(self.matrix_file)})
        post_dec = bridge.trigger_post_tool_call("lsp_definition", {"file": str(self.matrix_file)}, {"res": 1})
        self.assertTrue(pre_dec.allowed)
        self.assertEqual(hook_logs, ["PRE:lsp_definition", "POST:lsp_definition"])

        codex_adapter = CodexHookAdapter(bridge=bridge)
        claude_adapter = ClaudeCodeHookAdapter(bridge=bridge)
        codex_event = codex_adapter.format_pre_tool_use("grep", {"query": "Matrix2D"})
        claude_event = claude_adapter.format_pre_tool_use("grep", {"query": "Matrix2D"})
        self.assertEqual(codex_event["event"], "PreToolUse")
        self.assertEqual(claude_event["hookEventName"], "PreToolUse")
        print("   Hook Bridges verified across Codex and Claude Code protocols.")

        # Step 9: Event-Sourced Session Log & SQLite FTS5 Full-Text Search (R28)
        print("-> Step 9: Recording Immutable Session Events & SQLite FTS5 Indexing...")
        log_file = self.workspace / ".local_agent" / "sessions" / f"{self.session_id}.jsonl"
        log = SessionLog(self.session_id, log_path=log_file)

        log.append(SessionCreatedEvent(session_id=self.session_id, seq=0, timestamp="2026-08-21T00:00:00Z", metadata={"workspace_path": str(self.workspace), "profile": "balanced"}))
        log.append(UserPromptEvent(session_id=self.session_id, seq=1, timestamp="2026-08-21T00:00:01Z", content="Refactor matrix module and add scalar multiplication"))
        log.append(ModelTurnEvent(session_id=self.session_id, seq=2, timestamp="2026-08-21T00:00:02Z", content="I will search matrix.py using ripgrep", tool_calls=[{"id": "tc1", "name": "grep", "arguments": {"query": "Matrix2D"}}]))
        log.append(ToolResultEvent(session_id=self.session_id, seq=3, timestamp="2026-08-21T00:00:03Z", tool_call_id="tc1", tool_name="grep", result={"count": 1, "matches": [{"file": "src/matrix.py", "line": 3}]}))
        log.append(ModelTurnEvent(session_id=self.session_id, seq=4, timestamp="2026-08-21T00:00:04Z", content="I have verified matrix.py and prepared the plan."))
        log.append(SessionCompletedEvent(session_id=self.session_id, seq=5, timestamp="2026-08-21T00:00:05Z", status="success", summary="Matrix math successfully refactored"))

        derived = derive_messages(log.events)
        self.assertEqual(len(derived), 4)
        self.assertEqual(derived[0]["role"], "user")
        self.assertEqual(derived[1]["role"], "assistant")
        self.assertEqual(derived[2]["role"], "tool")
        self.assertEqual(derived[3]["role"], "assistant")

        db_path = self.workspace / ".local_agent" / "sessions.db"
        query_engine = SessionQueryEngine(db_path=db_path)
        query_engine.index_session_log(log)

        search_res = query_engine.search_events("scalar multiplication")
        self.assertGreaterEqual(len(search_res), 1)
        self.assertEqual(search_res[0]["session_id"], self.session_id)

        forked_log = fork_session(
            original_session_id=self.session_id,
            step_index=2,
            new_session_id="sess-e2e-forked-branch-002",
            storage_dir=self.workspace / ".local_agent" / "sessions",
            original_log=log,
        )
        self.assertEqual(forked_log.session_id, "sess-e2e-forked-branch-002")
        self.assertGreaterEqual(len(forked_log.events), 2)

        query_engine.close()
        print(f"   Session Log & FTS5 verified: {len(log.events)} events indexed, search returned {len(search_res)} hit(s).")

        # Step 10: Universal Agent Client Protocol (ACP) Server Validation (R29)
        print("-> Step 10: Testing ACP Server JSON-RPC 2.0 stdio stream...")
        server = AcpServer(default_workspace=self.workspace)

        init_req = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"client_info": {"name": "Zed-IDE"}, "capabilities": {}}}
        init_res = server.process_request(init_req)
        self.assertEqual(init_res["result"]["protocol_version"], PROTOCOL_VERSION)
        self.assertEqual(init_res["result"]["server_info"]["name"], "local-coding-agent-acp")

        tools_req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        tools_res = server.process_request(tools_req)
        tool_names = [t["name"] for t in tools_res["result"]["tools"]]
        self.assertIn("grep", tool_names)
        self.assertIn("lsp", tool_names)
        self.assertIn("spill_read", tool_names)

        call_req = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "grep", "arguments": {"query": "class Matrix2D", "paths": ["src/matrix.py"]}},
        }
        call_res = server.process_request(call_req)
        self.assertFalse(call_res["result"]["isError"])
        self.assertIn("class Matrix2D", call_res["result"]["content"][0]["text"])

        sess_req = {"jsonrpc": "2.0", "id": 4, "method": "session/new", "params": {"workspace_path": str(self.workspace), "profile": "fast"}}
        sess_res = server.process_request(sess_req)
        acp_sess_id = sess_res["result"]["session_id"]
        self.assertTrue(acp_sess_id.startswith("session_"))

        cancel_req = {"jsonrpc": "2.0", "id": 5, "method": "session/cancel", "params": {"session_id": acp_sess_id}}
        cancel_res = server.process_request(cancel_req)
        self.assertTrue(cancel_res["result"]["cancelled"])

        # Test Framing Codec format & parse roundtrip
        framed_bytes = AcpCodec.format_message(init_req, framing="content-length")
        parsed_msg, framing = AcpCodec.read_message(io.BytesIO(framed_bytes))
        self.assertEqual(parsed_msg, init_req)
        self.assertEqual(framing, "content-length")
        print("   ACP Server JSON-RPC 2.0 protocol interactions verified.")

        print("\n=== E2E REAL-WORLD TASK WORKFLOW COMPLETED SUCCESSFULLY! ===")


if __name__ == "__main__":
    unittest.main()
