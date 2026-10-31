"""Unit tests for AST-Guided Context Compactor (R17)."""

import pytest
from local_coding_agent.ast_compactor import skeletonize_python, skeletonize_file


PYTHON_SAMPLE = """import math
from typing import List

CONSTANT_VAL = 100

def helper_one(x: int) -> int:
    '''Helper docstring.'''
    a = x * 2
    b = a + CONSTANT_VAL
    return b

def target_func(items: List[int]) -> int:
    '''Target docstring.'''
    total = 0
    for item in items:
        total += helper_one(item)
    return total

class Calculator:
    '''Calculator class docstring.'''

    def __init__(self, base: int):
        self.base = base

    def compute(self, val: int) -> int:
        res = val * self.base
        return res
"""


def test_skeletonize_python_without_target_symbols_collapses_all_bodies():
    result = skeletonize_python(PYTHON_SAMPLE)
    assert "import math" in result
    assert "CONSTANT_VAL = 100" in result
    assert "def helper_one(x: int) -> int:" in result
    assert "Helper docstring." in result
    assert "a = x * 2" not in result
    assert "..." in result
    assert "def target_func(items: List[int]) -> int:" in result
    assert "total = 0" not in result
    assert "class Calculator:" in result
    assert "res = val * self.base" not in result


def test_skeletonize_python_with_target_symbol_expands_only_target():
    result = skeletonize_python(PYTHON_SAMPLE, target_symbols=["target_func"])
    # helper_one should be collapsed
    assert "def helper_one(x: int) -> int:" in result
    assert "a = x * 2" not in result
    # target_func should be fully preserved
    assert "def target_func(items: List[int]) -> int:" in result
    assert "total = 0" in result
    assert "total += helper_one(item)" in result
    assert "return total" in result
    # Calculator should be collapsed
    assert "class Calculator:" in result
    assert "res = val * self.base" not in result


def test_skeletonize_python_with_target_class_method():
    result = skeletonize_python(PYTHON_SAMPLE, target_symbols=["compute"])
    # helper_one and target_func are collapsed
    assert "a = x * 2" not in result
    assert "total = 0" not in result
    # Calculator.compute is expanded
    assert "class Calculator:" in result
    assert "def compute(self, val: int) -> int:" in result
    assert "res = val * self.base" in result


def test_skeletonize_python_syntax_error_graceful_fallback():
    broken_code = "def broken(x:\nreturn broken"
    result = skeletonize_python(broken_code)
    assert result == broken_code


def test_skeletonize_file_non_python(tmp_path):
    txt_file = tmp_path / "data.txt"
    txt_file.write_text("line1\nline2\nline3", encoding="utf-8")
    res = skeletonize_file(str(txt_file))
    assert res == "line1\nline2\nline3"
