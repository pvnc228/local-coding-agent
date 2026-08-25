from __future__ import annotations

from typing import Any, Protocol


SYSTEM_CONTRACT = """Ты локальный coding-subagent для одной атомарной задачи.
Работай только в пределах task envelope.
Не выдумывай отсутствующий контекст.
Не утверждай, что запускал тесты или менял файлы без результата инструмента.
Используй только предоставленные инструменты.
Для файлов используй только относительные пути из task allowlist; абсолютные пути и '..' запрещены.
Если данных не хватает, задай один точный вопрос.
Патч должен быть минимальным и затрагивать только разрешённые файлы.
Для propose_patch предпочтителен SEARCH/REPLACE (edits: file+search+replace, номера строк не нужны) либо полный unified diff с корректными hunk headers. Применимость проверяют validator и git. В search копируй старый код точно, включая ведущие пробелы каждой строки.
После завершения верни один JSON без markdown: {"status":"candidate","summary":"...","patch":"<diff>","checks":[],"risks":[]}. Вместо "patch" можно "edits":[{"file","search","replace"}]. Патч из propose_patch можно не дублировать."""


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List bounded files below a workspace-relative directory.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read one UTF-8 file from the task allowlist.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_text",
            "description": "Search text in bounded allowlisted files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "paths": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_patch",
            "description": (
                "Return a complete change proposal without writing files. "
                "Prefer SEARCH/REPLACE: a list of edits, each with file+search+replace "
                "(no line numbers needed). Copy search BYTE-FOR-BYTE from the file "
                "including every leading space/indent of each line. Example: "
                "{\"edits\":[{\"file\":\"src/a.py\",\"search\":\"def f(x):\\n    return x+1\","
                "\"replace\":\"def f(x):\\n    return x+2\"}]}. "
                "Alternatively provide one unified diff with diff --git, ---, +++ and "
                "valid hunk headers. Use real newlines and relative allowlisted paths. "
                "Applicability is checked by the controller-owned validator and git."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "patch": {"type": "string"},
                    "edits": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "file": {"type": "string"},
                                "search": {"type": "string"},
                                "replace": {"type": "string"},
                            },
                            "required": ["file", "search", "replace"],
                        },
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_tests",
            "description": "Run exactly one command from the task checks allowlist.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
]


class ModelClient(Protocol):
    def chat(self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]]) -> dict[str, Any]: ...
