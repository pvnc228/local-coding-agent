"""Semantic Linter & Fast Pre-Test Prescriptions Engine (R18).

Sub-50ms static analysis pre-gate catching syntax, indentation, and structural
errors before executing heavy unit test runners, translating diagnostics into
deterministic pinpointed prescriptions.
"""

from __future__ import annotations

import ast
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from .validators import apply_patch


@dataclass(frozen=True)
class LinterDiagnostic:
    file: str
    line: int | None
    message: str
    rule: str = "syntax"


@dataclass(frozen=True)
class LinterReport:
    valid: bool
    diagnostics: tuple[LinterDiagnostic, ...] = field(default_factory=tuple)
    prescriptions: tuple[str, ...] = field(default_factory=tuple)


def lint_source_code(filename: str, source_code: str) -> LinterReport:
    """Run fast in-memory static linting on a single file."""
    diagnostics: list[LinterDiagnostic] = []
    prescriptions: list[str] = []

    path = Path(filename)
    if path.suffix == ".py":
        try:
            ast.parse(source_code, filename=filename)
        except SyntaxError as exc:
            line_no = exc.lineno
            msg = exc.msg or "Syntax error"
            diag = LinterDiagnostic(file=filename, line=line_no, message=msg, rule="SyntaxError")
            diagnostics.append(diag)
            prescriptions.append(
                f"Синтаксическая ошибка в {filename}:{line_no}: {msg}. Проверьте двоеточия, скобки и отступы."
            )
        except Exception as exc:
            diag = LinterDiagnostic(file=filename, line=None, message=str(exc), rule="ParseError")
            diagnostics.append(diag)
            prescriptions.append(f"Ошибка синтаксического анализа в {filename}: {exc}")

    return LinterReport(
        valid=len(diagnostics) == 0,
        diagnostics=tuple(diagnostics),
        prescriptions=tuple(prescriptions),
    )


def lint_patch_in_memory(workspace_root: str, patch_content: str) -> LinterReport:
    """Simulate patch application and lint all modified files."""
    if not patch_content.strip():
        return LinterReport(valid=True)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_root = Path(tmp_dir)
        # Copy modified files from workspace_root to tmp_root
        ws_root = Path(workspace_root).resolve()

        # Parse target files from patch
        target_files: set[str] = set()
        for line in patch_content.splitlines():
            if line.startswith("--- a/") or line.startswith("+++ b/"):
                fname = line[6:].strip()
                if fname and fname != "/dev/null":
                    p = Path(fname)
                    if ".." in p.parts or p.is_absolute():
                        diag = LinterDiagnostic(file=fname, line=None, message="Path escapes workspace", rule="PathTraversal")
                        return LinterReport(
                            valid=False,
                            diagnostics=(diag,),
                            prescriptions=(f"Патч содержит недопустимый путь '{fname}', выходящий за пределы рабочей области",),
                        )
                    target_files.add(fname)

        for rel_file in target_files:
            src_file = ws_root / rel_file
            dst_file = tmp_root / rel_file
            if src_file.exists():
                dst_file.parent.mkdir(parents=True, exist_ok=True)
                dst_file.write_bytes(src_file.read_bytes())

        # Apply patch to tmp_root
        applied, detail = apply_patch(str(tmp_root), patch_content)
        if not applied:
            diag = LinterDiagnostic(file="patch", line=None, message=detail, rule="GitApplyFailed")
            return LinterReport(
                valid=False,
                diagnostics=(diag,),
                prescriptions=(f"Патч не может быть применён к рабочей области: {detail}",),
            )

        # Lint all target files
        all_diags: list[LinterDiagnostic] = []
        all_prescriptions: list[str] = []
        for rel_file in target_files:
            target_path = tmp_root / rel_file
            if target_path.exists() and target_path.is_file():
                content = target_path.read_text(encoding="utf-8", errors="replace")
                report = lint_source_code(rel_file, content)
                if not report.valid:
                    all_diags.extend(report.diagnostics)
                    all_prescriptions.extend(report.prescriptions)

        return LinterReport(
            valid=len(all_diags) == 0,
            diagnostics=tuple(all_diags),
            prescriptions=tuple(all_prescriptions),
        )
