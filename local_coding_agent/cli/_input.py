"""Task input loading helpers for the local-coding-agent CLI."""

from __future__ import annotations

import json
from pathlib import Path

from ..task import TaskEnvelope


def load_task_file(path: str | Path) -> TaskEnvelope:
    # utf-8-sig: PowerShell 5.1 `Set-Content -Encoding UTF8` writes a BOM.
    raw = Path(path).read_bytes().decode("utf-8-sig")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("task JSON must be an object")
    return TaskEnvelope.from_mapping(value)


def load_task_input(
    task_value: str | Path | None = None,
    task_file: str | Path | None = None,
) -> TaskEnvelope:
    if task_file is not None:
        return load_task_file(task_file)
    if task_value is not None:
        val_str = str(task_value).strip()
        if (val_str.startswith("'") and val_str.endswith("'")) or (val_str.startswith('"') and val_str.endswith('"')):
            stripped = val_str[1:-1].strip()
            if stripped.startswith("{") and stripped.endswith("}"):
                val_str = stripped
        if val_str.startswith("{") and val_str.endswith("}"):
            try:
                parsed = json.loads(val_str)
                if not isinstance(parsed, dict):
                    raise ValueError("task JSON must be an object")
                return TaskEnvelope.from_mapping(parsed)
            except json.JSONDecodeError:
                # Try replacing single quotes with double quotes if standard JSON failed
                try:
                    import ast
                    parsed_ast = ast.literal_eval(val_str)
                    if isinstance(parsed_ast, dict):
                        return TaskEnvelope.from_mapping(parsed_ast)
                except Exception:
                    pass
                raise ValueError(f"malformed inline task JSON: {val_str}")
        try:
            path = Path(task_value)
            if path.is_file():
                return load_task_file(path)
        except (OSError, ValueError):
            pass
        try:
            parsed = json.loads(val_str)
            if isinstance(parsed, dict):
                return TaskEnvelope.from_mapping(parsed)
        except Exception:
            pass
        return load_task_file(task_value)
    raise ValueError("--task or --task-file is required")
