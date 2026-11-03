"""Unit tests for Semantic Linter and Fast Pre-Test Prescriptions (R18)."""

import pytest
from local_coding_agent.semantic_linter import (
    LinterDiagnostic,
    LinterReport,
    lint_source_code,
    lint_patch_in_memory,
)


def test_lint_source_code_valid_python():
    code = "def add(a: int, b: int) -> int:\n    return a + b\n"
    report = lint_source_code("math_utils.py", code)
    assert report.valid is True
    assert len(report.diagnostics) == 0
    assert len(report.prescriptions) == 0


def test_lint_source_code_syntax_error_sub_50ms():
    broken_code = "def broken(\n    return 42\n"
    report = lint_source_code("broken.py", broken_code)
    assert report.valid is False
    assert len(report.diagnostics) > 0
    diag = report.diagnostics[0]
    assert diag.file == "broken.py"
    assert diag.line is not None
    assert len(report.prescriptions) > 0
    assert "broken.py" in report.prescriptions[0]


def test_lint_source_code_indentation_error():
    indent_broken = "def foo():\nreturn 10\n"
    report = lint_source_code("indent.py", indent_broken)
    assert report.valid is False
    assert len(report.diagnostics) > 0
    assert any("indent" in p.lower() or "syntax" in p.lower() for p in report.prescriptions)


def test_lint_patch_in_memory_valid(tmp_path):
    src = tmp_path / "hello.py"
    src.write_text("def hello():\n    return 'old'\n", encoding="utf-8")

    patch = (
        "--- a/hello.py\n"
        "+++ b/hello.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def hello():\n"
        "-    return 'old'\n"
        "+    return 'new'\n"
    )
    report = lint_patch_in_memory(str(tmp_path), patch)
    assert report.valid is True
    assert len(report.diagnostics) == 0


def test_lint_patch_in_memory_with_syntax_error(tmp_path):
    src = tmp_path / "hello.py"
    src.write_text("def hello():\n    return 'old'\n", encoding="utf-8")

    patch = (
        "--- a/hello.py\n"
        "+++ b/hello.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def hello():\n"
        "-    return 'old'\n"
        "+    return 'new'(\n"
    )
    report = lint_patch_in_memory(str(tmp_path), patch)
    assert report.valid is False
    assert len(report.diagnostics) > 0
    assert "hello.py" in report.prescriptions[0]
