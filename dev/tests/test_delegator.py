import json
import unittest

from local_coding_agent.atomizer import TaskBudget
from local_coding_agent.delegator import (
    DelegatingAgent,
    _combined_diff,
    _failed_test_tasks,
    _narrow_check,
    _planner_call,
    is_decomposable_failure,
    run_quality_strategy,
)
from local_coding_agent.controller import Controller
from local_coding_agent.task import TaskEnvelope


def _envelope(task_id, files):
    return TaskEnvelope(id=task_id, goal="разобрать", files=files)


class DelegatingAgentTests(unittest.TestCase):
    def _agent(self, delegate, **kwargs):
        return DelegatingAgent(
            delegate,
            workspace_ref="repo",
            model_profile="qwen2.5-1.5b",
            budget=TaskBudget(max_files=2),
            **kwargs,
        )

    def test_is_decomposable_failure_detects_known_kinds(self):
        for kind in (
            "preflight_rejected",
            "context_limit",
            "max_turns",
            "too_many_files",
            "repair_budget_exhausted",
        ):
            with self.subTest(kind=kind):
                self.assertTrue(is_decomposable_failure({"error": {"kind": kind}}))
        self.assertFalse(is_decomposable_failure({"error": {"kind": "cancelled"}}))
        self.assertFalse(is_decomposable_failure({"status": "accepted"}))

    def test_successful_child_is_not_redecomposed(self):
        calls = []

        def delegate(caller_id, request):
            calls.append(request.request_id)
            return {"status": "accepted", "summary": "ok"}

        result = self._agent(delegate).run(
            "caller", _envelope("wide", ("a.py", "b.py", "c.py")), independent_files=True
        )

        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["splits"], 0)
        self.assertEqual(len(calls), 2)
        self.assertEqual(
            {leaf["task_id"] for leaf in result["children"]}, {"wide#1", "wide#2"}
        )

    def test_decomposable_failure_splits_further_per_file(self):
        outcomes = {}

        def delegate(caller_id, request):
            outcomes[request.request_id] = request.task
            if request.task.id == "wide#1":
                return {"status": "failed", "error": {"kind": "context_limit"}}
            return {"status": "accepted", "summary": "ok"}

        result = self._agent(delegate, max_depth=3).run(
            "caller", _envelope("wide", ("a.py", "b.py", "c.py", "d.py")), independent_files=True
        )

        self.assertEqual(result["status"], "accepted")
        self.assertGreaterEqual(result["splits"], 1)
        self.assertIn("wide#1@1.0", outcomes)
        self.assertIn("wide#1@1.1", outcomes)

    def test_non_decomposable_failure_stops(self):
        calls = []

        def delegate(caller_id, request):
            calls.append(request.request_id)
            return {"status": "failed", "error": {"kind": "cancelled"}}

        result = self._agent(delegate).run("caller", _envelope("wide", ("a.py", "b.py")))

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["splits"], 0)
        self.assertEqual(len(calls), 1)

    def test_depth_cap_stops_endless_redecomposition(self):
        def delegate(caller_id, request):
            return {"status": "failed", "error": {"kind": "preflight_rejected"}}

        result = self._agent(delegate, max_depth=2).run(
            "caller", _envelope("wide", ("a.py",))
        )

        self.assertLessEqual(result["splits"], 1)
        self.assertEqual(result["status"], "failed")

    def test_parallel_children_keep_results_ordered(self):
        calls = []

        def delegate(caller_id, request):
            calls.append(request.task.id)
            return {"status": "accepted", "summary": request.task.id}

        agent = self._agent(delegate, max_parallel_children=3)
        result = agent.run("caller", _envelope("wide", ("a.py", "b.py", "c.py", "d.py")), independent_files=True)

        self.assertEqual(result["status"], "accepted")
        self.assertEqual(sorted(calls), ["wide#1", "wide#2"])
        self.assertEqual(
            [leaf["result"]["summary"] for leaf in result["children"]],
            ["wide#1", "wide#2"],
        )

    def test_parallel_children_redecompose_failures(self):
        def delegate(caller_id, request):
            if request.task.id == "wide#1":
                return {"status": "failed", "error": {"kind": "context_limit"}}
            return {"status": "accepted", "summary": request.task.id}

        agent = self._agent(delegate, max_parallel_children=2, max_depth=3)
        result = agent.run("caller", _envelope("wide", ("a.py", "b.py", "c.py")), independent_files=True)

        self.assertEqual(result["status"], "accepted")
        self.assertGreaterEqual(result["splits"], 1)

    def test_legacy_split_requires_explicit_independent_files(self):
        calls = []
        agent = self._agent(lambda caller, request: calls.append(request) or {'status': 'accepted'})
        result = agent.run('caller', _envelope('dependent', ('a.py', 'b.py', 'c.py')))
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error']['kind'], 'independent_files_required')
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()


class _QualityScripted:
    def __init__(self):
        self.calls = 0

    def chat(self, messages, *, tools=None):
        self.calls += 1
        if "Plan the coding task" in messages[0]["content"]:
            content = {
                "steps": [
                    {
                        "goal": "set the requested value",
                        "files": ["value.py"],
                        "check": "python -m pytest -q -p no:cacheprovider test_value.py",
                    }
                ]
            }
        else:
            content = {
                "status": "candidate",
                "summary": "set value",
                "checks": [],
                "risks": [],
                "edits": [
                    {"file": "value.py", "search": "VALUE = 1\n", "replace": "VALUE = 3\n"}
                ],
            }
        return {"message": {"role": "assistant", "content": json.dumps(content)}}


def test_quality_plan_returns_one_combined_patch_and_preserves_workspace(tmp_path):
    (tmp_path / "value.py").write_text("VALUE = 1\n")
    (tmp_path / "test_value.py").write_text(
        "from value import VALUE\n\n"
        "def test_value():\n"
        "    assert VALUE == 3\n"
    )
    task = TaskEnvelope(
        id="quality",
        goal="fix value",
        files=("value.py",),
        checks=("python -m pytest -q -p no:cacheprovider test_value.py",),
    )
    model = _QualityScripted()
    result = run_quality_strategy(Controller(model, str(tmp_path), max_turns=1), task, "plan")
    assert result["status"] == "accepted"
    assert result["verification"] == {"mode": "scratch", "rounds": 1}
    assert "VALUE = 3" in result["patch"]
    assert (tmp_path / "value.py").read_text() == "VALUE = 1\n"
    assert model.calls == 2


def test_failed_test_split_narrows_each_child_to_one_allowlisted_node():
    task = TaskEnvelope(
        id="repair",
        goal="repair tests",
        files=("value.py",),
        checks=("python -m pytest -q test_value.py",),
    )
    children, reason = _failed_test_tasks(
        task,
        [
            {
                "command": "python -m pytest -q test_value.py",
                "passed": False,
                "stdout": "FAILED test_value.py::test_one\nFAILED test_value.py::test_two\n",
            }
        ],
    )
    assert reason is None
    assert [child.checks for child in children] == [
        ('python -m pytest -q "test_value.py::test_one"',),
        ('python -m pytest -q "test_value.py::test_two"',),
    ]
    assert all("Сделай так, чтобы прошёл" in child.goal for child in children)


def test_failed_test_split_rejects_shell_wrappers_and_unapproved_commands():
    task = TaskEnvelope(
        id="repair",
        goal="repair tests",
        files=("value.py",),
        checks=("python -m pytest -q test_value.py; echo unsafe",),
    )
    assert _narrow_check(
        "python -m pytest -q test_value.py; echo unsafe", "test_value.py::test_one"
    ) is None
    children, reason = _failed_test_tasks(
        task,
        [{
            "command": "pytest -q test_value.py",
            "passed": False,
            "stdout": "FAILED test_value.py::test_one\nFAILED test_value.py::test_two\n",
        }],
    )
    assert children == ()
    assert "failed check command" in reason


def test_combined_diff_includes_created_deleted_and_no_newline_files(tmp_path):
    original = {"new.py": None, "deleted.py": b"old\n", "changed.py": b"old"}
    (tmp_path / "new.py").write_text("created\n", newline="")
    (tmp_path / "changed.py").write_bytes(b"new")
    patch, error = _combined_diff(original, tmp_path)
    assert error is None
    assert "new file mode 100644" in patch
    assert "--- /dev/null" in patch
    assert "deleted file mode 100644" in patch
    assert "+++ /dev/null" in patch
    assert "\\ No newline at end of file" in patch


class _RejectedChildScripted:
    def chat(self, messages, *, tools=None):
        return {"message": {"role": "assistant", "content": "not-json"}}


def test_per_file_does_not_accept_rejected_child_without_checks(tmp_path):
    (tmp_path / "value.py").write_text("VALUE = 1\n")
    task = TaskEnvelope(id="reject", goal="fix", files=("value.py",))
    result = run_quality_strategy(
        Controller(_RejectedChildScripted(), str(tmp_path), max_turns=1, max_retries=0),
        task,
        "per_file",
    )
    assert result["status"] == "rejected"
    assert result["error"]["kind"] == "child_failed"
    assert result["audit"]


def test_planner_honors_cancel_and_context_budget(tmp_path):
    class Model:
        def __init__(self):
            self.calls = 0

        def chat(self, messages, *, tools=None):
            self.calls += 1
            return {"message": {"content": "{}"}}

    event = __import__("threading").Event()
    event.set()
    model = Model()
    controller = Controller(model, str(tmp_path), max_context_bytes=100_000)
    task = TaskEnvelope(id="cancel", goal="x", files=("value.py",))
    plan, error, calls = _planner_call(controller, task, cancel_event=event)
    assert plan is None and "cancel" in error and calls == 0 and model.calls == 0

    model = Model()
    controller = Controller(model, str(tmp_path), max_context_bytes=64)
    task = TaskEnvelope(id="large", goal="x" * 500, files=("value.py",))
    plan, error, calls = _planner_call(controller, task)
    assert plan is None and "context" in error and calls == 0 and model.calls == 0


class _DependentPerFileScripted:
    def chat(self, messages, *, tools=None):
        payload = json.loads(messages[1]["content"])
        file_name = payload["files"][0]
        if file_name == "a.py":
            edit = {"file": "a.py", "search": "VALUE = 1\n", "replace": "VALUE = 3\n"}
        else:
            edit = {"file": "b.py", "search": "from a import VALUE\n", "replace": "from a import VALUE\nRESULT = VALUE\n"}
        return {"message": {"role": "assistant", "content": json.dumps({
            "status": "candidate", "summary": "dependent fix", "checks": [], "risks": [], "edits": [edit]
        })}}


def test_per_file_children_accumulate_dependent_two_file_state(tmp_path):
    (tmp_path / "a.py").write_text("VALUE = 1\n")
    (tmp_path / "b.py").write_text("from a import VALUE\n")
    (tmp_path / "test_dependent.py").write_text(
        "from b import RESULT\n\n"
        "def test_result():\n    assert RESULT == 3\n"
    )
    task = TaskEnvelope(
        id="dependent",
        goal="update dependent modules",
        files=("a.py", "b.py"),
        checks=("python -m pytest -q -p no:cacheprovider test_dependent.py",),
    )
    result = run_quality_strategy(
        Controller(_DependentPerFileScripted(), str(tmp_path), max_turns=1), task, "per_file"
    )
    assert result["status"] == "accepted"
    assert "VALUE = 3" in result["patch"]
    assert "RESULT = VALUE" in result["patch"]


def test_combined_patch_keeps_parent_file_limit(tmp_path):
    (tmp_path / 'a.py').write_text('VALUE = 1\n')
    (tmp_path / 'b.py').write_text('from a import VALUE\n')
    task = TaskEnvelope(id='limit', goal='fix dependent files', files=('a.py', 'b.py'))
    result = run_quality_strategy(Controller(_DependentPerFileScripted(), str(tmp_path),
                                  max_turns=1, max_patch_files=1), task, 'per_file')
    assert result['status'] == 'rejected'
    assert result['error']['kind'] == 'combined_validation_failed'


def test_pytest_node_cannot_expand_cmd_environment():
    from local_coding_agent.delegator import _narrow_check
    assert _narrow_check('python -m pytest tests', 'tests/test_x.py::test_value[%PAYLOAD%]') is None


def test_cancelled_planner_completion_waits_for_model_to_finish(tmp_path):
    from threading import Event, Thread
    started, release, finished, cancel, completed = (Event() for _ in range(5))
    class Blocking:
        def chat(self, messages, **kwargs):
            started.set()
            release.wait(5)
            finished.set()
            return {'message': {'content': '{}'}}
    controller = Controller(Blocking(), str(tmp_path), cancel_event=cancel)
    task = TaskEnvelope(id='cancel', goal='plan', files=('a.py',))
    thread = Thread(target=lambda: run_quality_strategy(controller, task, 'plan',
                    cancel_event=cancel, completion_event=completed))
    try:
        thread.start()
        assert started.wait(2)
        cancel.set()
        thread.join(2)
        assert not thread.is_alive()
        assert not finished.is_set()
        assert not completed.is_set()
    finally:
        release.set()
        thread.join(2)
    assert completed.wait(2)


class _TestRepairScripted:
    def __init__(self):
        self.calls = 0

    def chat(self, messages, *, tools=None):
        self.calls += 1
        payload = json.loads(messages[1]["content"])
        checks = payload.get("checks", [])
        if checks and "::test_one" in checks[0]:
            edit = {"file": "value.py", "search": "VALUE_1 = 1\n", "replace": "VALUE_1 = 3\n"}
        elif checks and "::test_two" in checks[0]:
            edit = {"file": "value.py", "search": "VALUE_2 = 1\n", "replace": "VALUE_2 = 4\n"}
        else:
            edit = {"file": "value.py", "search": "VALUE_1 = 1\n", "replace": "VALUE_1 = 0\n"}
        content = {"status": "candidate", "summary": "repair", "checks": [], "risks": [], "edits": [edit]}
        return {"message": {"role": "assistant", "content": json.dumps(content)}}


def test_tests_strategy_repairs_failed_nodes_sequentially(tmp_path):
    (tmp_path / "value.py").write_text("VALUE_1 = 1\nVALUE_2 = 1\n")
    (tmp_path / "test_value.py").write_text(
        "from value import VALUE_1, VALUE_2\n\n"
        "def test_one():\n    assert VALUE_1 == 3\n\n"
        "def test_two():\n    assert VALUE_2 == 4\n"
    )
    task = TaskEnvelope(
        id="repair",
        goal="repair values",
        files=("value.py",),
        checks=("python -m pytest -q -p no:cacheprovider test_value.py",),
    )
    model = _TestRepairScripted()
    result = run_quality_strategy(
        Controller(model, str(tmp_path), max_turns=1, max_repair_rounds=1), task, "tests"
    )
    assert result["status"] == "accepted"
    assert result["verification"]["mode"] == "scratch"
    assert "VALUE_1 = 3" in result["patch"]
    assert "VALUE_2 = 4" in result["patch"]
    assert (tmp_path / "value.py").read_text() == "VALUE_1 = 1\nVALUE_2 = 1\n"
    assert model.calls >= 4


class _ReplanningScripted:
    def __init__(self):
        self.plans = 0
        self.candidates = 0

    def chat(self, messages, *, tools=None):
        if "Plan the coding task" in messages[0]["content"]:
            self.plans += 1
            content = {
                "steps": [{
                    "goal": "set the requested value",
                    "files": ["value.py"],
                    "check": "python -m pytest -q -p no:cacheprovider test_value.py",
                }]
            }
        else:
            self.candidates += 1
            value = 2 if self.candidates == 1 else 3
            content = {
                "status": "candidate", "summary": "set value", "checks": [], "risks": [],
                "edits": [{"file": "value.py", "search": "VALUE = 1\n", "replace": f"VALUE = {value}\n"}],
            }
        return {"message": {"role": "assistant", "content": json.dumps(content)}}


def test_plan_replans_once_after_final_full_check_failure(tmp_path):
    (tmp_path / "value.py").write_text("VALUE = 1\n")
    (tmp_path / "test_value.py").write_text(
        "from value import VALUE\n\n"
        "def test_value():\n    assert VALUE == 3\n"
    )
    task = TaskEnvelope(
        id="replan",
        goal="fix value",
        files=("value.py",),
        checks=("python -m pytest -q -p no:cacheprovider test_value.py",),
    )
    model = _ReplanningScripted()
    result = run_quality_strategy(Controller(model, str(tmp_path), max_turns=1), task, "plan")
    assert result["status"] == "accepted"
    assert result["quality"]["state"] == "replanned"
    assert model.plans == 2 and model.candidates == 2
