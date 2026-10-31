"""Unit tests for the Deterministic Pinpointed Prescriptions Engine."""

import unittest

from local_coding_agent.prescriptions import (
    DiagnosticKind,
    json_syntax_prescription,
    prescribe_all,
    prescribe_issue,
    tool_policy_prescription,
)


class PrescriptionsEngineTests(unittest.TestCase):
    def test_schema_checks_type_prescription(self):
        prescription = prescribe_issue("each check must be an object")
        self.assertEqual(prescription.kind, DiagnosticKind.SCHEMA_CHECKS_TYPE_ERROR)
        self.assertIn("checks", prescription.instruction)
        self.assertIn("[]", prescription.instruction)

    def test_schema_risks_type_prescription(self):
        prescription = prescribe_issue("risks must be a list")
        self.assertEqual(prescription.kind, DiagnosticKind.SCHEMA_RISKS_TYPE_ERROR)
        self.assertIn("risks", prescription.instruction)

    def test_tool_dual_format_prescription(self):
        prescription = prescribe_issue("provide either patch or edits, not both")
        self.assertEqual(prescription.kind, DiagnosticKind.TOOL_DUAL_PATCH_AND_EDITS)
        self.assertIn("edits", prescription.instruction)
        self.assertIn("patch", prescription.instruction)

    def test_tool_missing_changes_prescription(self):
        prescription = prescribe_issue("patch or edits must be provided")
        self.assertEqual(prescription.kind, DiagnosticKind.TOOL_MISSING_PATCH_AND_EDITS)
        self.assertIn("edits", prescription.instruction)

    def test_forbidden_file_prescription(self):
        prescription = prescribe_issue("path is outside task allowlist: secret.py")
        self.assertEqual(prescription.kind, DiagnosticKind.TOOL_FORBIDDEN_FILE)
        self.assertIn("secret.py", prescription.instruction)
        self.assertIn("allowlist", prescription.instruction)

    def test_search_not_found_prescription(self):
        prescription = prescribe_issue("edit search block not found: src/calc.py")
        self.assertEqual(prescription.kind, DiagnosticKind.SEARCH_BLOCK_NOT_FOUND)
        self.assertIn("src/calc.py", prescription.instruction)
        self.assertIn("read_file", prescription.instruction)

    def test_search_not_line_aligned_prescription(self):
        prescription = prescribe_issue("edit search block is not line-aligned: src/calc.py")
        self.assertEqual(prescription.kind, DiagnosticKind.SEARCH_NOT_LINE_ALIGNED)
        self.assertIn("начала строки", prescription.instruction)

    def test_search_ambiguous_prescription(self):
        prescription = prescribe_issue("edit search block is ambiguous (3 matches): src/calc.py")
        self.assertEqual(prescription.kind, DiagnosticKind.SEARCH_AMBIGUOUS)
        self.assertIn("контекст", prescription.instruction)

    def test_duplicate_file_edit_prescription(self):
        prescription = prescribe_issue("duplicate edit file: src/calc.py")
        self.assertEqual(prescription.kind, DiagnosticKind.DUPLICATE_EDIT_FILE)
        self.assertIn("Объедини", prescription.instruction)

    def test_corrupt_hunk_diff_prescription(self):
        prescription = prescribe_issue("patch has invalid diff hunk header: @@ -1,99 +1,99 @@")
        self.assertEqual(prescription.kind, DiagnosticKind.DIFF_CORRUPT_HUNK)
        self.assertIn("edits", prescription.instruction)

    def test_diff_apply_failed_prescription(self):
        prescription = prescribe_issue("patch does not apply cleanly: error: patch failed")
        self.assertEqual(prescription.kind, DiagnosticKind.DIFF_APPLY_FAILED)
        self.assertIn("read_file", prescription.instruction)

    def test_prescribe_all_deduplicates_and_unifies(self):
        issues = [
            "each check must be an object",
            "each check must be an object",
            "edit search block not found: src/a.py",
        ]
        result = prescribe_all(issues)
        self.assertIn("checks", result)
        self.assertIn("src/a.py", result)
        # Verify deduplication
        self.assertEqual(result.count("Поле 'checks'"), 1)

    def test_json_syntax_prescription(self):
        text = json_syntax_prescription("Expecting property name enclosed in double quotes")
        self.assertIn("ОШИБКА JSON", text)
        self.assertIn("candidate", text)

    def test_tool_policy_prescription_payload(self):
        payload = tool_policy_prescription("propose_patch", "provide either patch or edits, not both")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error_code"], "ERR_DUAL_FORMAT")
        self.assertIn("edits", payload["hint"])

    def test_english_prescriptions(self):
        prescription = prescribe_issue("provide either patch or edits, not both", lang="en")
        self.assertEqual(prescription.kind, DiagnosticKind.TOOL_DUAL_PATCH_AND_EDITS)
        self.assertIn("Do not provide 'patch' and 'edits'", prescription.instruction)
        self.assertEqual(prescription.code, "ERR_DUAL_FORMAT")

        json_err = json_syntax_prescription("Expecting comma", lang="en")
        self.assertIn("JSON SYNTAX ERROR", json_err)

        all_err = prescribe_all(["each check must be an object"], lang="en")
        self.assertIn("The 'checks' field must not contain", all_err)


if __name__ == "__main__":
    unittest.main()

