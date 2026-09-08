import json
import tempfile
import unittest
from pathlib import Path
from threading import Event, Thread
from unittest.mock import patch

from local_coding_agent.atomizer import TaskBudget
from local_coding_agent.service import DelegationRequest, DelegationService
from local_coding_agent.task import TaskEnvelope


class FakeModel:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    def chat(self, messages, *, tools=None):
        self.calls += 1
        return self.response


class BlockingFakeModel(FakeModel):
    def __init__(self, response, entered, release):
        super().__init__(response)
        self.entered = entered
        self.release = release

    def chat(self, messages, *, tools=None):
        self.calls += 1
        self.entered.set()
        self.release.wait(timeout=5)
        return self.response


class SequenceModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def chat(self, messages, *, tools=None):
        self.calls += 1
        return self.responses.pop(0)


class DelegationServiceTests(unittest.TestCase):
    def _request(self, *, request_id="request-1", goal="прочитать файл", profile="qwen2.5-1.5b"):
        return DelegationRequest(
            request_id=request_id,
            workspace_ref="fixture",
            model_profile=profile,
            task=TaskEnvelope(id="task-1", goal=goal, files=("allowed.py",)),
        )

    def _candidate(self, *, patch=""):
        return {
            "message": {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "status": "candidate",
                        "summary": "готово",
                        "patch": patch,
                        "checks": [],
                        "risks": [],
                        "audit": [{"event": "forged"}],
                        "applied": True,
                    },
                    ensure_ascii=False,
                ),
            }
        }

    def test_delegates_registered_workspace_proposal_only_and_caches_idempotently(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            target = workspace / "allowed.py"
            target.write_text("VALUE = 1\n", encoding="utf-8")
            patch = (
                "diff --git a/allowed.py b/allowed.py\n"
                "--- a/allowed.py\n"
                "+++ b/allowed.py\n"
                "@@ -1 +1 @@\n"
                "-VALUE = 1\n"
                "+VALUE = 2\n"
            )
            model = FakeModel(self._candidate(patch=patch))
            service = DelegationService(
                {"fixture": workspace},
                model_factory=lambda profile: model,
            )

            first = service.delegate("caller-a", self._request())
            second = service.delegate("caller-a", self._request())
            unchanged = target.read_text(encoding="utf-8")

        self.assertEqual(first["status"], "accepted")
        self.assertFalse(first.get("applied", False))
        self.assertNotIn({"event": "forged"}, first["audit"])
        self.assertEqual(unchanged, "VALUE = 1\n")
        self.assertEqual(model.calls, 1)
        self.assertEqual(first, second)

    def test_rejects_unregistered_workspace_and_unknown_profile_without_model_call(self):
        calls = []
        with tempfile.TemporaryDirectory() as temp_dir:
            service = DelegationService(
                {"fixture": temp_dir},
                model_factory=lambda profile: calls.append(profile),
            )
            unknown_workspace = service.delegate(
                "caller-a",
                DelegationRequest(
                    request_id="request-1",
                    workspace_ref="unknown",
                    model_profile="qwen2.5-1.5b",
                    task=TaskEnvelope(id="task-1", goal="read", files=("allowed.py",)),
                ),
            )
            unknown_profile = service.delegate(
                "caller-a",
                self._request(request_id="request-2", profile="http://untrusted.invalid"),
            )

        self.assertEqual(unknown_workspace["error"]["kind"], "unknown_workspace")
        self.assertEqual(unknown_profile["error"]["kind"], "unknown_model_profile")
        self.assertEqual(calls, [])

    def test_rejects_reused_request_id_with_a_different_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "allowed.py").write_text("VALUE = 1\n", encoding="utf-8")
            model = FakeModel(self._candidate())
            service = DelegationService(
                {"fixture": workspace},
                model_factory=lambda profile: model,
            )

            service.delegate("caller-a", self._request())
            conflict = service.delegate("caller-a", self._request(goal="другая задача"))

        self.assertEqual(conflict["status"], "failed")
        self.assertEqual(conflict["error"]["kind"], "idempotency_conflict")
        self.assertEqual(model.calls, 1)

    def test_rejects_wide_task_before_model_execution(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            for index in range(6):
                (workspace / f"allowed-{index}.py").write_text("VALUE = 1\n", encoding="utf-8")
            model = FakeModel(self._candidate())
            service = DelegationService(
                {"fixture": workspace},
                model_factory=lambda profile: model,
            )
            request = DelegationRequest(
                request_id="too-many-files",
                workspace_ref="fixture",
                model_profile="qwen2.5-1.5b",
                task=TaskEnvelope(
                    id="wide-task",
                    goal="read",
                    files=tuple(f"allowed-{index}.py" for index in range(6)),
                ),
            )
            result = service.delegate("caller-a", request)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["kind"], "preflight_rejected")
        self.assertFalse(result["applied"])
        self.assertEqual(model.calls, 0)

    def test_unexpected_model_factory_error_completes_and_caches_terminal_result(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "allowed.py").write_text("VALUE = 1\n", encoding="utf-8")
            factory_calls = []

            def broken_factory(profile):
                factory_calls.append(profile.name)
                raise RuntimeError("simulated infrastructure failure")

            service = DelegationService({"fixture": workspace}, model_factory=broken_factory)
            first = service.delegate("caller-a", self._request())
            second = service.delegate("caller-a", self._request())

        self.assertEqual(first["status"], "failed")
        self.assertEqual(first["error"]["kind"], "controller_error")
        self.assertEqual(first, second)
        self.assertEqual(factory_calls, ["qwen2.5-1.5b"])

    def test_concurrent_duplicate_requests_share_one_controller_execution(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "allowed.py").write_text("VALUE = 1\n", encoding="utf-8")
            entered, release = Event(), Event()
            model = BlockingFakeModel(self._candidate(), entered, release)
            service = DelegationService(
                {"fixture": workspace},
                model_factory=lambda profile: model,
            )
            results = []
            first = Thread(target=lambda: results.append(service.delegate("caller-a", self._request())))
            second = Thread(target=lambda: results.append(service.delegate("caller-a", self._request())))
            first.start()
            self.assertTrue(entered.wait(timeout=2))
            second.start()
            release.set()
            first.join(timeout=3)
            second.join(timeout=3)

        self.assertEqual(model.calls, 1)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], results[1])

    def test_completed_idempotency_results_are_bounded_and_evictable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "allowed.py").write_text("VALUE = 1\n", encoding="utf-8")
            model = FakeModel(self._candidate())
            service = DelegationService(
                {"fixture": workspace},
                model_factory=lambda profile: model,
                max_cached_results=1,
            )
            service.delegate("caller-a", self._request(request_id="one"))
            service.delegate("caller-a", self._request(request_id="two"))
            service.delegate("caller-a", self._request(request_id="one"))

        self.assertEqual(model.calls, 3)

    def test_preflight_budget_rejects_overwide_task_without_model_call(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "allowed.py").write_text("VALUE = 1\n", encoding="utf-8")
            calls = []
            service = DelegationService(
                {"fixture": workspace},
                model_factory=lambda profile: calls.append(profile),
                preflight_budget=TaskBudget(max_files=2),
            )
            request = DelegationRequest(
                request_id="over-wide",
                workspace_ref="fixture",
                model_profile="qwen2.5-1.5b",
                task=TaskEnvelope(
                    id="wide-task",
                    goal="read",
                    files=("a.py", "b.py", "c.py"),
                ),
            )
            result = service.delegate("caller-a", request)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["kind"], "preflight_rejected")
        self.assertEqual(result["error"]["message"], "too_many_files")
        self.assertFalse(result["applied"])
        self.assertEqual(calls, [])

    def test_default_preflight_budget_rejects_overwide_task_without_model_call(self):
        calls = []
        with tempfile.TemporaryDirectory() as temp_dir:
            service = DelegationService(
                {"fixture": temp_dir},
                model_factory=lambda profile: calls.append(profile),
            )
            request = DelegationRequest(
                request_id="wide-default",
                workspace_ref="fixture",
                model_profile="qwen2.5-1.5b",
                task=TaskEnvelope(
                    id="wide-default",
                    goal="широкая задача",
                    files=tuple(f"file-{index}.py" for index in range(6)),
                ),
            )
            result = service.delegate("caller-a", request)

        self.assertEqual(result["error"]["kind"], "preflight_rejected")
        self.assertIn("too_many_files", result["error"]["message"])
        self.assertEqual(calls, [])

    def test_request_rejects_blank_transport_identifiers(self):
        task = TaskEnvelope(id="task-1", goal="read", files=("allowed.py",))
        with self.assertRaises(ValueError):
            DelegationRequest("", "fixture", "qwen2.5-1.5b", task)
        with self.assertRaises(ValueError):
            DelegationRequest("request-1", "", "qwen2.5-1.5b", task)
        with self.assertRaises(ValueError):
            DelegationRequest("request-1", "fixture", "", task)


_PATCH = (
    "diff --git a/value.py b/value.py\n"
    "--- a/value.py\n"
    "+++ b/value.py\n"
    "@@ -1 +1 @@\n"
    "-VALUE = 1\n"
    "+VALUE = 2\n"
)


class DelegationServiceApplyTests(unittest.TestCase):
    def _request(self, *, checks=()):
        return DelegationRequest(
            request_id="request-1",
            workspace_ref="fixture",
            model_profile="qwen2.5-1.5b",
            task=TaskEnvelope(id="task-1", goal="change", files=("value.py",), checks=checks),
        )

    def _candidate(self, patch=_PATCH, checks=None):
        return {
            "message": {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "status": "candidate",
                        "summary": "изменено значение",
                        "patch": patch,
                        "checks": checks if checks is not None else [],
                        "risks": [],
                    },
                    ensure_ascii=False,
                ),
            }
        }

    def test_apply_applies_stored_proposal_and_runs_checks(self):
        import sys

        command = f'"{sys.executable}" -B -c "pass"'
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "value.py").write_text("VALUE = 1\n", encoding="utf-8")
            model = SequenceModel(
                [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {"function": {"name": "run_tests", "arguments": {"command": command}}}
                            ],
                        }
                    },
                    self._candidate(
                        checks=[{"command": command, "passed": True, "evidence": "exit_code=0; passed=True"}]
                    ),
                ]
            )
            service = DelegationService({"fixture": workspace}, model_factory=lambda profile: model)
            delegated = service.delegate("caller-a", self._request(checks=(command,)))
            result = service.apply("caller-a", "fixture", "request-1")
            content = (workspace / "value.py").read_text(encoding="utf-8")

        self.assertEqual(delegated["status"], "accepted")
        self.assertEqual(result["status"], "accepted")
        self.assertTrue(result["applied"])
        self.assertEqual(content, "VALUE = 2\n")
        self.assertEqual(model.calls, 2)

    def test_apply_rejects_patch_without_targeted_checks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            target = workspace / "value.py"
            target.write_text("VALUE = 1\n", encoding="utf-8")
            service = DelegationService(
                {"fixture": workspace},
                model_factory=lambda profile: FakeModel(self._candidate()),
            )
            delegated = service.delegate("caller-a", self._request())
            result = service.apply("caller-a", "fixture", "request-1")
            content = target.read_text(encoding="utf-8")

        self.assertEqual(delegated["status"], "accepted")
        self.assertEqual(result["error"]["kind"], "apply_requires_checks")
        self.assertEqual(content, "VALUE = 1\n")

    def test_apply_rolls_back_when_post_apply_check_fails(self):
        import sys

        # The check passes pre-apply (VALUE = 1) and fails post-apply, forcing rollback.
        command = (
            f'"{sys.executable}" -B -c "import pathlib; '
            "raise SystemExit(0 if pathlib.Path('value.py').read_text().strip() == "
            "'VALUE = 1' else 1)\""
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "value.py").write_text("VALUE = 1\n", encoding="utf-8")
            model = SequenceModel(
                [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {"function": {"name": "run_tests", "arguments": {"command": command}}}
                            ],
                        }
                    },
                    self._candidate(
                        checks=[{"command": command, "passed": True, "evidence": "exit_code=0; passed=True"}]
                    ),
                ]
            )
            service = DelegationService({"fixture": workspace}, model_factory=lambda profile: model)
            service.delegate("caller-a", self._request(checks=(command,)))
            result = service.apply("caller-a", "fixture", "request-1")
            content = (workspace / "value.py").read_text(encoding="utf-8")

        self.assertEqual(result["status"], "rejected")
        self.assertNotIn("applied", result)
        self.assertEqual(content, "VALUE = 1\n")
        self.assertEqual(result["error"]["kind"], "post_apply_check_failed")
        self.assertNotIn("workspace_modified", result)
        self.assertTrue(any(e["event"] == "patch_rolled_back" for e in result["audit"]))

    def test_apply_reports_workspace_modified_when_rollback_raises(self):
        import sys

        command = f'"{sys.executable}" -B -c "raise SystemExit(1)"'
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            target = workspace / "value.py"
            target.write_text("VALUE = 1\n", encoding="utf-8")
            model = SequenceModel(
                [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {"function": {"name": "run_tests", "arguments": {"command": command}}}
                            ],
                        }
                    },
                    self._candidate(
                        checks=[{"command": command, "passed": True, "evidence": "exit_code=0; passed=True"}]
                    ),
                ]
            )
            service = DelegationService({"fixture": workspace}, model_factory=lambda profile: model)
            service.delegate("caller-a", self._request(checks=(command,)))

            from local_coding_agent import service as service_module

            real_apply_patch = service_module.apply_patch

            def apply_then_fail_reverse(root, patch, reverse=False):
                if reverse:
                    raise RuntimeError("rollback runner unavailable")
                return real_apply_patch(root, patch)

            with patch.object(service_module, "apply_patch", side_effect=apply_then_fail_reverse):
                result = service.apply("caller-a", "fixture", "request-1")

            self.assertEqual(result["status"], "rejected")
            self.assertTrue(result["workspace_modified"])
            self.assertTrue(any(r["kind"] == "rollback_failed" for r in result["risks"]))
            self.assertTrue(any(e["event"] == "rollback_failed" for e in result["audit"]))
            self.assertEqual(target.read_text(encoding="utf-8"), "VALUE = 2\n")


    def test_apply_unknown_proposal_fails_without_changes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "value.py").write_text("VALUE = 1\n", encoding="utf-8")
            service = DelegationService({"fixture": workspace}, model_factory=lambda profile: FakeModel(self._candidate()))
            result = service.apply("caller-a", "fixture", "does-not-exist")
            content = (workspace / "value.py").read_text(encoding="utf-8")

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["kind"], "unknown_proposal")
        self.assertEqual(content, "VALUE = 1\n")

    def test_apply_rejects_non_accepted_proposal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "value.py").write_text("VALUE = 1\n", encoding="utf-8")
            # A patch touching a file outside the task allowlist is rejected by
            # validation, so the stored proposal is never accepted.
            outside = (
                "diff --git a/other.py b/other.py\n"
                "--- a/other.py\n"
                "+++ b/other.py\n"
                "@@ -1 +1 @@\n"
                "-VALUE = 1\n"
                "+VALUE = 2\n"
            )
            model = FakeModel(self._candidate(patch=outside))
            service = DelegationService({"fixture": workspace}, model_factory=lambda profile: model)
            delegated = service.delegate("caller-a", self._request())
            result = service.apply("caller-a", "fixture", "request-1")

        self.assertEqual(delegated["status"], "rejected")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["kind"], "proposal_not_accepted")

    def test_apply_serializes_mutation_pipeline_per_workspace(self):
        import sys

        command = f'"{sys.executable}" -B -c "pass"'
        first_check_entered = Event()
        release_first_check = Event()
        second_started = Event()
        second_finished = Event()
        state = {"active": 0, "max_active": 0}
        state_lock = __import__("threading").Lock()

        def blocking_check(workspace, patch_text):
            with state_lock:
                state["active"] += 1
                state["max_active"] = max(state["max_active"], state["active"])
            first_check_entered.set()
            release_first_check.wait(timeout=5)
            with state_lock:
                state["active"] -= 1
            return False, "held for serialization test"

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "value.py").write_text("VALUE = 1\n", encoding="utf-8")
            model = SequenceModel(
                [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {"function": {"name": "run_tests", "arguments": {"command": command}}}
                            ],
                        }
                    },
                    self._candidate(
                        checks=[{"command": command, "passed": True, "evidence": "exit_code=0; passed=True"}]
                    ),
                ]
            )
            service = DelegationService({"fixture": workspace}, model_factory=lambda profile: model)
            service.delegate("caller-a", self._request(checks=(command,)))
            results = []

            def apply_first():
                results.append(service.apply("caller-a", "fixture", "request-1"))

            def apply_second():
                second_started.set()
                results.append(service.apply("caller-a", "fixture", "request-1"))
                second_finished.set()

            with patch("local_coding_agent.service.check_patch_applies", side_effect=blocking_check):
                first = Thread(target=apply_first)
                second = Thread(target=apply_second)
                first.start()
                self.assertTrue(first_check_entered.wait(timeout=2))
                second.start()
                self.assertTrue(second_started.wait(timeout=2))
                self.assertFalse(second_finished.wait(timeout=0.1))
                with state_lock:
                    self.assertEqual(state["max_active"], 1)
                release_first_check.set()
                first.join(timeout=5)
                second.join(timeout=5)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(len(results), 2)
        self.assertTrue(all(result["error"]["kind"] == "stale_workspace" for result in results))

    def test_service_forwards_profile_system_contract_to_controller(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "allowed.py").write_text("VALUE = 1\n", encoding="utf-8")
            recorded_messages = []

            class RecordingModel:
                def chat(self, messages, *, tools=None):
                    recorded_messages.extend(messages)
                    return {"message": {"role": "assistant", "content": json.dumps({"status": "candidate", "summary": "done", "patch": "", "checks": [], "risks": []})}}

            service = DelegationService(
                {"fixture": workspace},
                model_factory=lambda profile: RecordingModel(),
            )
            with patch("local_coding_agent.service.get_profile") as mock_get_profile:
                from local_coding_agent.ollama_adapter import ModelProfile
                mock_get_profile.return_value = ModelProfile(
                    name="custom-p",
                    model="custom-m",
                    system_contract="Specialized System Contract",
                )
                service.delegate(
                    "caller-a",
                    DelegationRequest(
                        request_id="req-custom-contract",
                        workspace_ref="fixture",
                        model_profile="custom-p",
                        task=TaskEnvelope(id="task-1", goal="goal", files=("allowed.py",)),
                    ),
                )

            self.assertEqual(recorded_messages[0]["role"], "system")
            self.assertEqual(recorded_messages[0]["content"], "Specialized System Contract")


if __name__ == "__main__":
    unittest.main()
