import json
import tempfile
import unittest
from threading import Event, Timer
import sys
from pathlib import Path
from unittest.mock import patch

from local_coding_agent.controller import Controller, TOOL_DEFINITIONS
from local_coding_agent.repository_tools import BoundedRepositoryTools, ToolPolicyError
from local_coding_agent.task import TaskEnvelope


class FakeModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def chat(self, messages, *, tools=None):
        self.requests.append({"messages": list(messages), "tools": tools})
        return self.responses.pop(0)


class BlockingFakeModel:
    def __init__(self, block_event, release_event):
        self.requests = []
        self._block = block_event
        self._release = release_event

    def chat(self, messages, *, tools=None):
        self.requests.append({"messages": messages, "tools": tools})
        try:
            self._block.wait(timeout=10)
        finally:
            self._release.set()
        return {"message": {"role": "assistant", "content": "{}"}}


class ControllerTests(unittest.TestCase):
    def test_controller_correlates_tool_result_and_returns_structured_candidate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "allowed.py").write_text("VALUE = 42\n", encoding="utf-8")
            task = TaskEnvelope(
                id="controller-read",
                goal="проверить значение",
                files=("allowed.py",),
            )
            model = FakeModel(
                [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": {"path": "allowed.py"},
                                    },
                                }
                            ],
                        }
                    },
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "status": "candidate",
                                    "summary": "прочитан файл",
                                    "patch": "",
                                    "checks": [],
                                    "risks": [],
                                },
                                ensure_ascii=False,
                            ),
                        }
                    },
                ]
            )

            result = Controller(model, workspace).run(task)

        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["summary"], "прочитан файл")
        self.assertEqual(len(model.requests), 2)
        second_messages = model.requests[1]["messages"]
        self.assertEqual(second_messages[-1]["role"], "tool")
        self.assertEqual(second_messages[-1]["tool_name"], "read_file")
        self.assertIn("VALUE = 42", second_messages[-1]["content"])

    def test_controller_does_not_advertise_run_tests_without_allowlisted_checks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "allowed.py").write_text("VALUE = 42\n", encoding="utf-8")
            task = TaskEnvelope(id="no-check-tool", goal="прочитать файл", files=("allowed.py",))
            model = FakeModel(
                [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "status": "candidate",
                                    "summary": "готово",
                                    "patch": "",
                                    "checks": [],
                                    "risks": [],
                                }
                            ),
                        }
                    }
                ]
            )

            result = Controller(model, workspace).run(task)

        self.assertEqual(result["status"], "accepted")
        advertised = {
            definition["function"]["name"]
            for definition in model.requests[0]["tools"]
        }
        self.assertNotIn("run_tests", advertised)

    def test_propose_patch_tool_contract_requires_counted_complete_diff(self):
        definition = next(
            definition
            for definition in TOOL_DEFINITIONS
            if definition["function"]["name"] == "propose_patch"
        )
        description = definition["function"]["description"]
        properties = definition["function"]["parameters"]["properties"]

        self.assertIn("edits", properties)
        self.assertIn("hunk", description)
        self.assertIn("git", description)
        self.assertIn("real newlines", description)
        self.assertIn("search", description)
        self.assertNotIn("required", definition["function"]["parameters"])

    def test_controller_converts_json_tool_call_in_content_to_bounded_tool_call(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "allowed.py").write_text("VALUE = 42\n", encoding="utf-8")
            task = TaskEnvelope(id="content-tool", goal="прочитать файл", files=("allowed.py",))
            model = FakeModel(
                [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "name": "read_file",
                                    "arguments": {"path": "allowed.py"},
                                }
                            ),
                        }
                    },
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "status": "candidate",
                                    "summary": "прочитан файл",
                                    "patch": "",
                                    "checks": [],
                                    "risks": [],
                                }
                            ),
                        }
                    },
                ]
            )

            result = Controller(model, workspace).run(task)

        self.assertEqual(result["status"], "accepted")
        self.assertEqual(model.requests[1]["messages"][-1]["tool_name"], "read_file")
        self.assertIn("VALUE = 42", model.requests[1]["messages"][-1]["content"])

    def test_controller_fails_on_repeated_identical_tool_call(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "allowed.py").write_text("VALUE = 42\n", encoding="utf-8")
            task = TaskEnvelope(id="duplicate", goal="прочитать файл", files=("allowed.py",))
            call = {
                "message": {
                    "role": "assistant",
                    "tool_calls": [{"function": {"name": "read_file", "arguments": {"path": "allowed.py"}}}],
                }
            }
            model = FakeModel([call, call])

            result = Controller(model, workspace).run(task)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["kind"], "duplicate_tool_call")
        self.assertEqual(len(model.requests), 2)

    def test_controller_fails_on_repeated_list_files_with_default_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "allowed.py").write_text("VALUE = 42\n", encoding="utf-8")
            task = TaskEnvelope(id="duplicate-list-files", goal="прочитать файлы", files=("allowed.py",))
            call = {
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {"function": {"name": "list_files", "arguments": {"path": "."}}}
                    ],
                }
            }
            default_call = {
                "message": {
                    "role": "assistant",
                    "tool_calls": [{"function": {"name": "list_files", "arguments": {}}}],
                }
            }
            model = FakeModel([default_call, call])

            result = Controller(model, workspace).run(task)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["kind"], "duplicate_tool_call")
        self.assertEqual(len(model.requests), 2)

    def test_controller_recovers_from_malformed_tool_call_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "allowed.py").write_text("VALUE = 42\n", encoding="utf-8")
            task = TaskEnvelope(id="tool-json", goal="проверить значение", files=("allowed.py",))
            valid = json.dumps(
                {"status": "candidate", "summary": "ok", "patch": "", "checks": [], "risks": []}
            )
            model = FakeModel(
                [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": '{"path": "allowed.py"',  # truncated JSON
                                    },
                                }
                            ],
                        }
                    },
                    {"message": {"role": "assistant", "content": valid}},
                ]
            )

            result = Controller(model, workspace).run(task)

        # Not a hard policy failure: the malformed tool call was fed back as a
        # prescription and the model recovered on the next turn.
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(len(model.requests), 2)
        # The feedback message must be a tool message with an invalid_json payload.
        tool_msg = model.requests[1]["messages"][3]
        self.assertEqual(tool_msg["role"], "tool")
        payload = json.loads(tool_msg["content"])
        self.assertEqual(payload["error_code"], "invalid_json")
        self.assertIn("hint", payload)

    def test_controller_retries_invalid_json_with_changed_contract(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "allowed.py").write_text("VALUE = 42\n", encoding="utf-8")
            task = TaskEnvelope(id="retry-json", goal="вернуть результат", files=("allowed.py",))
            valid = json.dumps(
                {"status": "candidate", "summary": "ok", "patch": "", "checks": [], "risks": []}
            )
            model = FakeModel(
                [
                    {"message": {"role": "assistant", "content": "not json"}},
                    {"message": {"role": "assistant", "content": valid}},
                ]
            )

            result = Controller(model, workspace).run(task)

        self.assertEqual(result["status"], "accepted")
        self.assertEqual(len(model.requests), 2)
        self.assertEqual(model.requests[1]["messages"][-1]["role"], "user")

    def test_controller_stops_before_model_call_when_cancelled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "allowed.py").write_text("VALUE = 42\n", encoding="utf-8")
            task = TaskEnvelope(id="cancel", goal="не запускать", files=("allowed.py",))
            model = FakeModel([])
            cancelled = Event()
            cancelled.set()

            result = Controller(model, workspace).run(task, cancel_event=cancelled)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["kind"], "cancelled")
        self.assertEqual(model.requests, [])

    def test_controller_cancels_during_blocking_model_call(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "allowed.py").write_text("VALUE = 42\n", encoding="utf-8")
            task = TaskEnvelope(id="cancel-block", goal="не ждать", files=("allowed.py",))
            block_event = Event()
            release_event = Event()
            model = BlockingFakeModel(block_event, release_event)
            cancelled = Event()
            timer = Timer(0.2, cancelled.set)
            timer.start()

            result = Controller(model, workspace).run(task, cancel_event=cancelled)

            timer.cancel()
            release_event.set()

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["kind"], "cancelled")
        self.assertEqual(len(model.requests), 1)

    def test_controller_accepts_check_only_with_external_runner_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "allowed.py").write_text("VALUE = 42\n", encoding="utf-8")
            command = f'"{sys.executable}" -B -c "pass"'
            task = TaskEnvelope(
                id="check-evidence",
                goal="подтвердить проверку",
                files=("allowed.py",),
                checks=(command,),
            )
            model = FakeModel(
                [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "run_tests",
                                        "arguments": {"command": command},
                                    }
                                }
                            ],
                        }
                    },
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "status": "candidate",
                                    "summary": "проверка прошла",
                                    "patch": "",
                                    "checks": [
                                        {
                                            "command": command,
                                            "passed": True,
                                            "evidence": "exit_code=0; passed=True; stdout_bytes=0; stderr_bytes=0; truncated=False",
                                        }
                                    ],
                                    "risks": [],
                                }
                            ),
                        }
                    },
                ]
            )

            result = Controller(model, workspace).run(task)

        self.assertEqual(result["status"], "accepted")
        self.assertTrue(result["validation"]["valid"])

    def test_controller_fails_when_cumulative_context_exceeds_budget(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "allowed.py").write_text("x" * 500, encoding="utf-8")
            task = TaskEnvelope(id="cumulative-context", goal="прочитать файл", files=("allowed.py",))
            model = FakeModel(
                [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": {"path": "allowed.py"},
                                    },
                                }
                            ],
                        }
                    },
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "status": "candidate",
                                    "summary": "готово",
                                    "patch": "",
                                    "checks": [],
                                    "risks": [],
                                }
                            ),
                        }
                    },
                ]
            )

            result = Controller(model, workspace, max_context_bytes=2000).run(task)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["kind"], "context_limit")
        self.assertEqual(len(model.requests), 1)

    def test_controller_rejects_invalid_schema_without_crashing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "allowed.py").write_text("VALUE = 42\n", encoding="utf-8")
            task = TaskEnvelope(id="bad-schema", goal="вернуть плохой результат", files=("allowed.py",))
            model = FakeModel(
                [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "status": "candidate",
                                    "summary": "bad",
                                    "patch": "",
                                    "checks": [],
                                    "risks": "not-a-list",
                                }
                            ),
                        }
                    }
                ]
            )

            result = Controller(model, workspace).run(task)

        self.assertEqual(result["status"], "rejected")
        self.assertTrue(result["risks"])


    def test_controller_reuses_tool_proposed_patch_when_final_json_omits_it(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "src").mkdir()
            (workspace / "src" / "value.py").write_text("VALUE = 1\n", encoding="utf-8")
            task = TaskEnvelope(
                id="reuse-tool-patch",
                goal="изменить значение",
                files=("src/value.py",),
            )
            patch = (
                "diff --git a/src/value.py b/src/value.py\n"
                "--- a/src/value.py\n"
                "+++ b/src/value.py\n"
                "@@ -1 +1 @@\n"
                "-VALUE = 1\n"
                "+VALUE = 2\n"
            )
            model = FakeModel(
                [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "propose_patch",
                                        "arguments": {"patch": patch},
                                    }
                                }
                            ],
                        }
                    },
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "status": "candidate",
                                    "summary": "изменено значение",
                                    "checks": [],
                                    "risks": [],
                                },
                                ensure_ascii=False,
                            ),
                        }
                    },
                ]
            )

            result = Controller(model, workspace).run(task)

        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["patch"], patch)
        self.assertTrue(
            any(e.get("event") == "patch_reused_from_tool_proposal" for e in result["audit"])
        )

    def test_controller_applies_accepted_patch_when_requested(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "src").mkdir()
            (workspace / "src" / "value.py").write_text("VALUE = 1\n", encoding="utf-8")
            task = TaskEnvelope(
                id="controller-apply",
                goal="изменить значение",
                files=("src/value.py",),
                checks=(f'"{sys.executable}" -B -c "pass"',),
            )
            command = task.checks[0]
            patch = (
                "diff --git a/src/value.py b/src/value.py\n"
                "--- a/src/value.py\n"
                "+++ b/src/value.py\n"
                "@@ -1 +1 @@\n"
                "-VALUE = 1\n"
                "+VALUE = 2\n"
            )
            model = FakeModel(
                [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "run_tests",
                                        "arguments": {"command": command},
                                    }
                                }
                            ],
                        }
                    },
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "status": "candidate",
                                    "summary": "изменено значение",
                                    "patch": patch,
                                    "checks": [],
                                    "risks": [],
                                }
                            ),
                        }
                    }
                ]
            )

            result = Controller(model, workspace).run(task, apply=True)

            self.assertEqual(result["status"], "accepted")
            self.assertIs(result["applied"], True)
            self.assertEqual(
                (workspace / "src" / "value.py").read_text(encoding="utf-8"),
                "VALUE = 2\n",
            )

    def test_controller_returns_external_runner_evidence_not_model_text(self):
        command = f'"{sys.executable}" -B -c "pass"'
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "allowed.py").write_text("VALUE = 1\n", encoding="utf-8")
            task = TaskEnvelope(
                id="external-evidence",
                goal="проверить evidence",
                files=("allowed.py",),
                checks=(command,),
            )
            model = FakeModel(
                [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "run_tests",
                                        "arguments": {"command": command},
                                    }
                                }
                            ],
                        }
                    },
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "status": "candidate",
                                    "summary": "проверено",
                                    "patch": "",
                                    "checks": [
                                        {
                                            "command": command,
                                            "passed": True,
                                            "evidence": "fabricated by model",
                                        }
                                    ],
                                    "risks": [],
                                }
                            ),
                        }
                    },
                ]
            )

            result = Controller(model, workspace).run(task)

        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["checks"][0]["command"], command)
        self.assertNotEqual(result["checks"][0]["evidence"], "fabricated by model")
        self.assertIn("exit_code=0", result["checks"][0]["evidence"])

    def test_controller_does_not_apply_by_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "src").mkdir()
            (workspace / "src" / "value.py").write_text("VALUE = 1\n", encoding="utf-8")
            task = TaskEnvelope(
                id="controller-no-apply",
                goal="изменить значение",
                files=("src/value.py",),
            )
            patch = (
                "diff --git a/src/value.py b/src/value.py\n"
                "--- a/src/value.py\n"
                "+++ b/src/value.py\n"
                "@@ -1 +1 @@\n"
                "-VALUE = 1\n"
                "+VALUE = 2\n"
            )
            model = FakeModel(
                [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "status": "candidate",
                                    "summary": "изменено значение",
                                    "patch": patch,
                                    "checks": [],
                                    "risks": [],
                                }
                            ),
                        }
                    }
                ]
            )

            result = Controller(model, workspace).run(task)

            self.assertEqual(result["status"], "accepted")
            self.assertNotIn("applied", result)
            self.assertEqual(
                (workspace / "src" / "value.py").read_text(encoding="utf-8"),
                "VALUE = 1\n",
            )

    def test_controller_ignores_model_owned_audit_and_applied_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "value.py").write_text("VALUE = 1\n", encoding="utf-8")
            task = TaskEnvelope(
                id="controller-provenance",
                goal="вернуть предложение",
                files=("value.py",),
            )
            model = FakeModel(
                [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "status": "candidate",
                                    "summary": "подделано",
                                    "patch": "",
                                    "checks": [],
                                    "risks": [],
                                    "applied": True,
                                    "audit": [{"event": "forged", "success": True}],
                                }
                            ),
                        }
                    }
                ]
            )

            result = Controller(model, workspace).run(task)

        self.assertEqual(result["status"], "accepted")
        self.assertNotIn("applied", result)
        self.assertNotIn("forged", json.dumps(result["audit"], ensure_ascii=False))
        self.assertEqual(result["audit"][0]["event"], "task_received")

    def test_controller_rejects_and_rolls_back_when_post_apply_check_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "src").mkdir()
            target = workspace / "src" / "value.py"
            target.write_text("VALUE = 1\n", encoding="utf-8")
            command = (
                f'"{sys.executable}" -B -c "import pathlib; '
                "raise SystemExit(0 if pathlib.Path('src/value.py').read_text().strip() == "
                "'VALUE = 1' else 1)\""
            )
            task = TaskEnvelope(
                id="controller-post-check",
                goal="изменить значение с post-check",
                files=("src/value.py",),
                checks=(command,),
            )
            patch = (
                "diff --git a/src/value.py b/src/value.py\n"
                "--- a/src/value.py\n"
                "+++ b/src/value.py\n"
                "@@ -1 +1 @@\n"
                "-VALUE = 1\n"
                "+VALUE = 2\n"
            )
            model = FakeModel(
                [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "run_tests",
                                        "arguments": {"command": command},
                                    }
                                }
                            ],
                        }
                    },
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "status": "candidate",
                                    "summary": "изменение предложено",
                                    "patch": patch,
                                    "checks": [
                                        {
                                            "command": command,
                                            "passed": True,
                                            "evidence": "exit_code=0; passed=True",
                                        }
                                    ],
                                    "risks": [],
                                }
                            ),
                        }
                    },
                ]
            )

            result = Controller(model, workspace).run(task, apply=True)

            restored = target.read_text(encoding="utf-8")

        self.assertEqual(result["status"], "rejected")
        self.assertNotIn("applied", result)
        self.assertEqual(restored, "VALUE = 1\n")
        self.assertTrue(
            any(risk["kind"] == "post_apply_check_failed" for risk in result["risks"])
        )
        self.assertTrue(
            any(event["event"] == "post_apply_check" for event in result["audit"])
        )

    def test_controller_marks_workspace_modified_when_rollback_fails(self):
        command = (
            f'"{sys.executable}" -B -c "import pathlib; '
            "raise SystemExit(0 if pathlib.Path('src/value.py').read_text().strip() == "
            "'VALUE = 1' else 1)\""
        )

        patch_text = (
            "diff --git a/src/value.py b/src/value.py\n"
            "--- a/src/value.py\n"
            "+++ b/src/value.py\n"
            "@@ -1 +1 @@\n"
            "-VALUE = 1\n"
            "+VALUE = 2\n"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "src").mkdir()
            target = workspace / "src" / "value.py"
            target.write_text("VALUE = 1\n", encoding="utf-8")
            task = TaskEnvelope(
                id="rollback-failure",
                goal="проверить rollback",
                files=("src/value.py",),
                checks=(command,),
            )
            model = FakeModel(
                [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "run_tests",
                                        "arguments": {"command": command},
                                    }
                                }
                            ],
                        }
                    },
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "status": "candidate",
                                    "summary": "изменение",
                                    "patch": patch_text,
                                    "checks": [],
                                    "risks": [],
                                }
                            ),
                        }
                    },
                ]
            )

            from local_coding_agent.validators import apply_patch as real_apply_patch

            def apply_side_effect(root, value, reverse=False):
                if reverse:
                    return False, "rollback denied"
                return real_apply_patch(root, value, reverse=False)

            with patch("local_coding_agent.controller.apply_patch", side_effect=apply_side_effect):
                result = Controller(model, workspace).run(task, apply=True)

        self.assertEqual(result["status"], "rejected")
        self.assertTrue(result["workspace_modified"])
        self.assertTrue(any(risk["kind"] == "rollback_failed" for risk in result["risks"]))

    def test_controller_applies_edit_proposal_when_requested(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "src").mkdir()
            (workspace / "src" / "value.py").write_text("VALUE = 1\n", encoding="utf-8")
            task = TaskEnvelope(
                id="controller-apply-edits",
                goal="изменить значение",
                files=("src/value.py",),
                checks=(f'"{sys.executable}" -B -c "pass"',),
            )
            command = task.checks[0]
            model = FakeModel(
                [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "run_tests",
                                        "arguments": {"command": command},
                                    }
                                }
                            ],
                        }
                    },
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "status": "candidate",
                                    "summary": "изменено значение",
                                    "edits": [
                                        {
                                            "file": "src/value.py",
                                            "search": "VALUE = 1",
                                            "replace": "VALUE = 2",
                                        }
                                    ],
                                    "checks": [],
                                    "risks": [],
                                }
                            ),
                        }
                    }
                ]
            )

            result = Controller(model, workspace).run(task, apply=True)

            self.assertEqual(result["status"], "accepted")
            self.assertIs(result["applied"], True)
            self.assertEqual(
                (workspace / "src" / "value.py").read_text(encoding="utf-8"),
                "VALUE = 2\n",
            )
            self.assertIn("patch", result)
            self.assertNotIn("edits", result)

    def test_apply_patch_is_not_a_model_tool(self):
        function_names = {
            definition["function"]["name"] for definition in TOOL_DEFINITIONS
        }
        self.assertNotIn("apply_patch", function_names)
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "src").mkdir()
            (workspace / "src" / "value.py").write_text("VALUE = 1\n", encoding="utf-8")
            task = TaskEnvelope(
                id="no-apply-tool",
                goal="изменить значение",
                files=("src/value.py",),
            )
            tools = BoundedRepositoryTools(workspace, task)
            with self.assertRaises(ToolPolicyError):
                tools.execute("apply_patch", {"patch": "x"})

    def test_controller_blocks_propose_patch_in_read_only_mode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "src").mkdir()
            (workspace / "src" / "value.py").write_text("VALUE = 1\n", encoding="utf-8")
            task = TaskEnvelope(
                id="readonly-block",
                goal="изменить значение",
                files=("src/value.py",),
            )
            patch = (
                "diff --git a/src/value.py b/src/value.py\n"
                "--- a/src/value.py\n"
                "+++ b/src/value.py\n"
                "@@ -1 +1 @@\n"
                "-VALUE = 1\n"
                "+VALUE = 2\n"
            )
            model = FakeModel(
                [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "propose_patch",
                                        "arguments": {"patch": patch},
                                    }
                                }
                            ],
                        }
                    },
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "status": "candidate",
                                    "summary": "изменено значение",
                                    "checks": [],
                                    "risks": [],
                                },
                                ensure_ascii=False,
                            ),
                        }
                    },
                ]
            )

            result = Controller(model, workspace, blocked_tools={"propose_patch", "run_tests"}).run(task)

        # The propose_patch tool call was blocked: no patch may be produced.
        self.assertNotIn("VALUE = 2", result.get("patch") or "")
        self.assertTrue(
            any(e.get("event") == "tool_policy_error" for e in result["audit"])
        )
        policy_error = next(
            e for e in result["audit"] if e.get("event") == "tool_policy_error"
        )
        self.assertIn("blocked in read-only mode", policy_error["error"])

    def test_retry_budget_rejects_above_hard_cap(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            with self.assertRaises(ValueError):
                Controller(FakeModel([]), workspace, max_retries=11)

    def test_controller_escalates_when_retry_budget_exhausted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "allowed.py").write_text("VALUE = 42\n", encoding="utf-8")
            task = TaskEnvelope(
                id="escalate-json",
                goal="вернуть результат",
                files=("allowed.py",),
            )
            model = FakeModel(
                [
                    {"message": {"role": "assistant", "content": "not json"}},
                    {"message": {"role": "assistant", "content": "not json"}},
                ]
            )

            result = Controller(model, workspace).run(task)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["kind"], "retry_budget_exhausted")
        self.assertEqual(result["escalation"]["reason"], "invalid_json")
        self.assertEqual(result["escalation"]["task"]["id"], "escalate-json")
        self.assertEqual(result["escalation"]["task"]["files"], ["allowed.py"])
        self.assertEqual(
            result["escalation"]["attempts"],
            [
                {"attempt": 1, "reason": "invalid_json"},
                {"attempt": 2, "reason": "invalid_json"},
            ],
        )
        self.assertEqual(len(model.requests), 2)
        self.assertTrue(
            any(e.get("event") == "escalation" for e in result["audit"])
        )

    def test_escalation_bundle_captures_viewed_files_and_last_patch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "allowed.py").write_text("VALUE = 42\n", encoding="utf-8")
            task = TaskEnvelope(
                id="escalate-context",
                goal="изменить значение",
                files=("allowed.py",),
            )
            patch = (
                "diff --git a/allowed.py b/allowed.py\n"
                "--- a/allowed.py\n"
                "+++ b/allowed.py\n"
                "@@ -1 +1 @@\n"
                "-VALUE = 42\n"
                "+VALUE = 43\n"
            )
            model = FakeModel(
                [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": {"path": "allowed.py"},
                                    },
                                }
                            ],
                        }
                    },
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "propose_patch",
                                        "arguments": {"patch": patch},
                                    }
                                }
                            ],
                        }
                    },
                    {"message": {"role": "assistant", "content": "bad"}},
                    {"message": {"role": "assistant", "content": "bad"}},
                ]
            )

            result = Controller(model, workspace, max_retries=1).run(task)

        self.assertEqual(result["escalation"]["viewed_files"], ["allowed.py"])
        self.assertEqual(result["escalation"]["last_patch"], patch)
        self.assertEqual(result["escalation"]["external_evidence"], {})
        self.assertEqual(len(result["escalation"]["attempts"]), 2)

    def test_controller_escalates_on_invalid_response_without_message(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "allowed.py").write_text("VALUE = 42\n", encoding="utf-8")
            task = TaskEnvelope(
                id="escalate-invalid-response",
                goal="вернуть результат",
                files=("allowed.py",),
            )
            model = FakeModel([{}, {}])

            result = Controller(model, workspace).run(task)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["kind"], "retry_budget_exhausted")
        self.assertEqual(result["escalation"]["reason"], "invalid_response")
        self.assertEqual(
            result["escalation"]["attempts"],
            [
                {"attempt": 1, "reason": "invalid_response"},
                {"attempt": 2, "reason": "invalid_response"},
            ],
        )
        self.assertEqual(len(model.requests), 2)

    def test_controller_escalates_on_max_turns_when_attempts_exist(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "allowed.py").write_text("VALUE = 42\n", encoding="utf-8")
            task = TaskEnvelope(
                id="escalate-max-turns",
                goal="вернуть результат",
                files=("allowed.py",),
            )
            model = FakeModel(
                [
                    {"message": {"role": "assistant", "content": "bad"}},
                    {"message": {"role": "assistant", "content": "bad"}},
                ]
            )

            result = Controller(model, workspace, max_turns=2, max_retries=5).run(task)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["kind"], "retry_budget_exhausted")
        self.assertEqual(result["escalation"]["reason"], "max_turns")
        self.assertEqual(len(result["escalation"]["attempts"]), 2)

    def test_repeated_tool_call_has_priority_over_retry_budget(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "allowed.py").write_text("VALUE = 42\n", encoding="utf-8")
            task = TaskEnvelope(
                id="duplicate-priority",
                goal="прочитать файл",
                files=("allowed.py",),
            )
            call = {
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {"function": {"name": "read_file", "arguments": {"path": "allowed.py"}}}
                    ],
                }
            }
            model = FakeModel([call, call])

            result = Controller(model, workspace, max_retries=5).run(task)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["kind"], "duplicate_tool_call")
        self.assertNotIn("escalation", result)

    def test_cancellation_has_priority_over_retry_budget(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "allowed.py").write_text("VALUE = 42\n", encoding="utf-8")
            task = TaskEnvelope(
                id="cancel-priority",
                goal="не запускать",
                files=("allowed.py",),
            )
            model = FakeModel([])
            cancelled = Event()
            cancelled.set()

            result = Controller(model, workspace, max_retries=5).run(
                task, cancel_event=cancelled
            )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["kind"], "cancelled")
        self.assertNotIn("escalation", result)
        self.assertEqual(model.requests, [])


    def test_controller_provides_feedback_for_propose_patch_error_and_allows_correction(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "src").mkdir()
            (workspace / "src" / "value.py").write_text("VALUE = 1\n", encoding="utf-8")
            task = TaskEnvelope(
                id="patch-feedback-correction",
                goal="изменить значение",
                files=("src/value.py",),
            )
            # Turn 1: model tries edits with non-matching search block
            turn1_call = {
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "function": {
                                "name": "propose_patch",
                                "arguments": {
                                    "edits": [
                                        {
                                            "file": "src/value.py",
                                            "search": "WRONG_SEARCH",
                                            "replace": "VALUE = 2",
                                        }
                                    ]
                                },
                            },
                        }
                    ],
                }
            }
            # Turn 2: model sees error in tool message and issues corrected edits
            turn2_call = {
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call-2",
                            "function": {
                                "name": "propose_patch",
                                "arguments": {
                                    "edits": [
                                        {
                                            "file": "src/value.py",
                                            "search": "VALUE = 1",
                                            "replace": "VALUE = 2",
                                        }
                                    ]
                                },
                            },
                        }
                    ],
                }
            }
            # Turn 3: model finishes with candidate
            turn3_call = {
                "message": {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "status": "candidate",
                            "summary": "значение успешно обновлено",
                            "checks": [],
                            "risks": [],
                        }
                    ),
                }
            }
            model = FakeModel([turn1_call, turn2_call, turn3_call])
            result = Controller(model, workspace).run(task)

        self.assertEqual(result["status"], "accepted")
        self.assertIn("VALUE = 2", result["patch"])
        # Verify turn 2 tool feedback contained the error detail
        turn2_messages = model.requests[1]["messages"]
        self.assertEqual(turn2_messages[-1]["role"], "tool")
        self.assertIn("propose_patch rejected", turn2_messages[-1]["content"])
        self.assertIn("not found", turn2_messages[-1]["content"])

    def test_controller_provides_templated_tool_feedback_on_tool_policy_violation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "src").mkdir()
            (workspace / "src" / "value.py").write_text("VALUE = 1\n", encoding="utf-8")
            task = TaskEnvelope(
                id="policy-feedback",
                goal="исправить значение",
                files=("src/value.py",),
            )
            # Turn 1: model provides both patch and edits (ToolPolicyError)
            turn1_call = {
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call-policy-1",
                            "function": {
                                "name": "propose_patch",
                                "arguments": {
                                    "patch": "diff --git a/src/value.py b/src/value.py\n",
                                    "edits": [{"file": "src/value.py", "search": "VALUE = 1", "replace": "VALUE = 2"}],
                                },
                            },
                        }
                    ],
                }
            }
            # Turn 2: model fixes arguments to only edits
            turn2_call = {
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call-policy-2",
                            "function": {
                                "name": "propose_patch",
                                "arguments": {
                                    "edits": [{"file": "src/value.py", "search": "VALUE = 1", "replace": "VALUE = 2"}],
                                },
                            },
                        }
                    ],
                }
            }
            # Turn 3: model finalizes candidate
            turn3_call = {
                "message": {
                    "role": "assistant",
                    "content": json.dumps(
                        {"status": "candidate", "summary": "исправлено", "checks": [], "risks": []}
                    ),
                }
            }
            model = FakeModel([turn1_call, turn2_call, turn3_call])
            result = Controller(model, workspace).run(task)

        self.assertEqual(result["status"], "accepted")
        self.assertIn("VALUE = 2", result["patch"])
        # Verify turn 2 tool feedback contained policy violation details in same chat
        turn2_messages = model.requests[1]["messages"]
        self.assertEqual(turn2_messages[-1]["role"], "tool")
        self.assertIn("ERR_DUAL_FORMAT", turn2_messages[-1]["content"])
        self.assertIn("not both", turn2_messages[-1]["content"])

    def test_controller_provides_templated_candidate_feedback_when_patch_invalid(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "src").mkdir()
            (workspace / "src" / "value.py").write_text("VALUE = 1\n", encoding="utf-8")
            task = TaskEnvelope(
                id="candidate-feedback",
                goal="исправить значение",
                files=("src/value.py",),
            )
            # Turn 1: model returns candidate with invalid hunk line
            turn1_candidate = {
                "message": {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "status": "candidate",
                            "summary": "плохой патч",
                            "patch": "diff --git a/src/value.py b/src/value.py\n--- a/src/value.py\n+++ b/src/value.py\n@@ -1,99 +1,99 @@\n-VALUE = 1\n+VALUE = 2\n",
                            "checks": [],
                            "risks": [],
                        }
                    ),
                }
            }
            # Turn 2: model corrects candidate using edits
            turn2_candidate = {
                "message": {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "status": "candidate",
                            "summary": "исправленный патч",
                            "edits": [{"file": "src/value.py", "search": "VALUE = 1", "replace": "VALUE = 2"}],
                            "checks": [],
                            "risks": [],
                        }
                    ),
                }
            }
            model = FakeModel([turn1_candidate, turn2_candidate])
            result = Controller(model, workspace, max_retries=1).run(task)

        self.assertEqual(result["status"], "accepted")
        self.assertIn("VALUE = 2", result["patch"])
        # Verify turn 2 prompt contained validation feedback in single contiguous context
        turn2_messages = model.requests[1]["messages"]
        self.assertEqual(turn2_messages[-1]["role"], "user")
        self.assertIn("CANDIDATE_VALIDATION_FAILED", turn2_messages[-1]["content"])
    def test_run_post_apply_checks_includes_stdout_and_stderr(self):
        from local_coding_agent.controller import run_post_apply_checks
        from local_coding_agent.repository_tools import BoundedRepositoryTools

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            test_file = workspace / "test_dummy.py"
            test_file.write_text("def test_ok(): pass\n", encoding="utf-8")
            task = TaskEnvelope(
                id="post-check-test",
                goal="test checks output",
                files=("test_dummy.py",),
                checks=("pytest test_dummy.py",),
            )
            tools = BoundedRepositoryTools(workspace, task)
            audit = []
            checks, all_passed = run_post_apply_checks(task, tools, active_cancel=None, audit=audit)

            self.assertEqual(len(checks), 1)
            self.assertTrue(all_passed)
            self.assertIn("stdout", checks[0])
            self.assertIn("stderr", checks[0])
            self.assertIn("exit_code", checks[0])
            self.assertEqual(checks[0]["exit_code"], 0)

    def test_controller_normalizes_redundant_patch_when_edits_provided(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            target = workspace / "src" / "sample.py"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("x = 1\n", encoding="utf-8")
            task = TaskEnvelope(
                id="redundant-test",
                goal="fix x",
                files=("src/sample.py",),
            )
            candidate = {
                "status": "candidate",
                "summary": "fixed",
                "patch": "diff --git a/src/sample.py b/src/sample.py\n--- a/src/sample.py\n+++ b/src/sample.py\n@@ -1,1 +1,1 @@\n-x = 1\n+x = 2\n",
                "edits": [
                    {
                        "file": "src/sample.py",
                        "search": "x = 1",
                        "replace": "x = 2",
                    }
                ],
                "checks": [],
                "risks": [],
            }
            model = FakeModel([{"message": {"content": json.dumps(candidate)}}])
            result = Controller(model, workspace).run(task)


            self.assertEqual(result["status"], "accepted")
            self.assertTrue(result["validation"]["valid"])
            self.assertIn("x = 2", result["patch"])

    def test_controller_uses_custom_system_contract(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "allowed.py").write_text("VALUE = 1\n", encoding="utf-8")
            task = TaskEnvelope(id="custom-contract", goal="тест", files=("allowed.py",))
            model = FakeModel([{"message": {"role": "assistant", "content": json.dumps({"status": "candidate", "summary": "ok", "patch": "", "checks": [], "risks": []})}}])
            custom_contract = "Custom system prompt for model."
            Controller(model, workspace, system_contract=custom_contract).run(task)

            first_request_messages = model.requests[0]["messages"]
            self.assertEqual(first_request_messages[0]["role"], "system")
            self.assertEqual(first_request_messages[0]["content"], custom_contract)

    def test_context_compaction_evicts_oldest_tool_call_pair_when_context_budget_exceeded(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "allowed.py").write_text("VALUE = 1\n", encoding="utf-8")
            task = TaskEnvelope(id="compact-test", goal="тест компактификации", files=("allowed.py",))

            model = FakeModel([
                {
                    "message": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "function": {
                                    "name": "read_file",
                                    "arguments": {"path": "allowed.py"},
                                },
                            }
                        ],
                    }
                },
                {
                    "message": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "call-2",
                                "function": {
                                    "name": "propose_patch",
                                    "arguments": {
                                        "edits": [{"file": "allowed.py", "search": "VALUE = 1", "replace": "VALUE = 2"}]
                                    },
                                },
                            }
                        ],
                    }
                },
                {
                    "message": {
                        "role": "assistant",
                        "content": json.dumps({
                            "status": "candidate",
                            "summary": "done",
                            "edits": [{"file": "allowed.py", "search": "VALUE = 1", "replace": "VALUE = 2"}],
                            "checks": [],
                            "risks": [],
                        }),
                    }
                },
            ])
            controller = Controller(model, workspace, max_turns=4, max_context_bytes=2500)
            result = controller.run(task)

            self.assertEqual(result["status"], "accepted")
            compaction_events = [e for e in result["audit"] if e.get("event") == "context_compacted"]
            self.assertGreaterEqual(len(compaction_events), 1)

            for req in model.requests:
                msgs = req["messages"]
                for i, m in enumerate(msgs):
                    if m["role"] == "tool":
                        self.assertGreater(i, 0)
                        prev_idx = i - 1
                        while prev_idx >= 0 and msgs[prev_idx]["role"] == "tool":
                            prev_idx -= 1
                        self.assertEqual(msgs[prev_idx]["role"], "assistant")
                        self.assertTrue(bool(msgs[prev_idx].get("tool_calls")))

    def test_diff_residue_elimination_on_validation_retry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "allowed.py").write_text("VALUE = 1\n", encoding="utf-8")
            task = TaskEnvelope(id="residue-test", goal="тест остатка", files=("allowed.py",))

            invalid_candidate = {
                "status": "candidate",
                "summary": "invalid diff",
                "patch": "diff --git a/allowed.py b/allowed.py\ncorrupt hunk header\n" + ("x" * 500),
                "checks": [],
                "risks": [],
            }
            valid_candidate = {
                "status": "candidate",
                "summary": "fixed",
                "edits": [{"file": "allowed.py", "search": "VALUE = 1", "replace": "VALUE = 2"}],
                "checks": [],
                "risks": [],
            }
            model = FakeModel([
                {"message": {"role": "assistant", "content": json.dumps(invalid_candidate)}},
                {"message": {"role": "assistant", "content": json.dumps(valid_candidate)}},
            ])
            controller = Controller(model, workspace, max_turns=4, max_retries=1)
            result = controller.run(task)

            self.assertEqual(result["status"], "accepted")
            second_request_messages = model.requests[1]["messages"]
            prompt_text = json.dumps(second_request_messages)
            self.assertNotIn("x" * 500, prompt_text)

if __name__ == "__main__":
    unittest.main()


class ContextOverflowTests(unittest.TestCase):
    def test_context_overflow_translated_to_prescription(self):
        error_payload = (
            'Ollama HTTP 400: {"error":{"code":400,"message":"request (10848 tokens) '
            'exceeds the available context size (8192 tokens), try increasing it",'
            '"type":"exceed_context_size_error","n_prompt_tokens":10848,"n_ctx":8192}}'
        )

        class OverflowModel:
            def chat(self, messages, *, tools=None):
                raise RuntimeError(error_payload)

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "allowed.py").write_text("VALUE = 42\n", encoding="utf-8")
            task = TaskEnvelope(id="overflow", goal="fix", files=("allowed.py",))
            result = Controller(OverflowModel(), str(workspace)).run(task)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["kind"], "context_overflow")
        self.assertIn("10848", result["summary"])
        self.assertIn("8192", result["summary"])
        self.assertNotIn("HTTP 400", result["summary"])

    def test_context_overflow_without_numbers_gets_generic_prescription(self):
        from local_coding_agent.controller._controller import (
            _context_overflow_message,
            _is_context_overflow,
        )

        self.assertTrue(_is_context_overflow(Exception("exceed_context_size_error boom")))
        msg = _context_overflow_message(Exception("exceed_context_size_error"))
        self.assertIn("num_ctx", msg)
        self.assertNotIn("~", msg)
