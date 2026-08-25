"""Small-model tool-call emitters -> controller tool_call dicts.

Small local models often cannot emit a proper OpenAI ``tool_calls`` array and
instead spell a tool call inside their text using one of several ad-hoc
formats (qwen ``<tool_call>`` JSON, qwen XML ``<function=...>``,
llama-server ``<|python_tag|>``, mistral ``[TOOL_CALLS]``). This module
extracts those spans and reshapes them into the SAME dict shape the
controller's ``_decode_tool_call`` consumes (``{"function": {"name",
"arguments"}}``), so the controller can promote them into real tool calls.

Standalone on purpose (no controller import) to avoid a circular import; the
dict shape is duplicated here.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCallExtraction:
    calls: list[dict[str, Any]] = field(default_factory=list)
    remaining_text: str = ""
    formats_detected: list[str] = field(default_factory=list)


_FORMATS: list[tuple[str, re.Pattern]] = [
    ("<tool_call>", re.compile(r"<tool_call>(.*?)</tool_call>", re.S)),
    ("llama_python_tag", re.compile(r"<\|python_tag\|>(.*?)(?=<|$)", re.S)),
    ("qwen_xml", re.compile(r"<function=([^>]+)>(.*?)</function>", re.S)),
    ("mistral_tool_calls", re.compile(r"\[TOOL_CALLS\]\s*(\[[^\]]*\])", re.S)),
]

# <parameter=K>V</parameter> or <parameter name="K">V</parameter>
_PARAMETER_RE = re.compile(
    r"<parameter\s*(?:name=\"([^\"]+)\"|=([^>]+))>(.*?)</parameter>", re.S
)


def _parse_argument(value: str) -> Any:
    """Parse a qwen-XML parameter value as JSON when possible, else raw string."""
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def _make_call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {"function": {"name": name, "arguments": arguments}}


def _matches_allowed(name: str, allowed_names: list[str] | None) -> bool:
    return not allowed_names or name in allowed_names


def _consume(text: str, spans: list[tuple[int, int]]) -> str:
    """Drop the given [start, end) spans from text, keeping everything else."""
    if not spans:
        return text
    spans = sorted(spans)
    parts: list[str] = []
    cursor = 0
    for start, end in spans:
        if start > cursor:
            parts.append(text[cursor:start])
        cursor = max(cursor, end)
    parts.append(text[cursor:])
    return "".join(parts)


def _extract_json_calls(
    text: str,
    body: str,
    start: int,
    end: int,
    allowed_names: list[str] | None,
    calls: list[dict[str, Any]],
    spans: list[tuple[int, int]],
    is_array: bool,
) -> None:
    """Parse a JSON (object or array) body into calls; consume the span on success."""
    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        return  # malformed -> span left in remaining_text, no call promoted
    items = payload if is_array and isinstance(payload, list) else [payload]
    if not items or not isinstance(items[0], dict):
        return
    promoted: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        arguments = item.get("arguments")
        if not isinstance(name, str) or not name or not isinstance(arguments, dict):
            continue  # invalid call: dropped, but span may still be consumed
        if not _matches_allowed(name, allowed_names):
            return  # not allowlisted: keep the whole span in remaining text
        promoted.append(_make_call(name, arguments))
    if promoted:
        calls.extend(promoted)
        spans.append((start, end))
    elif is_array and isinstance(payload, list) and payload:
        # array held only invalid items -> consume to avoid partial noise
        spans.append((start, end))


def extract_tool_calls(
    text: str, allowed_names: list[str] | None = None
) -> ToolCallExtraction:
    """Extract small-model tool calls from ``text``.

    Returns a :class:`ToolCallExtraction`. Consumed spans are removed from
    ``remaining_text``; a call whose name is not in ``allowed_names`` (when
    provided) is left untouched in the text. Malformed input never raises —
    worst case an empty ``calls`` list and the original text.
    """
    if not isinstance(text, str) or not text.strip():
        return ToolCallExtraction(remaining_text=text or "", formats_detected=[])

    calls: list[dict[str, Any]] = []
    spans: list[tuple[int, int]] = []
    formats_detected: list[str] = []

    for fmt, pattern in _FORMATS:
        matched = False
        for m in pattern.finditer(text):
            matched = True
            start, end = m.span()
            if fmt == "<tool_call>":
                _extract_json_calls(
                    text, m.group(1), start, end, allowed_names, calls, spans, False
                )
            elif fmt == "llama_python_tag":
                _extract_json_calls(
                    text, m.group(1), start, end, allowed_names, calls, spans, False
                )
            elif fmt == "mistral_tool_calls":
                _extract_json_calls(
                    text, m.group(1), start, end, allowed_names, calls, spans, True
                )
            elif fmt == "qwen_xml":
                name = m.group(1).strip()
                arguments: dict[str, Any] = {}
                valid = bool(name)
                for pm in _PARAMETER_RE.finditer(m.group(2)):
                    key = (pm.group(1) or pm.group(2) or "").strip()
                    if not key:
                        continue
                    arguments[key] = _parse_argument(pm.group(3))
                if not valid or not _matches_allowed(name, allowed_names):
                    continue  # missing name -> skipped; not allowed -> kept in text
                calls.append(_make_call(name, arguments))
                spans.append((start, end))
        if matched:
            formats_detected.append(fmt)

    return ToolCallExtraction(
        calls=calls,
        remaining_text=_consume(text, spans),
        formats_detected=formats_detected,
    )
