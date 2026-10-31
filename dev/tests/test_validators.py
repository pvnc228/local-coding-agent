import tempfile
import unittest
from pathlib import Path

from local_coding_agent.task import TaskEnvelope
from local_coding_agent.validators import validate_candidate


class CandidateValidatorTests(unittest.TestCase):
    def setUp(self):
        self.task = TaskEnvelope(
            id="validate-one",
            goal="изменить разрешённый файл",
            files=("src/allowed.py",),
            checks=("check allowed",),
        )
        self.patch = (
            "diff --git a/src/allowed.py b/src/allowed.py\n"
            "--- a/src/allowed.py\n"
            "+++ b/src/allowed.py\n"
            "@@ -1 +1 @@\n"
            "-VALUE = 42\n"
            "+VALUE = 43\n"
        )

    def test_valid_candidate_reports_changed_allowlisted_files(self):
        report = validate_candidate(
            {
                "status": "candidate",
                "summary": "изменено значение",
                "patch": self.patch,
                "checks": [
                    {"command": "check allowed", "passed": True, "evidence": "runner exit 0"}
                ],
                "risks": [],
            },
            self.task,
            observed_checks={"check allowed": {"passed": True, "evidence": "runner exit 0"}},
        )

        self.assertTrue(report.valid)
        self.assertEqual(report.changed_files, ("src/allowed.py",))
        self.assertEqual(report.issues, ())

    def test_validator_rejects_out_of_scope_patch_and_untrusted_check(self):
        report = validate_candidate(
            {
                "status": "candidate",
                "summary": "опасное изменение",
                "patch": self.patch.replace("src/allowed.py", "secret.txt"),
                "checks": [
                    {"command": "Remove-Item *", "passed": True, "evidence": "claimed"}
                ],
                "risks": [],
            },
            self.task,
        )

        self.assertFalse(report.valid)
        self.assertTrue(any("allowlist" in issue for issue in report.issues))
        self.assertTrue(any("allowlisted" in issue for issue in report.issues))

    def test_validator_accepts_rephrased_evidence_with_matching_exit_code(self):
        report = validate_candidate(
            {
                "status": "candidate",
                "summary": "перефразировано",
                "patch": "",
                "checks": [
                    {
                        "command": "check allowed",
                        "passed": True,
                        "evidence": "exit_code=0; passed=True; stdout_bytes=123",
                    }
                ],
                "risks": [],
            },
            self.task,
            observed_checks={
                "check allowed": {
                    "passed": True,
                    "evidence": "exit_code=0; passed=True; stdout_bytes=999; stderr_bytes=0; truncated=False",
                }
            },
        )

        self.assertTrue(report.valid)
        self.assertEqual(report.issues, ())

    def test_validator_rejects_evidence_with_wrong_exit_code(self):
        report = validate_candidate(
            {
                "status": "candidate",
                "summary": "неверный код выхода",
                "patch": "",
                "checks": [
                    {
                        "command": "check allowed",
                        "passed": True,
                        "evidence": "exit_code=1; passed=True; stdout_bytes=0",
                    }
                ],
                "risks": [],
            },
            self.task,
            observed_checks={
                "check allowed": {
                    "passed": True,
                    "evidence": "exit_code=0; passed=True; stdout_bytes=0",
                }
            },
        )

        self.assertFalse(report.valid)
        self.assertTrue(
            any("evidence disagrees" in issue for issue in report.issues)
        )

    def test_validator_requires_external_evidence_for_every_allowlisted_check(self):
        report = validate_candidate(
            {
                "status": "candidate",
                "summary": "без runner",
                "patch": "",
                "checks": [{"command": "check allowed", "passed": True, "evidence": "claimed"}],
                "risks": [],
            },
            self.task,
            observed_checks={},
        )

        self.assertFalse(report.valid)
        self.assertTrue(any("external runner" in issue for issue in report.issues))

    def test_validator_rejects_candidate_when_external_check_failed(self):
        report = validate_candidate(
            {
                "status": "candidate",
                "summary": "проверка не прошла",
                "patch": "",
                "checks": [
                    {"command": "check allowed", "passed": False, "evidence": "exit_code=1"}
                ],
                "risks": [],
            },
            self.task,
            observed_checks={"check allowed": {"passed": False, "evidence": "exit_code=1"}},
        )

        self.assertFalse(report.valid)
        self.assertIn("check failed: check allowed", report.issues)

    def test_validator_rejects_malformed_patch_with_git(self):
        # A hunk that declares more lines than it carries is a corrupt patch;
        # the parser no longer flags the count mismatch itself, but git apply
        # still rejects it, preserving the security intent.
        malformed = self.patch.replace("@@ -1 +1 @@", "@@ -1,2 +1,2 @@")

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            target = workspace / "src" / "allowed.py"
            target.parent.mkdir()
            target.write_text("VALUE = 42\n", encoding="utf-8")
            report = validate_candidate(
                {
                    "status": "candidate",
                    "summary": "неверный hunk",
                    "patch": malformed,
                    "checks": [],
                    "risks": [],
                },
                self.task,
                workspace_root=workspace,
            )

        self.assertFalse(report.valid)
        self.assertTrue(
            any(
                "does not apply" in issue or "corrupt" in issue
                for issue in report.issues
            )
        )

    def test_validator_accepts_patch_with_mismatched_hunk_counts_when_git_applies(self):
        # The header declares a one-line hunk (@@ -1 +1 @@) but the body adds a
        # second new line (new_seen=2 vs new_expected=1). git applies it cleanly;
        # the parser no longer rejects the count mismatch, so git is the gate.
        patch = (
            "diff --git a/src/allowed.py b/src/allowed.py\n"
            "--- a/src/allowed.py\n"
            "+++ b/src/allowed.py\n"
            "@@ -1 +1 @@\n"
            "-VALUE = 42\n"
            "+VALUE = 43\n"
            "+NEW = 5\n"
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            target = workspace / "src" / "allowed.py"
            target.parent.mkdir()
            target.write_text("VALUE = 42\n", encoding="utf-8")
            report = validate_candidate(
                {
                    "status": "candidate",
                    "summary": "допустимый hunk с неверным count",
                    "patch": patch,
                    "checks": [],
                    "risks": [],
                },
                TaskEnvelope(
                    id="validate-no-checks",
                    goal="проверить diff",
                    files=("src/allowed.py",),
                ),
                workspace_root=workspace,
            )

        self.assertTrue(report.valid)
        self.assertEqual(report.issues, ())

    def test_validator_rejects_patch_that_does_not_apply_to_workspace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            target = workspace / "src" / "allowed.py"
            target.parent.mkdir()
            target.write_text("VALUE = 42\n", encoding="utf-8")
            report = validate_candidate(
                {
                    "status": "candidate",
                    "summary": "неверный контекст",
                    "patch": self.patch.replace("VALUE = 42", "VALUE = 41"),
                    "checks": [],
                    "risks": [],
                },
                TaskEnvelope(
                    id="validate-applicability",
                    goal="проверить применимость",
                    files=("src/allowed.py",),
                ),
                workspace_root=workspace,
            )

        self.assertFalse(report.valid)
        self.assertTrue(any("does not apply" in issue for issue in report.issues))


if __name__ == "__main__":
    unittest.main()
