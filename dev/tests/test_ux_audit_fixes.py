"""Regression tests for the v0.8.1 UX-audit remediation pass."""

import json
import tempfile
import unittest
from pathlib import Path

from local_coding_agent.cli._handlers2 import _sanitize_session_id
from local_coding_agent.cli._input import load_task_file
from local_coding_agent.controller import Controller
from local_coding_agent.task import TaskEnvelope


class _FakeModel:
    def __init__(self, responses):
        self.responses = list(responses)

    def chat(self, messages, *, tools=None):
        return self.responses.pop(0)


def _tool_call(call_id, name, arguments):
    return {
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": call_id, "function": {"name": name, "arguments": arguments}}
            ],
        }
    }


class BomTaskFileTests(unittest.TestCase):
    def test_load_task_file_accepts_utf8_bom(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "task.json"
            payload = '{"id":"bom-1","goal":"fix","files":["a.py"],"checks":[],"acceptance":[]}'
            path.write_bytes(b"\xef\xbb\xbf" + payload.encode("utf-8"))

            task = load_task_file(path)

            self.assertEqual(task.id, "bom-1")


class SessionIdSanitizeTests(unittest.TestCase):
    def test_traversal_and_separators_are_neutered(self):
        from local_coding_agent.cli import _handlers2

        for evil in ("../../etc/passwd", "..\\..\\win", "a/b/c", "", "my session"):
            safe = _sanitize_session_id(evil)
            self.assertNotIn("/", safe)
            self.assertNotIn("\\", safe)
            # Whatever the id, the log path must stay inside the sessions dir.
            resolved = (_handlers2._SESSIONS_DIR / f"{safe}.jsonl").resolve()
            self.assertEqual(resolved.parent, _handlers2._SESSIONS_DIR.resolve())
        self.assertEqual(_sanitize_session_id("../../etc/passwd"), ".._.._etc_passwd")
        self.assertEqual(_sanitize_session_id("my session"), "my_session")

    def test_reasonable_ids_survive(self):
        self.assertEqual(_sanitize_session_id("chat-20260823-120000"), "chat-20260823-120000")


class SalvageLintGateTests(unittest.TestCase):
    def test_salvaged_patch_with_syntax_errors_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "allowed.py").write_text("VALUE = 42\n", encoding="utf-8")
            task = TaskEnvelope(id="salvage-lint", goal="fix value", files=("allowed.py",))
            broken_edit = {
                "status": "candidate",
                "summary": "broken",
                "edits": [
                    {"file": "allowed.py", "search": "VALUE = 42", "replace": "VALUE = (42"}
                ],
                "checks": [],
                "risks": [],
            }
            model = _FakeModel(
                [
                    _tool_call("c1", "propose_patch", {"edits": broken_edit["edits"]}),
                    # Keep issuing distinct tool calls so the turn loop runs to
                    # completion and the loop-end salvage path takes over.
                    _tool_call("c2", "read_file", {"path": "allowed.py"}),
                    _tool_call("c3", "search_text", {"query": "VALUE"}),
                    _tool_call("c4", "list_files", {"path": "."}),
                ]
            )

            result = Controller(model, str(workspace), max_turns=4).run(task)

            self.assertEqual(result["status"], "rejected")
            self.assertTrue(result["validation"]["lint_issues"])
            kinds = [r.get("kind") for r in result["risks"]]
            self.assertIn("semantic_lint_failed", kinds)


if __name__ == "__main__":
    unittest.main()
