"""Benchmark result summaries and failure taxonomy."""

from __future__ import annotations

from statistics import mean, median
from typing import Any, Mapping, Sequence

from ._runner import BenchmarkCaseResult
from ._stats import _percent, _wilson_score_interval


def summarize_results(model_name: str, results: Sequence[BenchmarkCaseResult]) -> dict[str, Any]:
    total = len(results)
    if total == 0:
        raise ValueError("cannot summarize an empty benchmark")
    total_eval_tokens = sum(result.eval_count for result in results)
    total_eval_duration_ns = sum(result.eval_duration_ns for result in results)
    eval_tps = (
        round(total_eval_tokens * 1_000_000_000.0 / total_eval_duration_ns, 2)
        if total_eval_duration_ns > 0
        else 0.0
    )
    total_prompt_tokens = sum(result.prompt_tokens for result in results)
    total_prompt_eval_duration_ns = sum(result.prompt_eval_duration_ns for result in results)
    prompt_eval_tps = (
        round(total_prompt_tokens * 1_000_000_000.0 / total_prompt_eval_duration_ns, 2)
        if total_prompt_eval_duration_ns > 0
        else 0.0
    )
    correct_count = sum(result.correct for result in results)
    loop_reliable_count = sum(result.loop_reliable for result in results)
    patch_applied_count = sum(result.patch_applied for result in results)
    return {
        "model": model_name,
        "cases": total,
        "correctness_percent": _percent(correct_count, total),
        "correctness_ci_95": _wilson_score_interval(correct_count, total),
        "tool_loop_reliability_percent": _percent(loop_reliable_count, total),
        "tool_loop_reliability_ci_95": _wilson_score_interval(loop_reliable_count, total),
        "validation_percent": _percent(sum(result.validation_valid for result in results), total),
        "patch_apply_percent": _percent(patch_applied_count, total),
        "patch_apply_ci_95": _wilson_score_interval(patch_applied_count, total),
        "error_categories": _categorize_errors(results),
        "failure_taxonomy": categorize_failure_taxonomy(results),
        "average_wall_time_ms": round(mean(result.wall_time_ms for result in results), 3),
        "median_wall_time_ms": round(median(result.wall_time_ms for result in results), 3),
        "model_calls": sum(result.model_calls for result in results),
        "tool_calls": sum(result.tool_calls for result in results),
        "total_duration_ms": round(sum(result.total_duration_ns for result in results) / 1_000_000, 3),
        "load_duration_ms": round(sum(result.load_duration_ns for result in results) / 1_000_000, 3),
        "prompt_tokens": total_prompt_tokens,
        "eval_tokens": total_eval_tokens,
        "prompt_eval_duration_ms": round(total_prompt_eval_duration_ns / 1_000_000, 3),
        "eval_duration_ms": round(total_eval_duration_ns / 1_000_000, 3),
        "eval_tokens_per_second": eval_tps,
        "prompt_tokens_per_second": prompt_eval_tps,
    }


def _categorize_errors(results: Sequence[BenchmarkCaseResult]) -> dict[str, int]:
    categories: dict[str, int] = {}
    for r in results:
        if r.correct and r.loop_reliable:
            continue
        err_msg = r.patch_error or ""
        error_kind = (
            r.result.get("error", {}).get("kind")
            if isinstance(r.result.get("error"), Mapping)
            else None
        )
        if "not line-aligned" in err_msg:
            cat = "search_not_line_aligned"
        elif "search block not found" in err_msg or "not found" in err_msg:
            cat = "search_not_found"
        elif "ambiguous" in err_msg:
            cat = "search_ambiguous"
        elif "git apply exited" in err_msg or "does not apply" in err_msg:
            cat = "git_apply_failed"
        elif "oracle mismatch" in err_msg:
            cat = "oracle_mismatch"
        elif error_kind:
            cat = str(error_kind)
        elif not r.patch_applied:
            cat = "patch_not_applied"
        else:
            cat = "other"
        categories[cat] = categories.get(cat, 0) + 1
    return dict(sorted(categories.items()))


CONTRACT_FRICTION_CATEGORIES: frozenset[str] = frozenset({
    "search_not_line_aligned",
    "search_not_found",
    "search_ambiguous",
    "git_apply_failed",
    "patch_not_applied",
    "invalid_json",
    "invalid_response",
    "duplicate_tool_call",
    "preflight_rejected",
    "context_limit",
    "policy",
})

CAPABILITY_FAILURE_CATEGORIES: frozenset[str] = frozenset({
    "oracle_mismatch",
    "test_check_failed",
    "check_failed",
    "model_error",
    "timeout",
    "cancelled",
})


def categorize_failure_taxonomy(results: Sequence[BenchmarkCaseResult]) -> dict[str, Any]:
    categories = _categorize_errors(results)
    contract_friction: dict[str, int] = {}
    capability_failures: dict[str, int] = {}
    other: dict[str, int] = {}

    for cat, count in categories.items():
        if cat in CONTRACT_FRICTION_CATEGORIES:
            contract_friction[cat] = count
        elif cat in CAPABILITY_FAILURE_CATEGORIES:
            capability_failures[cat] = count
        else:
            other[cat] = count

    friction_count = sum(contract_friction.values())
    capability_count = sum(capability_failures.values())
    other_count = sum(other.values())
    total_failures = friction_count + capability_count + other_count

    return {
        "total_failures": total_failures,
        "contract_friction_count": friction_count,
        "capability_failure_count": capability_count,
        "friction_ratio": round(friction_count / total_failures, 2) if total_failures else 0.0,
        "capability_ratio": round(capability_count / total_failures, 2) if total_failures else 0.0,
        "contract_friction": dict(sorted(contract_friction.items())),
        "capability_failures": dict(sorted(capability_failures.items())),
        "other": dict(sorted(other.items())),
    }
