"""Benchmark execution: run one model against a disposable fixture and judge it."""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter_ns
from typing import Any, Mapping, Protocol, Sequence

from ..controller import Controller

from ._cases import BenchmarkCase, default_cases
from ._judge import _judge_patch


class ChatModel(Protocol):
    def chat(self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]]) -> dict[str, Any]: ...


class InstrumentedModel:
    """Collect metrics returned by Ollama without changing the model response."""

    def __init__(self, model: ChatModel) -> None:
        self.model = model
        self.model_calls = 0
        self.total_duration_ns = 0
        self.load_duration_ns = 0
        self.prompt_tokens = 0
        self.prompt_eval_duration_ns = 0
        self.eval_count = 0
        self.eval_duration_ns = 0
        self.proposed_patches: list[str] = []
        self.proposed_edits: list[list[dict[str, Any]]] = []

    def chat(self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]]) -> dict[str, Any]:
        response = self.model.chat(messages, tools=tools)
        self.model_calls += 1
        self._record_proposed_patches(response)
        self.total_duration_ns += _metric_int(response, "total_duration")
        self.load_duration_ns += _metric_int(response, "load_duration")
        self.prompt_tokens += _metric_int(response, "prompt_eval_count")
        self.prompt_eval_duration_ns += _metric_int(response, "prompt_eval_duration")
        self.eval_count += _metric_int(response, "eval_count")
        self.eval_duration_ns += _metric_int(response, "eval_duration")
        return response

    def _record_proposed_patches(self, response: Mapping[str, Any]) -> None:
        message = response.get("message")
        if not isinstance(message, Mapping):
            return
        calls = message.get("tool_calls") or []
        if not calls:
            compatible_call = _decode_content_tool_call(message.get("content"))
            if compatible_call is not None:
                calls = [compatible_call]
        for call in calls:
            if not isinstance(call, Mapping):
                continue
            function = call.get("function")
            if not isinstance(function, Mapping) or function.get("name") != "propose_patch":
                continue
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    continue
            if isinstance(arguments, Mapping):
                if isinstance(arguments.get("patch"), str):
                    self.proposed_patches.append(arguments["patch"])
                if isinstance(arguments.get("edits"), list):
                    self.proposed_edits.append(arguments["edits"])


@dataclass(frozen=True)
class BenchmarkCaseResult:
    case_id: str
    status: str
    correct: bool
    loop_reliable: bool
    validation_valid: bool
    patch_applied: bool
    patch_source: str
    patch_error: str
    wall_time_ms: float
    model_calls: int
    tool_calls: int
    total_duration_ns: int
    load_duration_ns: int
    prompt_tokens: int
    prompt_eval_duration_ns: int
    eval_count: int
    eval_duration_ns: int
    result: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        prompt_eval_tps = (
            round(self.prompt_tokens * 1e9 / self.prompt_eval_duration_ns, 2)
            if self.prompt_eval_duration_ns > 0
            else 0.0
        )
        eval_tps = (
            round(self.eval_count * 1e9 / self.eval_duration_ns, 2)
            if self.eval_duration_ns > 0
            else 0.0
        )
        return {
            "case_id": self.case_id,
            "status": self.status,
            "correct": self.correct,
            "loop_reliable": self.loop_reliable,
            "validation_valid": self.validation_valid,
            "patch_applied": self.patch_applied,
            "patch_source": self.patch_source,
            "patch_error": self.patch_error,
            "wall_time_ms": self.wall_time_ms,
            "model_calls": self.model_calls,
            "tool_calls": self.tool_calls,
            "total_duration_ns": self.total_duration_ns,
            "load_duration_ns": self.load_duration_ns,
            "prompt_tokens": self.prompt_tokens,
            "prompt_eval_duration_ns": self.prompt_eval_duration_ns,
            "prompt_eval_tps": prompt_eval_tps,
            "eval_count": self.eval_count,
            "eval_duration_ns": self.eval_duration_ns,
            "eval_tps": eval_tps,
            "result": dict(self.result),
        }


def _decode_content_tool_call(content: Any) -> dict[str, Any] | None:
    if not isinstance(content, str) or not content.strip():
        return None
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, Mapping):
        return None
    name = payload.get("name")
    arguments = payload.get("arguments")
    if not isinstance(name, str) or not name or not isinstance(arguments, (Mapping, str)):
        return None
    return {"function": {"name": name, "arguments": arguments}}


def run_case(
    model: ChatModel,
    case: BenchmarkCase,
    *,
    max_turns: int = 4,
) -> BenchmarkCaseResult:
    """Run one model against a disposable fixture and judge its proposal externally."""

    instrumented = model if isinstance(model, InstrumentedModel) else InstrumentedModel(model)
    with tempfile.TemporaryDirectory(prefix="local-benchmark-") as temp_dir:
        workspace = Path(temp_dir)
        for raw_path, content in case.fixture.items():
            target = workspace / raw_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="")

        started = perf_counter_ns()
        result = Controller(instrumented, workspace, max_turns=max_turns).run(case.task)
        wall_time_ms = (perf_counter_ns() - started) / 1_000_000
        fallback_patch = instrumented.proposed_patches[-1] if instrumented.proposed_patches else ""
        fallback_edits = instrumented.proposed_edits[-1] if instrumented.proposed_edits else None
        patch_applied, correct, patch_error, patch_source = _judge_patch(
            result,
            case,
            workspace,
            fallback_patch=fallback_patch,
            fallback_edits=fallback_edits,
        )
        tool_calls = sum(
            1
            for event in result.get("audit", [])
            if isinstance(event, Mapping) and event.get("event") == "tool_call"
        )
        validation = result.get("validation")
        validation_valid = isinstance(validation, Mapping) and validation.get("valid") is True
        if patch_source == "tool_proposal":
            validation_valid = _validate_patch_for_case(fallback_patch, case).valid
        status = result.get("status") if isinstance(result.get("status"), str) else "failed"
        loop_reliable = status == "accepted" and not _has_loop_error(result)
        return BenchmarkCaseResult(
            case_id=case.id,
            status=status,
            correct=correct,
            loop_reliable=loop_reliable,
            validation_valid=validation_valid,
            patch_applied=patch_applied,
            patch_source=patch_source,
            patch_error=patch_error,
            wall_time_ms=wall_time_ms,
            model_calls=instrumented.model_calls,
            tool_calls=tool_calls,
            total_duration_ns=instrumented.total_duration_ns,
            load_duration_ns=instrumented.load_duration_ns,
            prompt_tokens=instrumented.prompt_tokens,
            prompt_eval_duration_ns=instrumented.prompt_eval_duration_ns,
            eval_count=instrumented.eval_count,
            eval_duration_ns=instrumented.eval_duration_ns,
            result=result,
        )


def run_benchmark(
    model_name: str,
    model: ChatModel,
    *,
    cases: Sequence[BenchmarkCase] | None = None,
    repeats: int = 1,
    max_turns: int = 4,
) -> dict[str, Any]:
    if repeats <= 0:
        raise ValueError("repeats must be positive")
    from ._summary import summarize_results
    selected = tuple(cases or default_cases())
    results = [
        run_case(model, case, max_turns=max_turns)
        for _ in range(repeats)
        for case in selected
    ]
    return {
        "model": model_name,
        "repeats": repeats,
        "cases": [result.as_dict() for result in results],
        "summary": summarize_results(model_name, results),
    }


def write_artifact(path: str | Path, artifact: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(
        (json.dumps(artifact, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )


def _has_loop_error(result: Mapping[str, Any]) -> bool:
    error = result.get("error")
    if not isinstance(error, Mapping):
        return False
    return error.get("kind") in {
        "duplicate_tool_call",
        "max_turns",
        "model_error",
        "policy",
        "invalid_json",
        "invalid_response",
    }


def _metric_int(response: Mapping[str, Any], key: str) -> int:
    value = response.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0
