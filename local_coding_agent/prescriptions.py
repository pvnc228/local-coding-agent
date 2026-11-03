"""Deterministic Pinpointed Prescriptions Engine for Small Local Models.

Translates validation issues, JSON parse failures, tool policy violations,
SEARCH/REPLACE mismatches, and test failures into precise, actionable,
laser-focused instructions that small models (2B-4B) can follow deterministically.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Optional, Sequence


class DiagnosticKind(str, Enum):
    JSON_SYNTAX_ERROR = "JSON_SYNTAX_ERROR"
    SCHEMA_CHECKS_TYPE_ERROR = "SCHEMA_CHECKS_TYPE_ERROR"
    SCHEMA_RISKS_TYPE_ERROR = "SCHEMA_RISKS_TYPE_ERROR"
    SCHEMA_MISSING_REQUIRED = "SCHEMA_MISSING_REQUIRED"
    TOOL_DUAL_PATCH_AND_EDITS = "TOOL_DUAL_PATCH_AND_EDITS"
    TOOL_MISSING_PATCH_AND_EDITS = "TOOL_MISSING_PATCH_AND_EDITS"
    TOOL_FORBIDDEN_FILE = "TOOL_FORBIDDEN_FILE"
    SEARCH_BLOCK_NOT_FOUND = "SEARCH_BLOCK_NOT_FOUND"
    SEARCH_NOT_LINE_ALIGNED = "SEARCH_NOT_LINE_ALIGNED"
    SEARCH_AMBIGUOUS = "SEARCH_AMBIGUOUS"
    DUPLICATE_EDIT_FILE = "DUPLICATE_EDIT_FILE"
    DIFF_CORRUPT_HUNK = "DIFF_CORRUPT_HUNK"
    DIFF_APPLY_FAILED = "DIFF_APPLY_FAILED"
    TEST_CHECK_FAILED = "TEST_CHECK_FAILED"
    GENERIC_DIAGNOSTIC = "GENERIC_DIAGNOSTIC"


@dataclass(frozen=True)
class Prescription:
    kind: DiagnosticKind
    code: str
    summary: str
    instruction: str
    fix_example: Optional[str] = None
    lang: str = "ru"

    def format_message(self) -> str:
        if self.fix_example:
            example_label = "Example:" if self.lang == "en" else "Пример:"
            return f"{self.instruction} {example_label} {self.fix_example}"
        return self.instruction


def prescribe_issue(
    issue: str,
    *,
    lang: str = "ru",
    context: Optional[Mapping[str, Any]] = None,
) -> Prescription:
    """Analyze a single diagnostic issue string and return an actionable prescription."""
    raw = str(issue or "").strip()
    lower = raw.lower()
    is_en = lang == "en"

    # 1. Checks schema issues (e.g. checks must be a list of objects)
    if "check" in lower and ("must be an object" in lower or "must be a list" in lower or "string" in lower or "type" in lower):
        return Prescription(
            kind=DiagnosticKind.SCHEMA_CHECKS_TYPE_ERROR,
            code="ERR_CHECKS_TYPE",
            summary="Field 'checks' has invalid type" if is_en else "Поле 'checks' заполнено неверным типом",
            instruction=(
                "The 'checks' field must not contain strings or arbitrary text. Provide an empty array 'checks': [] (or test execution objects)."
                if is_en
                else "Поле 'checks' не должно содержать строки или произвольный текст. Укажи пустой массив 'checks': [] (или объекты результатов тестов)."
            ),
            fix_example='"checks": []',
            lang=lang,
        )

    # 2. Risks schema issues
    if "risk" in lower and ("must be a list" in lower or "not-a-list" in lower or "type" in lower):
        return Prescription(
            kind=DiagnosticKind.SCHEMA_RISKS_TYPE_ERROR,
            code="ERR_RISKS_TYPE",
            summary="Field 'risks' must be a list" if is_en else "Поле 'risks' должно быть списком",
            instruction=(
                "The 'risks' field must be an array of strings or an empty array 'risks': []."
                if is_en
                else "Поле 'risks' должно быть массивом строк или пустым массивом 'risks': []."
            ),
            fix_example='"risks": []',
            lang=lang,
        )

    # 3. Tool Policy: Dual format (both patch and edits)
    if "not both" in lower or ("patch" in lower and "edits" in lower and "both" in lower):
        return Prescription(
            kind=DiagnosticKind.TOOL_DUAL_PATCH_AND_EDITS,
            code="ERR_DUAL_FORMAT",
            summary="Both patch and edits provided" if is_en else "Переданы одновременно и patch, и edits",
            instruction=(
                "Do not provide 'patch' and 'edits' simultaneously. Provide ONLY the 'edits' list with structure [{\"file\": \"...\", \"search\": \"...\", \"replace\": \"...\"}], omitting the 'patch' field."
                if is_en
                else "Не передавай 'patch' и 'edits' одновременно. Передай ТОЛЬКО список 'edits' со структурой [{\"file\": \"...\", \"search\": \"...\", \"replace\": \"...\"}], удалив поле 'patch'."
            ),
            fix_example='{"edits": [{"file": "src/a.py", "search": "old_code", "replace": "new_code"}]}',
            lang=lang,
        )

    # 4. Tool Policy: Missing patch/edits
    if "must be provided" in lower or "no patch nor edits" in lower or "no resolved patch" in lower:
        return Prescription(
            kind=DiagnosticKind.TOOL_MISSING_PATCH_AND_EDITS,
            code="ERR_MISSING_CHANGES",
            summary="No changes provided" if is_en else "Не указаны изменения",
            instruction=(
                "You must provide the list of changes in 'edits': [{\"file\": \"...\", \"search\": \"...\", \"replace\": \"...\"}]."
                if is_en
                else "Необходимо передать список изменений 'edits': [{\"file\": \"...\", \"search\": \"...\", \"replace\": \"...\"}]."
            ),
            fix_example='{"edits": [{"file": "src/a.py", "search": "old_code", "replace": "new_code"}]}',
            lang=lang,
        )

    # 5. File Allowlist violations
    if "allowlist" in lower or "outside" in lower or "forbidden" in lower:
        file_match = re.search(r"([a-zA-Z0-9_\-\./\\]+\.[a-zA-Z0-9]+)", raw)
        filename = file_match.group(1) if file_match else ("the specified file" if is_en else "указанный файл")
        return Prescription(
            kind=DiagnosticKind.TOOL_FORBIDDEN_FILE,
            code="ERR_FILE_FORBIDDEN",
            summary=f"File {filename} is outside task allowlist" if is_en else f"Файл {filename} вне task allowlist",
            instruction=(
                f"File '{filename}' is not in the task allowlist. Work only on allowlisted files."
                if is_en
                else f"Файл '{filename}' не входит в разрешенный список задачи. Работай только с файлами из task allowlist."
            ),
            lang=lang,
        )

    # 6. Search block not found
    if "search block not found" in lower or ("search" in lower and "not found" in lower):
        file_match = re.search(r":\s*([a-zA-Z0-9_\-\./\\]+)", raw)
        filename = file_match.group(1) if file_match else ("file" if is_en else "файле")
        return Prescription(
            kind=DiagnosticKind.SEARCH_BLOCK_NOT_FOUND,
            code="ERR_SEARCH_NOT_FOUND",
            summary=f"Search block not found in {filename}" if is_en else f"Блок search не найден в {filename}",
            instruction=(
                f"The 'search' block does not match the content of '{filename}'. Call the read_file tool and copy exact lines character-for-character, including leading whitespace and indentation."
                if is_en
                else f"Блок 'search' не совпадает с содержимым файла '{filename}'. Вызови инструмент read_file и скопируй точные строки символ-в-символ, включая пробелы и отступы в начале каждой строки."
            ),
            lang=lang,
        )

    # 7. Search not line-aligned
    if "line-aligned" in lower or "line aligned" in lower:
        return Prescription(
            kind=DiagnosticKind.SEARCH_NOT_LINE_ALIGNED,
            code="ERR_SEARCH_ALIGNMENT",
            summary="Search block is not line-aligned" if is_en else "Блок search не выровнен по границам строк",
            instruction=(
                "The 'search' block must begin at the very start of a line and end at a line boundary (do not slice fragments from the middle of a line)."
                if is_en
                else "Блок 'search' должен начинаться с самого начала строки файла и заканчиваться концом строки (не вырезай фрагменты из середины строки)."
            ),
            lang=lang,
        )

    # 8. Search ambiguous
    if "ambiguous" in lower:
        return Prescription(
            kind=DiagnosticKind.SEARCH_AMBIGUOUS,
            code="ERR_SEARCH_AMBIGUOUS",
            summary="Search block matches multiple locations" if is_en else "Блок search встречается в файле несколько раз",
            instruction=(
                "The 'search' block is ambiguous (multiple matches found). Include 2-3 unique lines of surrounding context above and below the replacement."
                if is_en
                else "Блок 'search' неоднозначен (найдено несколько совпадений). Добавь 2-3 уникальные строки контекста выше и ниже заменяемого кода."
            ),
            lang=lang,
        )

    # 9. Duplicate edit file
    if "duplicate edit file" in lower:
        return Prescription(
            kind=DiagnosticKind.DUPLICATE_EDIT_FILE,
            code="ERR_DUPLICATE_FILE_EDIT",
            summary="Multiple edit blocks for the same file" if is_en else "Несколько блоков правок для одного файла",
            instruction=(
                "Consolidate all edits for this file into a single 'search'/'replace' block."
                if is_en
                else "Объедини все изменения в одном файле в единый блок 'search'/'replace'."
            ),
            lang=lang,
        )

    # 10. Corrupt hunk / diff syntax issues
    if "hunk" in lower or "corrupt patch" in lower or "invalid hunk" in lower or "diff header" in lower:
        return Prescription(
            kind=DiagnosticKind.DIFF_CORRUPT_HUNK,
            code="ERR_DIFF_SYNTAX",
            summary="Invalid diff / hunk header syntax" if is_en else "Неверный синтаксис diff / hunk header",
            instruction=(
                "Syntax error in diff hunk header (line number mismatch). It is recommended to use the 'edits' format with [{file, search, replace}], where line numbers are not required."
                if is_en
                else "Ошибка синтаксиса в diff hunk header (не совпали номера строк). Рекомендуется использовать формат 'edits' со списком {file, search, replace}, где номера строк не требуются."
            ),
            fix_example='"edits": [{"file": "src/a.py", "search": "...", "replace": "..."}]',
            lang=lang,
        )

    # 11. Patch does not apply cleanly
    if "cleanly" in lower or "apply" in lower:
        return Prescription(
            kind=DiagnosticKind.DIFF_APPLY_FAILED,
            code="ERR_PATCH_APPLY",
            summary="Patch does not apply cleanly to current files" if is_en else "Патч не накладывается на текущее состояние файлов",
            instruction=(
                "Patch does not apply to the current file state. Call read_file to inspect fresh content and formulate SEARCH/REPLACE in 'edits'."
                if is_en
                else "Патч не накладывается на актуальную версию файла. Вызови read_file, чтобы получить свежий код, и сформируй SEARCH/REPLACE блок в поле 'edits'."
            ),
            lang=lang,
        )

    # Fallback generic prescription
    return Prescription(
        kind=DiagnosticKind.GENERIC_DIAGNOSTIC,
        code="ERR_GENERIC",
        summary=raw,
        instruction=raw,
        lang=lang,
    )


def prescribe_all(
    issues: Sequence[str],
    *,
    lang: str = "ru",
    context: Optional[Mapping[str, Any]] = None,
) -> str:
    """Build a unified, de-duplicated prescription string from multiple issues."""
    if not issues:
        return "Please fix response structure and return valid JSON." if lang == "en" else "Исправь структуру ответа и верни корректный JSON."
    prescriptions = [prescribe_issue(issue, lang=lang, context=context) for issue in issues]
    formatted = [p.format_message() for p in prescriptions]
    # Deduplicate preserving order
    unique_messages = list(dict.fromkeys(formatted))
    return " ".join(unique_messages)


def json_syntax_prescription(error: Optional[str] = None, *, lang: str = "ru") -> str:
    """Return an exact prescriptive prompt when a model outputs malformed non-JSON."""
    err_detail = f" ({error})" if error else ""
    if lang == "en":
        return (
            f"JSON SYNTAX ERROR: Your response could not be parsed as valid JSON{err_detail}. "
            "Return STRICTLY one JSON object without markdown fences (no ```json), without leading or trailing prose: "
            '{"status": "candidate", "summary": "brief summary", "patch": "", "checks": [], "risks": []} '
            'or with "edits": [{"file": "...", "search": "...", "replace": "..."}].'
        )
    return (
        f"ОШИБКА JSON: Твой ответ не удалось разобрать как валидный JSON{err_detail}. "
        "Верни СТРОГО один JSON-объект без markdown-разметки (без ```json), без текста до и после: "
        '{"status": "candidate", "summary": "краткое описание", "patch": "", "checks": [], "risks": []} '
        'или вместо "patch" передай "edits": [{"file": "...", "search": "...", "replace": "..."}].'
    )


def redact_check_output(text: str) -> str:
    """Bounded diagnostics, including credentials printed by a check."""
    import os

    text = str(text)[:128_000]
    for name, value in os.environ.items():
        if re.search(r'secret|token|password|credential|api.?key', name, re.I) and len(value) >= 6:
            text = text.replace(value, '[REDACTED]')
    text = re.sub(r'(?i)(bearer\s+)\S+', r'\1[REDACTED]', text)
    text = re.sub(r'(?i)((?:[\w.-]*(?:secret|token|password|api[_-]?key)[\w.-]*)\s*[=:]\s*)[^\s,;]+',
                  r'\1[REDACTED]', text)
    return text


def test_failure_prescription(checks: Sequence[Mapping[str, Any]], files: Sequence[str],
                              *, max_bytes: int = 1500) -> dict[str, Any]:
    """Last failure only, with deterministic pytest/unittest/generic excerpts."""
    import json

    lines = []
    for check in checks:
        if check.get('passed') is True:
            continue
        text = redact_check_output(str(check.get('stdout', '')) + '\n' + str(check.get('stderr', '')))
        raw_lines = text.splitlines()
        identities = [line[:350] for line in raw_lines if line.startswith(('FAILED ', 'FAIL:', 'ERROR:'))]
        selected = identities + [line[:350] for line in raw_lines if (
            line.startswith(('E ', 'E\t')) or re.match(r'^\w*(?:Error|Exception):', line.strip()) or
            'assert ' in line or any(f in line and (': ' in line or 'line ' in line) for f in files))]
        lines.append(f"exit_code={check.get('exit_code')}; timeout={bool(check.get('timeout'))}")
        lines.extend(selected[:12] if selected else [line[:350] for line in raw_lines[-8:]])
    payload = {'error': 'TEST_CHECK_FAILED', 'issues': lines,
               'instruction': 'Fix the code using this observed failure. Return a complete replacement patch against the ORIGINAL files. Do not change tests.'}
    while len(json.dumps(payload, ensure_ascii=False).encode('utf-8')) > max_bytes and lines:
        if len(lines[-1]) > 80:
            lines[-1] = lines[-1][:-80]
        else:
            lines.pop()
    return payload


# This public prescription helper is not a pytest test when imported into tests.
test_failure_prescription.__test__ = False


def tool_policy_prescription(tool_name: str, error: str, *, lang: str = "ru") -> dict[str, Any]:
    """Return a structured error payload with code, summary, and actionable hint for tool policy errors."""
    prescription = prescribe_issue(error, lang=lang)
    return {
        "ok": False,
        "status": "error",
        "error_code": prescription.code,
        "error": error,
        "hint": prescription.instruction,
        "diagnostic_kind": prescription.kind.value,
    }
