import tempfile
import unittest
from pathlib import Path

from local_coding_agent.ripgrep import (
    RipgrepMatch,
    ripgrep_files,
    ripgrep_search,
)


class RipgrepSearchTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)

        (self.workspace / "src").mkdir()
        (self.workspace / "tests").mkdir()
        (self.workspace / "docs").mkdir()
        (self.workspace / ".git").mkdir()
        (self.workspace / "node_modules").mkdir()

        (self.workspace / "src" / "app.py").write_text(
            "def calculate_total(items):\n    # TODO: optimize loop\n    return sum(items)\n",
            encoding="utf-8",
        )
        (self.workspace / "src" / "helpers.py").write_text(
            "CONSTANT_VAL = 42\ndef format_output(val):\n    return f'Total: {val}'\n",
            encoding="utf-8",
        )
        (self.workspace / "tests" / "test_app.py").write_text(
            "from src.app import calculate_total\ndef test_calculate():\n    assert calculate_total([1, 2]) == 3\n",
            encoding="utf-8",
        )
        (self.workspace / "docs" / "guide.md").write_text(
            "# User Guide\nCalculate totals easily.\n",
            encoding="utf-8",
        )
        (self.workspace / ".git" / "HEAD").write_text(
            "ref: refs/heads/main\n",
            encoding="utf-8",
        )
        (self.workspace / "node_modules" / "pkg.json").write_text(
            '{"name": "ignore_me"}\n',
            encoding="utf-8",
        )
        # Binary file
        (self.workspace / "data.bin").write_bytes(b"\x00\x01\x02\x03BINARY_DATA\x00")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_search_literal_case_insensitive_both_modes(self):
        for force_fallback in (True, False):
            with self.subTest(force_fallback=force_fallback):
                matches = ripgrep_search(
                    query="calculate_total",
                    root=self.workspace,
                    case_sensitive=False,
                    _force_fallback=force_fallback,
                )

                self.assertTrue(len(matches) >= 2)
                files_found = {m.file.replace("\\", "/") for m in matches}
                self.assertIn("src/app.py", files_found)
                self.assertIn("tests/test_app.py", files_found)

                # Check RipgrepMatch properties
                app_match = next(m for m in matches if m.file.replace("\\", "/").endswith("src/app.py"))
                self.assertIsInstance(app_match, RipgrepMatch)
                self.assertEqual(app_match.line_number, 1)
                self.assertIn("def calculate_total(items):", app_match.line_content)

    def test_search_case_sensitive(self):
        for force_fallback in (True, False):
            with self.subTest(force_fallback=force_fallback):
                # Search lowercase
                matches_lower = ripgrep_search(
                    query="todo",
                    root=self.workspace,
                    case_sensitive=True,
                    _force_fallback=force_fallback,
                )
                self.assertEqual(len(matches_lower), 0)

                # Search uppercase
                matches_upper = ripgrep_search(
                    query="TODO",
                    root=self.workspace,
                    case_sensitive=True,
                    _force_fallback=force_fallback,
                )
                self.assertEqual(len(matches_upper), 1)
                self.assertEqual(matches_upper[0].line_number, 2)

    def test_search_with_regex(self):
        for force_fallback in (True, False):
            with self.subTest(force_fallback=force_fallback):
                matches = ripgrep_search(
                    query=r"def\s+\w+\(",
                    root=self.workspace,
                    is_regex=True,
                    _force_fallback=force_fallback,
                )
                self.assertTrue(len(matches) >= 3)
                contents = [m.line_content for m in matches]
                self.assertTrue(any("def calculate_total" in c for c in contents))
                self.assertTrue(any("def format_output" in c for c in contents))
                self.assertTrue(any("def test_calculate" in c for c in contents))

    def test_search_with_globs_inclusion_and_exclusion(self):
        for force_fallback in (True, False):
            with self.subTest(force_fallback=force_fallback):
                # Globs: only python files
                py_matches = ripgrep_search(
                    query="calculate",
                    root=self.workspace,
                    globs=["*.py"],
                    _force_fallback=force_fallback,
                )
                for m in py_matches:
                    self.assertTrue(m.file.endswith(".py"))

                # Globs: exclude tests
                no_test_matches = ripgrep_search(
                    query="calculate",
                    root=self.workspace,
                    globs=["*.py", "!tests/*"],
                    _force_fallback=force_fallback,
                )
                files_found = {m.file.replace("\\", "/") for m in no_test_matches}
                self.assertIn("src/app.py", files_found)
                self.assertNotIn("tests/test_app.py", files_found)

    def test_search_max_results(self):
        for force_fallback in (True, False):
            with self.subTest(force_fallback=force_fallback):
                matches = ripgrep_search(
                    query="def",
                    root=self.workspace,
                    max_results=2,
                    _force_fallback=force_fallback,
                )
                self.assertEqual(len(matches), 2)

    def test_search_skips_binary_and_ignored_directories(self):
        for force_fallback in (True, False):
            with self.subTest(force_fallback=force_fallback):
                # Should not match inside .git, node_modules, or binary file
                git_match = ripgrep_search(
                    query="refs/heads",
                    root=self.workspace,
                    _force_fallback=force_fallback,
                )
                self.assertEqual(len(git_match), 0)

                bin_match = ripgrep_search(
                    query="BINARY_DATA",
                    root=self.workspace,
                    _force_fallback=force_fallback,
                )
                self.assertEqual(len(bin_match), 0)

    def test_search_single_file_target(self):
        target_file = self.workspace / "src" / "app.py"
        matches = ripgrep_search(
            query="calculate_total",
            root=target_file,
            _force_fallback=True,
        )
        self.assertEqual(len(matches), 1)

    def test_ripgrep_files(self):
        for force_fallback in (True, False):
            with self.subTest(force_fallback=force_fallback):
                # All py files
                py_files = ripgrep_files(
                    pattern="*.py",
                    root=self.workspace,
                    _force_fallback=force_fallback,
                )
                normalized = [f.replace("\\", "/") for f in py_files]
                self.assertIn("src/app.py", normalized)
                self.assertIn("src/helpers.py", normalized)
                self.assertIn("tests/test_app.py", normalized)
                self.assertNotIn("docs/guide.md", normalized)

                # Max results
                limited_files = ripgrep_files(
                    pattern="*.py",
                    root=self.workspace,
                    max_results=1,
                    _force_fallback=force_fallback,
                )
                self.assertEqual(len(limited_files), 1)

    def test_search_nonexistent_root_returns_empty(self):
        non_existent = self.workspace / "ghost_folder"
        self.assertEqual(ripgrep_search("query", root=non_existent), [])
        self.assertEqual(ripgrep_files("*.py", root=non_existent), [])


if __name__ == "__main__":
    unittest.main()
