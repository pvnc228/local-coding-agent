import tempfile
import unittest
from pathlib import Path

from local_coding_agent.task import TaskEnvelope
from local_coding_agent.validators import (
    resolve_edits,
    validate_candidate,
)


class SearchReplaceResolutionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        (self.workspace / "src").mkdir()
        (self.workspace / "src" / "allowed.py").write_text(
            "def unique(values):\n    return sorted(set(values))\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_search_replace_to_diff_builds_valid_hunk(self):
        patch, _, _ = self._resolve(
            "    return sorted(set(values))",
            "    return list(dict.fromkeys(values))",
        )

        self.assertIn("diff --git a/src/allowed.py b/src/allowed.py", patch)
        self.assertIn("@@ -2,1 +2,1 @@", patch)
        self.assertIn("-    return sorted(set(values))", patch)
        self.assertIn("+    return list(dict.fromkeys(values))", patch)

    def test_search_replace_can_remove_final_newline(self):
        target = self.workspace / "src" / "allowed.py"
        target.write_text("VALUE = 1\n", encoding="utf-8")

        patch, _, issues = self._resolve("VALUE = 1\n", "VALUE = 2")

        self.assertEqual(issues, [])
        self.assertNotIn("-VALUE = 1\n\\ No newline at end of file", patch)
        self.assertIn("+VALUE = 2\n\\ No newline at end of file", patch)

    def test_search_replace_can_add_final_newline(self):
        target = self.workspace / "src" / "allowed.py"
        target.write_text("VALUE = 1", encoding="utf-8")

        patch, _, issues = self._resolve("VALUE = 1", "VALUE = 2\n")

        self.assertEqual(issues, [])
        self.assertIn("-VALUE = 1\n\\ No newline at end of file", patch)
        self.assertIn("+VALUE = 2\n", patch)
        self.assertNotIn("+VALUE = 2\n\\ No newline at end of file", patch)

    def _resolve(self, search, replace):
        return resolve_edits(
            self.workspace,
            [{"file": "src/allowed.py", "search": search, "replace": replace}],
            allowed_files={"src/allowed.py"},
            max_files=2,
            max_patch_bytes=32000,
        )

    def test_resolve_edits_returns_patch_and_changed_files(self):
        patch, changed, issues = resolve_edits(
            self.workspace,
            [
                {
                    "file": "src/allowed.py",
                    "search": "    return sorted(set(values))",
                    "replace": "    return list(dict.fromkeys(values))",
                }
            ],
            allowed_files={"src/allowed.py"},
            max_files=2,
            max_patch_bytes=32000,
        )

        self.assertEqual(issues, [])
        self.assertEqual(changed, ("src/allowed.py",))
        self.assertIn("+    return list(dict.fromkeys(values))", patch)

    def test_resolve_edits_rejects_search_not_found(self):
        _, _, issues = resolve_edits(
            self.workspace,
            [{"file": "src/allowed.py", "search": "not in the file", "replace": "x"}],
            allowed_files={"src/allowed.py"},
            max_files=2,
            max_patch_bytes=32000,
        )

        self.assertTrue(any("not found" in issue for issue in issues))

    def test_resolve_edits_rejects_out_of_scope_file(self):
        _, _, issues = resolve_edits(
            self.workspace,
            [{"file": "secret.txt", "search": "a", "replace": "b"}],
            allowed_files={"src/allowed.py"},
            max_files=2,
            max_patch_bytes=32000,
        )

        self.assertTrue(any("allowlist" in issue for issue in issues))

    def test_resolve_edits_rejects_ambiguous_search(self):
        (self.workspace / "src" / "allowed.py").write_text(
            "x = 1\nx = 1\n", encoding="utf-8"
        )
        _, _, issues = resolve_edits(
            self.workspace,
            [{"file": "src/allowed.py", "search": "x = 1", "replace": "x = 2"}],
            allowed_files={"src/allowed.py"},
            max_files=2,
            max_patch_bytes=32000,
        )

        self.assertTrue(any("ambiguous" in issue for issue in issues))

    def test_validate_candidate_accepts_edits_and_resolves_patch(self):
        report = validate_candidate(
            {
                "status": "candidate",
                "summary": "заменено",
                "edits": [
                    {
                        "file": "src/allowed.py",
                        "search": "    return sorted(set(values))",
                        "replace": "    return list(dict.fromkeys(values))",
                    }
                ],
                "checks": [],
                "risks": [],
            },
            TaskEnvelope(id="edit-task", goal="изменить", files=("src/allowed.py",)),
            workspace_root=self.workspace,
        )

        self.assertTrue(report.valid, report.issues)
        self.assertEqual(report.changed_files, ("src/allowed.py",))
        self.assertIn("+    return list(dict.fromkeys(values))", report.resolved_patch)

    def test_validate_candidate_rejects_patch_and_edits_together(self):
        report = validate_candidate(
            {
                "status": "candidate",
                "summary": "оба",
                "patch": "diff --git a/src/allowed.py b/src/allowed.py\n",
                "edits": [
                    {
                        "file": "src/allowed.py",
                        "search": "    return sorted(set(values))",
                        "replace": "    return list(dict.fromkeys(values))",
                    }
                ],
                "checks": [],
                "risks": [],
            },
            TaskEnvelope(id="edit-task", goal="изменить", files=("src/allowed.py",)),
            workspace_root=self.workspace,
        )

        self.assertFalse(report.valid)
        self.assertTrue(any("not both" in issue for issue in report.issues))

    def test_validate_candidate_rejects_edits_without_workspace(self):
        report = validate_candidate(
            {
                "status": "candidate",
                "summary": "без workspace",
                "edits": [
                    {
                        "file": "src/allowed.py",
                        "search": "    return sorted(set(values))",
                        "replace": "    return list(dict.fromkeys(values))",
                    }
                ],
                "checks": [],
                "risks": [],
            },
            TaskEnvelope(id="edit-task", goal="изменить", files=("src/allowed.py",)),
        )

        self.assertFalse(report.valid)
        self.assertTrue(any("workspace" in issue for issue in report.issues))

    def test_resolve_edits_auto_aligns_unique_subline_search(self):
        patch, changed, issues = resolve_edits(
            self.workspace,
            [{"file": "src/allowed.py", "search": "sorted(set(values))", "replace": "list(dict.fromkeys(values))"}],
            allowed_files={"src/allowed.py"},
            max_files=2,
            max_patch_bytes=32000,
        )

        self.assertEqual(issues, [])
        self.assertEqual(changed, ("src/allowed.py",))
        self.assertIn("-    return sorted(set(values))", patch)
    def test_resolve_edits_preserves_single_line_when_search_has_newline(self):
        target = self.workspace / "src" / "allowed.py"
        target.write_text("line1\nline2\nline3\n", encoding="utf-8")
        patch, changed, issues = resolve_edits(
            self.workspace,
            [{"file": "src/allowed.py", "search": "line1\n", "replace": "line1_updated\n"}],
            allowed_files={"src/allowed.py"},
            max_files=2,
            max_patch_bytes=32000,
        )

        self.assertEqual(issues, [])
        self.assertEqual(changed, ("src/allowed.py",))
        self.assertIn("@@ -1,1 +1,1 @@", patch)
        self.assertIn("-line1", patch)
        self.assertIn("+line1_updated", patch)
        self.assertNotIn("line2", patch)


if __name__ == "__main__":
    unittest.main()


