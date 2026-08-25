"""Fallback AST & Regex Code Intelligence Engine."""

from __future__ import annotations

import ast
from pathlib import Path
import re
from typing import Any

from ._types import LspHoverResult, LspLocation, LspPosition, LspRange, LspSymbol, path_to_uri


class FallbackLspEngine:
    """Built-in code intelligence engine for offline environments & test runs."""

    @staticmethod
    def _get_word_at_pos(content: str, line: int, character: int) -> tuple[str, LspRange] | None:
        lines = content.splitlines()
        if line < 0 or line >= len(lines):
            return None
        line_text = lines[line]
        if character < 0 or character > len(line_text):
            return None

        # Find word boundaries
        start = character
        while start > 0 and (line_text[start - 1].isalnum() or line_text[start - 1] == "_"):
            start -= 1
        end = character
        while end < len(line_text) and (line_text[end].isalnum() or line_text[end] == "_"):
            end += 1

        if start >= end:
            return None
        word = line_text[start:end]
        word_range = LspRange(LspPosition(line, start), LspPosition(line, end))
        return word, word_range

    def document_symbols(self, file_path: str | Path) -> list[LspSymbol]:
        """Extract symbol outline using AST (for Python) or regex (for others)."""
        path = Path(file_path).resolve()
        if not path.is_file():
            return []
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []

        uri = path_to_uri(path)
        if path.suffix.lower() in {".py", ".pyi"}:
            return self._document_symbols_python(content, uri, str(path))
        return self._document_symbols_regex(content, uri, str(path))

    def _document_symbols_python(self, content: str, uri: str, file_path: str) -> list[LspSymbol]:
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return self._document_symbols_regex(content, uri, file_path)

        symbols: list[LspSymbol] = []

        def visit_node(node: ast.AST, parent_kind: int | None = None) -> None:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = 6 if parent_kind == 5 else 12
                kind_name = "Method" if kind == 6 else "Function"
                start_line = max(0, getattr(node, "lineno", 1) - 1)
                start_char = getattr(node, "col_offset", 0)
                end_line = max(start_line, getattr(node, "end_lineno", start_line + 1) - 1)
                end_char = getattr(node, "end_col_offset", start_char + len(node.name))
                loc = LspLocation(
                    uri=uri,
                    range=LspRange(LspPosition(start_line, start_char), LspPosition(end_line, end_char)),
                    file_path=file_path,
                )
                symbols.append(LspSymbol(name=node.name, kind=kind, kind_name=kind_name, location=loc))
                for child in node.body:
                    visit_node(child, parent_kind=kind)
            elif isinstance(node, ast.ClassDef):
                kind = 5
                kind_name = "Class"
                start_line = max(0, getattr(node, "lineno", 1) - 1)
                start_char = getattr(node, "col_offset", 0)
                end_line = max(start_line, getattr(node, "end_lineno", start_line + 1) - 1)
                end_char = getattr(node, "end_col_offset", start_char + len(node.name))
                loc = LspLocation(
                    uri=uri,
                    range=LspRange(LspPosition(start_line, start_char), LspPosition(end_line, end_char)),
                    file_path=file_path,
                )
                symbols.append(LspSymbol(name=node.name, kind=kind, kind_name=kind_name, location=loc))
                for child in node.body:
                    visit_node(child, parent_kind=kind)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        name = target.id
                        is_const = name.isupper()
                        kind = 14 if is_const else 13
                        kind_name = "Constant" if is_const else "Variable"
                        start_line = max(0, getattr(target, "lineno", 1) - 1)
                        start_char = getattr(target, "col_offset", 0)
                        end_char = start_char + len(name)
                        loc = LspLocation(
                            uri=uri,
                            range=LspRange(LspPosition(start_line, start_char), LspPosition(start_line, end_char)),
                            file_path=file_path,
                        )
                        symbols.append(LspSymbol(name=name, kind=kind, kind_name=kind_name, location=loc))

        for item in tree.body:
            visit_node(item)
        return symbols

    def _document_symbols_regex(self, content: str, uri: str, file_path: str) -> list[LspSymbol]:
        symbols: list[LspSymbol] = []
        patterns = [
            (re.compile(r"^\s*(?:async\s+)?def\s+([a-zA-Z_][a-zA-Z0-9_]*)"), 12, "Function"),
            (re.compile(r"^\s*class\s+([a-zA-Z_][a-zA-Z0-9_]*)"), 5, "Class"),
            (re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([a-zA-Z_][a-zA-Z0-9_]*)"), 12, "Function"),
            (re.compile(r"^\s*(?:export\s+)?(?:class|interface|type)\s+([a-zA-Z_][a-zA-Z0-9_]*)"), 5, "Class"),
            (re.compile(r"^\s*(?:pub\s+)?fn\s+([a-zA-Z_][a-zA-Z0-9_]*)"), 12, "Function"),
            (re.compile(r"^\s*(?:pub\s+)?(?:struct|enum|trait)\s+([a-zA-Z_][a-zA-Z0-9_]*)"), 23, "Struct"),
            (re.compile(r"^\s*func\s+(?:\([^)]+\)\s+)?([a-zA-Z_][a-zA-Z0-9_]*)"), 12, "Function"),
            (re.compile(r"^\s*type\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+struct"), 23, "Struct"),
        ]
        for line_num, line_text in enumerate(content.splitlines()):
            for pat, kind, kind_name in patterns:
                m = pat.match(line_text)
                if m:
                    name = m.group(1)
                    start_char = line_text.find(name)
                    end_char = start_char + len(name)
                    loc = LspLocation(
                        uri=uri,
                        range=LspRange(LspPosition(line_num, start_char), LspPosition(line_num, end_char)),
                        file_path=file_path,
                    )
                    symbols.append(LspSymbol(name=name, kind=kind, kind_name=kind_name, location=loc))
                    break
        return symbols

    def go_to_definition(
        self,
        file_path: str | Path,
        line: int,
        character: int,
        workspace_root: str | Path | None = None,
    ) -> list[LspLocation]:
        """Navigate to symbol definition in file or workspace."""
        path = Path(file_path).resolve()
        if not path.is_file():
            return []
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []

        word_info = self._get_word_at_pos(content, line, character)
        if not word_info:
            return []
        word, _ = word_info

        # 1. Search in current file first
        current_symbols = self.document_symbols(path)
        for sym in current_symbols:
            if sym.name == word:
                return [sym.location]

        # 2. Search in workspace files
        root = Path(workspace_root).resolve() if workspace_root else path.parent
        locations: list[LspLocation] = []
        for file in root.rglob(f"*{path.suffix}"):
            if file == path or not file.is_file():
                continue
            if any(part.startswith(".") or part in {"node_modules", "__pycache__", "venv", ".venv"} for part in file.parts):
                continue
            symbols = self.document_symbols(file)
            for sym in symbols:
                if sym.name == word:
                    locations.append(sym.location)
                    if len(locations) >= 5:
                        return locations
        return locations

    def find_references(
        self,
        file_path: str | Path,
        line: int,
        character: int,
        workspace_root: str | Path | None = None,
        include_declaration: bool = True,
    ) -> list[LspLocation]:
        """Find occurrences and references of the symbol across workspace."""
        path = Path(file_path).resolve()
        if not path.is_file():
            return []
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []

        word_info = self._get_word_at_pos(content, line, character)
        if not word_info:
            return []
        word, _ = word_info

        root = Path(workspace_root).resolve() if workspace_root else path.parent
        target_files = [path]
        for f in root.rglob(f"*{path.suffix}"):
            if f != path and f.is_file() and not any(part.startswith(".") or part in {"node_modules", "__pycache__", "venv", ".venv"} for part in f.parts):
                target_files.append(f)

        references: list[LspLocation] = []
        word_regex = re.compile(rf"\b{re.escape(word)}\b")

        for f in target_files:
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            file_uri = path_to_uri(f)
            for line_idx, line_text in enumerate(text.splitlines()):
                for match in word_regex.finditer(line_text):
                    col_start = match.start()
                    col_end = match.end()
                    loc = LspLocation(
                        uri=file_uri,
                        range=LspRange(LspPosition(line_idx, col_start), LspPosition(line_idx, col_end)),
                        file_path=str(f),
                    )
                    references.append(loc)
                    if len(references) >= 100:
                        return references
        return references

    def hover(
        self,
        file_path: str | Path,
        line: int,
        character: int,
        workspace_root: str | Path | None = None,
    ) -> LspHoverResult | None:
        """Extract signature or docstring preview for the symbol under cursor."""
        path = Path(file_path).resolve()
        if not path.is_file():
            return None
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

        word_info = self._get_word_at_pos(content, line, character)
        if not word_info:
            return None
        word, word_range = word_info

        # Check Python AST
        if path.suffix.lower() in {".py", ".pyi"}:
            try:
                tree = ast.parse(content)
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == word:
                        args = [a.arg for a in node.args.args]
                        sig = f"def {node.name}({', '.join(args)})"
                        doc = ast.get_docstring(node) or ""
                        body = f"```python\n{sig}\n```"
                        if doc:
                            body += f"\n\n{doc}"
                        return LspHoverResult(contents=body, range=word_range)
                    elif isinstance(node, ast.ClassDef) and node.name == word:
                        bases = [getattr(b, "id", "object") for b in node.bases if hasattr(b, "id")]
                        sig = f"class {node.name}({', '.join(bases)})" if bases else f"class {node.name}"
                        doc = ast.get_docstring(node) or ""
                        body = f"```python\n{sig}\n```"
                        if doc:
                            body += f"\n\n{doc}"
                        return LspHoverResult(contents=body, range=word_range)
            except SyntaxError:
                pass

        # Regex fallback hover
        lines = content.splitlines()
        for idx, line_text in enumerate(lines):
            if re.search(rf"\b(?:def|class|function|fn|func|struct)\s+{re.escape(word)}\b", line_text):
                doc_lines = []
                for prev_idx in range(idx - 1, max(-1, idx - 6), -1):
                    prev = lines[prev_idx].strip()
                    if prev.startswith(("#", "//", "/*", "*")):
                        doc_lines.insert(0, prev)
                    else:
                        break
                body = f"```\n{line_text.strip()}\n```"
                if doc_lines:
                    body += f"\n\n" + "\n".join(doc_lines)
                return LspHoverResult(contents=body, range=word_range)

        return LspHoverResult(contents=f"Symbol: `{word}`", range=word_range)
