"""Benchmark execution: run one model against a disposable fixture and judge it."""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter_ns
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

from ..controller import Controller

from ._cases import BenchmarkCase, default_cases, hard_cases
from ._judge import _judge_patch, _validate_patch_for_case


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
        self.eval_count: int | None = None
        self._eval_count_seen = False
        self.eval_duration_ns = 0
        self.proposed_patches: list[str] = []
        self.proposed_edits: list[list[dict[str, Any]]] = []

    def chat(self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]]) -> dict[str, Any]:
        self.model_calls += 1
        try:
            response = self.model.chat(messages, tools=tools)
        except Exception:
            self.eval_count = None
            self._eval_count_seen = True
            raise
        self._record_proposed_patches(response)
        self.total_duration_ns += _metric_int(response, "total_duration")
        self.load_duration_ns += _metric_int(response, "load_duration")
        self.prompt_tokens += _metric_int(response, "prompt_eval_count")
        self.prompt_eval_duration_ns += _metric_int(response, "prompt_eval_duration")
        eval_count = _metric_optional_int(response, "eval_count")
        if not self._eval_count_seen:
            self.eval_count = eval_count
            self._eval_count_seen = True
        elif self.eval_count is not None and eval_count is not None:
            self.eval_count += eval_count
        else:
            self.eval_count = None
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
    eval_count: int | None
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
            if self.eval_count is not None and self.eval_duration_ns > 0
            else None
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


def _benchmark_controller(
    base_model: ChatModel,
    model_factory: Callable[[int], ChatModel] | None,
    index: int,
    shared_instrumented: InstrumentedModel,
    instrumented_models: list[InstrumentedModel],
    workspace: Path,
    *,
    max_turns: int,
    max_repair_rounds: int,
) -> Controller:
    """Build one sampled controller, optionally selecting a fresh model client."""

    del base_model
    if model_factory is None:
        selected = shared_instrumented
    else:
        selected = InstrumentedModel(model_factory(index))
        instrumented_models.append(selected)
    return Controller(
        selected,
        workspace,
        max_turns=max_turns,
        max_repair_rounds=max_repair_rounds,
    )


def run_case(
    model: ChatModel,
    case: BenchmarkCase,
    *,
    max_turns: int = 4,
    max_repair_rounds: int = 0,
    samples: int = 1,
    strategy: str = "single",
    max_model_calls: int = 30,
    model_factory: Callable[[int], ChatModel] | None = None,
) -> BenchmarkCaseResult:
    """Run one model against a disposable fixture and judge its proposal externally."""

    instrumented_models: list[InstrumentedModel] = []
    instrumented = model if isinstance(model, InstrumentedModel) else InstrumentedModel(model)
    if model_factory is None:
        instrumented_models.append(instrumented)
    with tempfile.TemporaryDirectory(prefix="local-benchmark-") as temp_dir:
        workspace = Path(temp_dir)
        for raw_path, content in {**case.fixture, **case.support_files}.items():
            target = workspace / raw_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="")

        started = perf_counter_ns()
        # F0 evaluates proposal quality before the later scratch/apply phases.
        # Keep the case envelope intact for every quality mode.  With zero
        # repair rounds, the controller records format/scope acceptance and
        # the same allowlisted checks run after apply below; with repairs, the
        # shared quality seam verifies the candidate in scratch first.
        quality_task = case.task
        from ..quality import run_quality

        result = run_quality(
            quality_task,
            lambda index: _benchmark_controller(
                model,
                model_factory,
                index,
                instrumented,
                instrumented_models,
                workspace,
                max_turns=max_turns,
                max_repair_rounds=max_repair_rounds,
            ),
            samples=samples,
            strategy=strategy,
            max_model_calls=max_model_calls,
        )
        wall_time_ms = (perf_counter_ns() - started) / 1_000_000
        fallback_patch = next(
            (
                patch
                for candidate in reversed(instrumented_models)
                for patch in reversed(candidate.proposed_patches)
            ),
            "",
        )
        fallback_edits = next(
            (
                edits
                for candidate in reversed(instrumented_models)
                for edits in reversed(candidate.proposed_edits)
            ),
            None,
        )
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
            validation_valid = _validate_patch_for_case(
                fallback_patch,
                case,
                include_checks=False,
            ).valid
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
            model_calls=sum(candidate.model_calls for candidate in instrumented_models),
            tool_calls=tool_calls,
            total_duration_ns=sum(candidate.total_duration_ns for candidate in instrumented_models),
            load_duration_ns=sum(candidate.load_duration_ns for candidate in instrumented_models),
            prompt_tokens=sum(candidate.prompt_tokens for candidate in instrumented_models),
            prompt_eval_duration_ns=sum(candidate.prompt_eval_duration_ns for candidate in instrumented_models),
            eval_count=_sum_optional_metric(candidate.eval_count for candidate in instrumented_models),
            eval_duration_ns=sum(candidate.eval_duration_ns for candidate in instrumented_models),
            result=result,
        )


def run_benchmark(
    model_name: str,
    model: ChatModel,
    *,
    cases: Sequence[BenchmarkCase] | None = None,
    repeats: int = 1,
    max_turns: int = 4,
    suite: str = "default",
    max_repair_rounds: int = 0,
    samples: int = 1,
    strategy: str = "single",
    max_model_calls: int = 30,
    model_factory: Callable[[int], ChatModel] | None = None,
) -> dict[str, Any]:
    if repeats <= 0:
        raise ValueError("repeats must be positive")
    if suite not in {"default", "hard"}:
        raise ValueError("suite must be 'default' or 'hard'")
    from ._summary import summarize_results
    selected = tuple(cases) if cases is not None else (
        hard_cases() if suite == "hard" else default_cases()
    )
    results = [
        run_case(
            model,
            case,
            max_turns=max_turns,
            max_repair_rounds=max_repair_rounds,
            samples=samples,
            strategy=strategy,
            max_model_calls=max_model_calls,
            model_factory=model_factory,
        )
        for _ in range(repeats)
        for case in selected
    ]
    return {
        "model": model_name,
        "suite": suite,
        "repeats": repeats,
        "samples": samples,
        "strategy": strategy,
        "max_repair_rounds": max_repair_rounds,
        "max_model_calls": max_model_calls,
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


def _metric_optional_int(response: Mapping[str, Any], key: str) -> int | None:
    value = response.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _sum_optional_metric(values: Iterable[int | None]) -> int | None:
    materialized = list(values)
    if not materialized or any(value is None for value in materialized):
        return None
    return sum(value for value in materialized if value is not None)
