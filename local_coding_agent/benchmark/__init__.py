"""Comparable, proposal-only benchmark for local coding models."""

from __future__ import annotations

from ._cases import BenchmarkCase, default_cases
from ._oracles import (
    _unique_oracle,
    _limit_oracle,
    _utf8_oracle,
    _no_mutation_oracle,
    _count_positives_oracle,
    _max_value_oracle,
    _abs_sum_oracle,
    _reverse_str_oracle,
    _filter_evens_oracle,
    _count_words_oracle,
    _dict_default_oracle,
    _strip_text_oracle,
    _join_words_oracle,
    _last_element_oracle,
    _sorted_copy_oracle,
    _replace_dash_oracle,
    _starts_with_oracle,
    _dot_product_oracle,
    _min_value_oracle,
    _title_case_oracle,
)
from ._runner import (
    BenchmarkCaseResult,
    ChatModel,
    InstrumentedModel,
    run_benchmark,
    run_case,
    write_artifact,
)
from ._stats import _percent, _wilson_score_interval
from ._summary import (
    CAPABILITY_FAILURE_CATEGORIES,
    CONTRACT_FRICTION_CATEGORIES,
    categorize_failure_taxonomy,
    summarize_results,
)

__all__ = [
    "BenchmarkCase",
    "ChatModel",
    "InstrumentedModel",
    "BenchmarkCaseResult",
    "default_cases",
    "run_case",
    "run_benchmark",
    "summarize_results",
    "write_artifact",
    "categorize_failure_taxonomy",
    "CONTRACT_FRICTION_CATEGORIES",
    "CAPABILITY_FAILURE_CATEGORIES",
]
