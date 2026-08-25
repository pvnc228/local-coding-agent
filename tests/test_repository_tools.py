import os
import subprocess
import tempfile
import unittest
import json
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

from local_coding_agent.repository_tools import (
    BoundedRepositoryTools,
    ToolCancelled,
    ToolPolicyError,
    _fold_path,
)
from local_coding_agent.task import TaskEnvelope


class AllowlistFoldTests(unittest.TestCase):
    def test_allowlist_matching_is_case_sensitive_on_case_sensitive_fs(self):
        if os.name == "nt":
            self.assertEqual(_fold_path("SRC/allowed.py"), _fold_path("src/allowed.py"))
        else:
            self.assertNotEqual(_fold_path("SRC/allowed.py"), _fold_path("src/allowed.py"))


class RepositoryToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        (self.workspace / "src").mkdir()
        (self.workspace / "src" / "allowed.py").write_text("VALUE = 42\n", encoding="utf-8")
        (self.workspace / "secret.txt").write_text("do not expose\n", encoding="utf-8")
        self.task = TaskEnvelope(
            id="read-one",
            goal="прочитать разрешённый файл",
            files=("src/allowed.py",),
            checks=(),
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_read_file_returns_only_allowlisted_content(self):
        tools = BoundedRepositoryTools(self.workspace, self.task)

        result = tools.execute("read_file", {"path": "src/allowed.py"})

        self.assertEqual(
            result,
            {"path": "src/allowed.py", "content": "VALUE = 42\n", "truncated": False},
        )
        with self.assertRaisesRegex(ToolPolicyError, "allowlist"):
            tools.execute("read_file", {"path": "secret.txt"})
        with self.assertRaises(ToolPolicyError):
            tools.execute("read_file", {"path": "../secret.txt"})
        with self.assertRaises(ToolPolicyError):
            tools.execute("read_file", {"path": str((self.workspace / "src" / "allowed.py").resolve())})

    def test_read_file_bounds_utf8_tool_result_without_splitting_text(self):
        long_content = "Привет мир! " * 40
        (self.workspace / "src" / "allowed.py").write_text(long_content, encoding="utf-8")
        tools = BoundedRepositoryTools(self.workspace, self.task, max_tool_result_bytes=96)

        result = tools.execute("read_file", {"path": "src/allowed.py"})

        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False).encode("utf-8")), 96)
        result["content"].encode("utf-8").decode("utf-8")

    def test_search_text_returns_bounded_matches_from_allowlisted_files(self):
        (self.workspace / "src" / "allowed.py").write_text(
            "VALUE = 42\nother = VALUE\n", encoding="utf-8"
        )
        tools = BoundedRepositoryTools(self.workspace, self.task)

        result = tools.execute(
            "search_text",
            {"query": "VALUE", "paths": ["src/allowed.py"]},
        )

        self.assertEqual(
            result,
            {
                "matches": [
                    {"path": "src/allowed.py", "line": 1, "text": "VALUE = 42"},
                    {"path": "src/allowed.py", "line": 2, "text": "other = VALUE"},
                ],
                "truncated": False,
            },
        )

    def test_search_text_rejects_oversized_file(self):
        large = "x" * 200
        (self.workspace / "src" / "allowed.py").write_text(large, encoding="utf-8")
        tools = BoundedRepositoryTools(self.workspace, self.task, max_patch_bytes=100)

        with self.assertRaises(ToolPolicyError):
            tools.execute("search_text", {"query": "x", "paths": ["src/allowed.py"]})

    def test_list_files_stays_inside_requested_workspace_directory(self):
        result = BoundedRepositoryTools(self.workspace, self.task).execute(
            "list_files",
            {"path": "src"},
        )

        self.assertEqual(result, {"files": ["src/allowed.py"], "truncated": False})

    def test_list_files_does_not_expose_non_allowlisted_files_from_workspace_root(self):
        result = BoundedRepositoryTools(self.workspace, self.task).execute(
            "list_files",
            {"path": "."},
        )

        self.assertEqual(result, {"files": ["src/allowed.py"], "truncated": False})

    def test_propose_patch_returns_valid_diff_without_writing_files(self):
        patch = (
            "diff --git a/src/allowed.py b/src/allowed.py\n"
            "--- a/src/allowed.py\n"
            "+++ b/src/allowed.py\n"
            "@@ -1 +1 @@\n"
            "-VALUE = 42\n"
            "+VALUE = 43\n"
        )
        tools = BoundedRepositoryTools(self.workspace, self.task)

        result = tools.execute("propose_patch", {"patch": patch})

        self.assertEqual(result, {"patch": patch, "files": ["src/allowed.py"]})
        self.assertEqual(
            (self.workspace / "src" / "allowed.py").read_text(encoding="utf-8"),
            "VALUE = 42\n",
        )
        outside_patch = patch.replace("src/allowed.py", "secret.txt")
        with self.assertRaisesRegex(ToolPolicyError, "allowlist"):
            tools.execute("propose_patch", {"patch": outside_patch})

    def test_propose_patch_rejects_malformed_corrupt_hunk(self):
        patch = (
            "diff --git a/src/allowed.py b/src/allowed.py\n"
            "--- a/src/allowed.py\n"
            "+++ b/src/allowed.py\n"
            "@@ -1,2 +1,2 @@\n"
            "-VALUE = 42\n"
            "+VALUE = 43\n"
        )
        result = BoundedRepositoryTools(self.workspace, self.task).execute(
            "propose_patch", {"patch": patch}
        )
        self.assertFalse(result.get("ok", True))
        self.assertIn("error", result)

    def test_propose_patch_rejects_valid_diff_with_wrong_file_context(self):
        patch = (
            "diff --git a/src/allowed.py b/src/allowed.py\n"
            "--- a/src/allowed.py\n"
            "+++ b/src/allowed.py\n"
            "@@ -1 +1 @@\n"
            "-VALUE = 41\n"
            "+VALUE = 43\n"
        )
        result = BoundedRepositoryTools(self.workspace, self.task).execute(
            "propose_patch", {"patch": patch}
        )
        self.assertFalse(result.get("ok", True))
        self.assertIn("does not apply", result["error"])

    def test_run_tests_executes_only_an_exactly_allowlisted_command(self):
        command = f'"{sys.executable}" -B -c "print(\'check ok\')"'
        task = TaskEnvelope(
            id="run-check",
            goal="запустить проверку",
            files=("src/allowed.py",),
            checks=(command,),
        )
        tools = BoundedRepositoryTools(self.workspace, task)

        result = tools.execute("run_tests", {"command": command})

        self.assertTrue(result["passed"])
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("check ok", result["stdout"])
        with self.assertRaisesRegex(ToolPolicyError, "allowlisted"):
            tools.execute("run_tests", {"command": "python -c \"print(1)\""})

    def test_run_tests_keeps_external_evidence_inside_result_limit(self):
        task = TaskEnvelope(
            id="bounded-check",
            goal="запустить короткую проверку",
            files=("src/allowed.py",),
            checks=("exit 0",),
        )
        tools = BoundedRepositoryTools(self.workspace, task, max_tool_result_bytes=200)

        result = tools.execute("run_tests", {"command": "exit 0"})

        self.assertIn("evidence", result)
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False).encode("utf-8")), 200)

    def test_run_tests_uses_sanitized_environment_and_reports_isolated_process(self):
        command = f'"{sys.executable}" -B -c "import os; print(os.getenv(\'AGENT_ISOLATION_SENTINEL\', \'missing\'))"'
        task = TaskEnvelope(
            id="isolated-check",
            goal="запустить изолированную проверку",
            files=("src/allowed.py",),
            checks=(command,),
        )
        tools = BoundedRepositoryTools(self.workspace, task)

        with patch.dict(os.environ, {"AGENT_ISOLATION_SENTINEL": "must-not-leak"}):
            result = tools.execute("run_tests", {"command": command})

        self.assertTrue(result["passed"])
        self.assertEqual(result["stdout"].strip(), "missing")
        self.assertTrue(result["isolated"])

    def test_run_tests_drains_verbose_child_output_without_deadlock(self):
        command = f'"{sys.executable}" -B -c "print(\'x\' * 200000)"'
        task = TaskEnvelope(
            id="verbose-check",
            goal="запустить многословную проверку",
            files=("src/allowed.py",),
            checks=(command,),
        )
        tools = BoundedRepositoryTools(
            self.workspace, task, max_tool_result_bytes=512, test_timeout_seconds=3
        )

        result = tools.execute("run_tests", {"command": command})

        self.assertTrue(result["passed"])
        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False).encode("utf-8")), 512)

    def test_run_tests_uses_bounded_stream_collectors_not_unbounded_tempfiles(self):
        command = f'"{sys.executable}" -B -c "print(\'x\' * 200000)"'
        task = TaskEnvelope(
            id="bounded-stream",
            goal="проверить bounded output",
            files=("src/allowed.py",),
            checks=(command,),
        )
        tools = BoundedRepositoryTools(self.workspace, task, max_tool_result_bytes=512)

        with patch("tempfile.TemporaryFile", side_effect=AssertionError):
            result = tools.execute("run_tests", {"command": command})

        self.assertTrue(result["passed"])
        self.assertTrue(result["truncated"])

    def test_termination_failure_is_bounded_and_reported(self):
        class StuckProcess:
            pid = 123
            returncode = None

            def poll(self):
                return None

            def wait(self, timeout=None):
                raise subprocess.TimeoutExpired("wait", timeout)

            def kill(self):
                return None

        process = StuckProcess()
        tools = BoundedRepositoryTools(self.workspace, self.task)

        with patch.object(
            BoundedRepositoryTools,
            "_kill_tree",
            return_value=(False, "taskkill failed: access denied"),
        ):
            with self.assertRaisesRegex(ToolPolicyError, "did not terminate"):
                tools._terminate(process)

    def test_run_tests_cancels_running_command(self):
        command = f'"{sys.executable}" -B -c "import time; time.sleep(30)"'
        task = TaskEnvelope(
            id="cancel-check",
            goal="запустить и прервать проверку",
            files=("src/allowed.py",),
            checks=(command,),
        )
        tools = BoundedRepositoryTools(
            self.workspace, task, cancel_event=threading.Event()
        )
        holder = {}

        def run_tool():
            try:
                holder["result"] = tools.execute("run_tests", {"command": command})
            except Exception as error:  # noqa: BLE001 - captured to inspect in main thread
                holder["error"] = error

        thread = threading.Thread(target=run_tool)
        thread.start()
        time.sleep(0.2)
        tools.cancel_event.set()
        thread.join(timeout=10)

        self.assertFalse(thread.is_alive())
        self.assertIsInstance(holder.get("error"), ToolCancelled)

    def test_propose_patch_accepts_search_replace_edits(self):
        tools = BoundedRepositoryTools(self.workspace, self.task)
        result = tools.execute(
            "propose_patch",
            {
                "edits": [
                    {
                        "file": "src/allowed.py",
                        "search": "VALUE = 42",
                        "replace": "VALUE = 43",
                    }
                ]
            },
        )

        self.assertEqual(result["files"], ["src/allowed.py"])
        self.assertIn("+VALUE = 43", result["patch"])
        self.assertEqual(
            (self.workspace / "src" / "allowed.py").read_text(encoding="utf-8"),
            "VALUE = 42\n",
        )

    def test_propose_patch_rejects_edits_with_bad_search(self):
        tools = BoundedRepositoryTools(self.workspace, self.task)
        result = tools.execute(
            "propose_patch",
            {
                "edits": [
                    {"file": "src/allowed.py", "search": "NOPE", "replace": "VALUE = 43"}
                ]
            },
        )
        self.assertFalse(result.get("ok", True))
        self.assertIn("not found", result["error"])


    def test_propose_patch_rejects_both_patch_and_edits(self):
        tools = BoundedRepositoryTools(self.workspace, self.task)
        with self.assertRaisesRegex(ToolPolicyError, "not both"):
            tools.execute(
                "propose_patch",
                {
                    "patch": "diff --git a/src/allowed.py b/src/allowed.py\n",
                    "edits": [
                        {"file": "src/allowed.py", "search": "VALUE = 42", "replace": "VALUE = 43"}
                    ],
                },
            )

    def test_tool_calls_are_recorded_as_audit_events(self):
        tools = BoundedRepositoryTools(self.workspace, self.task)
        tools.execute("read_file", {"path": "src/allowed.py"})
        with self.assertRaises(ToolPolicyError):
            tools.execute("read_file", {"path": "secret.txt"})

        self.assertEqual([event["name"] for event in tools.audit_events], ["read_file", "read_file"])
        self.assertTrue(tools.audit_events[0]["success"])
        self.assertFalse(tools.audit_events[1]["success"])

    def test_blocked_tools_raise_policy_error_in_read_only_mode(self):
        tools = BoundedRepositoryTools(
            self.workspace, self.task, blocked_tools={"propose_patch", "run_tests"}
        )

        with self.assertRaisesRegex(ToolPolicyError, "blocked in read-only mode"):
            tools.execute("propose_patch", {"patch": "diff"})
        with self.assertRaisesRegex(ToolPolicyError, "blocked in read-only mode"):
            tools.execute("run_tests", {"command": "exit 0"})

        result = tools.execute("read_file", {"path": "src/allowed.py"})
        self.assertEqual(result["path"], "src/allowed.py")

    def test_blocked_tool_calls_are_recorded_as_audit_events(self):
        tools = BoundedRepositoryTools(
            self.workspace, self.task, blocked_tools={"propose_patch"}
        )

        with self.assertRaises(ToolPolicyError):
            tools.execute("propose_patch", {"patch": "diff"})

        event = tools.audit_events[-1]
        self.assertEqual(event["name"], "propose_patch")
        self.assertFalse(event["success"])
        self.assertIn("blocked in read-only mode", event["error"])

    def test_write_tools_still_work_without_blocked_tools(self):
        patch = (
            "diff --git a/src/allowed.py b/src/allowed.py\n"
            "--- a/src/allowed.py\n"
            "+++ b/src/allowed.py\n"
            "@@ -1 +1 @@\n"
            "-VALUE = 42\n"
            "+VALUE = 43\n"
        )
        task = TaskEnvelope(
            id="unblocked-write",
            goal="проверить запись",
            files=("src/allowed.py",),
            checks=("exit 0",),
        )
        tools = BoundedRepositoryTools(self.workspace, task)

        propose = tools.execute("propose_patch", {"patch": patch})
        self.assertEqual(propose["files"], ["src/allowed.py"])
        run = tools.execute("run_tests", {"command": "exit 0"})
        self.assertTrue(run["passed"])


class RunTestsHardeningTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def _task(command: str) -> TaskEnvelope:
        return TaskEnvelope(
            id="hardening-check",
            goal="проверить изоляцию запуска проверок",
            files=("src/allowed.py",),
            checks=(command,),
        )

    def test_isolated_environment_scrubs_secret_named_vars(self):
        secrets = {
            "MY_API_KEY": "k1",
            "AUTH_TOKEN": "t1",
            "AWS_SECRET_ACCESS_KEY": "s1",
            "DATABASE_PASSWORD": "p1",
            "GITHUB_TOKEN": "g1",
            "AZURE_CLIENT_SECRET": "a1",
        }
        with patch.dict(os.environ, {**secrets, "LANG": "C.UTF-8"}):
            env = BoundedRepositoryTools._isolated_environment()

        for name in secrets:
            self.assertNotIn(name, env)
        self.assertEqual(env.get("LANG"), "C.UTF-8")
        self.assertIn("PATH", env)

    def test_run_tests_happy_path_unaffected_by_hardening(self):
        command = f'"{sys.executable}" -B -c "print(\'check ok\')"'
        tools = BoundedRepositoryTools(self.workspace, self._task(command))

        result = tools.execute("run_tests", {"command": command})

        self.assertTrue(result["passed"])
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("check ok", result["stdout"])

    def test_process_group_options_shape_per_platform(self):
        options = BoundedRepositoryTools._process_group_options(60)

        if os.name == "nt":
            self.assertTrue(
                options["creationflags"] & getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
        else:
            self.assertIn("preexec_fn", options)
            self.assertNotIn("start_new_session", options)

    @unittest.skipIf(os.name != "posix", "POSIX process-group guard")
    def test_posix_child_gets_own_process_group(self):
        command = (
            f'"{sys.executable}" -B -c "import os; print(os.getpid(), os.getpgid(0))"'
        )
        tools = BoundedRepositoryTools(self.workspace, self._task(command))

        result = tools.execute("run_tests", {"command": command})

        self.assertTrue(result["passed"])
        child_pid, child_pgid = result["stdout"].split()
        self.assertEqual(child_pid, child_pgid)

    @unittest.skipIf(os.name != "posix", "POSIX rlimit guard")
    def test_posix_child_runs_under_cpu_and_nofile_rlimits(self):
        command = (
            f'"{sys.executable}" -B -c "import resource; '
            "print(resource.getrlimit(resource.RLIMIT_CPU)[0], "
            'resource.getrlimit(resource.RLIMIT_NOFILE)[0])"'
        )
        timeout = 5
        tools = BoundedRepositoryTools(
            self.workspace, self._task(command), test_timeout_seconds=timeout
        )

        result = tools.execute("run_tests", {"command": command})

        self.assertTrue(result["passed"])
        cpu_soft, nofile_soft = (int(value) for value in result["stdout"].split())
        self.assertEqual(cpu_soft, timeout + 30)
        self.assertEqual(nofile_soft, 256)


if __name__ == "__main__":
    unittest.main()
