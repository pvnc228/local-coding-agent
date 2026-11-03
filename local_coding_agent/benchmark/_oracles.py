"""External correctness oracles for benchmark cases.

Note: oracle bodies reference ``_load_function`` as a module-level global.
That symbol is injected at call time by ``benchmark_oracle_worker.py``, which
executes the oracle source in a restricted child process; it is never resolved
in this package.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable


def _unique_oracle(workspace: Path) -> tuple[bool, str]:
    unique = _load_function(workspace / "src/unique.py", "unique")
    if unique([3, 1, 2, 1, 3]) != [3, 1, 2]:
        return False, "external oracle mismatch: unique does not preserve order"
    return True, ""


def _limit_oracle(workspace: Path) -> tuple[bool, str]:
    take = _load_function(workspace / "src/window.py", "take")
    if take([0, 1, 2, 3], 3) != [0, 1, 2]:
        return False, "external oracle mismatch: limit is not inclusive"
    return True, ""


def _utf8_oracle(workspace: Path) -> tuple[bool, str]:
    encode = _load_function(workspace / "src/encoding.py", "encode")
    encoded = encode({"message": "мир"})
    if not isinstance(encoded, str) or "\\u" in encoded or '"мир"' not in encoded:
        return False, "external oracle mismatch: Unicode was escaped"
    return True, ""


def _no_mutation_oracle(workspace: Path) -> tuple[bool, str]:
    append_flag = _load_function(workspace / "src/flags.py", "append_flag")
    values = ["a"]
    returned = append_flag(values, "b")
    if values != ["a"] or returned != ["a", "b"] or returned is values:
        return False, "external oracle mismatch: input list was mutated"
    return True, ""


def _count_positives_oracle(workspace: Path) -> tuple[bool, str]:
    fn = _load_function(workspace / "src/count.py", "count_positives")
    if fn([-1, 2, -3, 4, 0]) != 2:
        return False, "external oracle mismatch: count_positives"
    return True, ""


def _max_value_oracle(workspace: Path) -> tuple[bool, str]:
    fn = _load_function(workspace / "src/maxval.py", "max_value")
    if fn([3, 7, 2, 5]) != 7:
        return False, "external oracle mismatch: max_value"
    return True, ""


def _abs_sum_oracle(workspace: Path) -> tuple[bool, str]:
    fn = _load_function(workspace / "src/abssum.py", "abs_sum")
    if fn([-1, 2, -3]) != 6:
        return False, "external oracle mismatch: abs_sum"
    return True, ""


def _reverse_str_oracle(workspace: Path) -> tuple[bool, str]:
    fn = _load_function(workspace / "src/reverse.py", "reverse_str")
    if fn("мир") != "рим":
        return False, "external oracle mismatch: reverse_str"
    return True, ""


def _filter_evens_oracle(workspace: Path) -> tuple[bool, str]:
    fn = _load_function(workspace / "src/evens.py", "evens")
    if fn([1, 2, 3, 4, 6]) != [2, 4, 6]:
        return False, "external oracle mismatch: evens"
    return True, ""


def _count_words_oracle(workspace: Path) -> tuple[bool, str]:
    fn = _load_function(workspace / "src/words.py", "count_words")
    if fn("один два три") != 3:
        return False, "external oracle mismatch: count_words"
    return True, ""


def _dict_default_oracle(workspace: Path) -> tuple[bool, str]:
    fn = _load_function(workspace / "src/defval.py", "get_or_zero")
    if fn({"a": 1}, "b") != 0 or fn({"a": 5}, "a") != 5:
        return False, "external oracle mismatch: get_or_zero"
    return True, ""


def _strip_text_oracle(workspace: Path) -> tuple[bool, str]:
    fn = _load_function(workspace / "src/strip.py", "normalize")
    if fn("  hi  ") != "hi":
        return False, "external oracle mismatch: normalize"
    return True, ""


def _join_words_oracle(workspace: Path) -> tuple[bool, str]:
    fn = _load_function(workspace / "src/join.py", "join_words")
    if fn(["a", "b", "c"]) != "a b c":
        return False, "external oracle mismatch: join_words"
    return True, ""


def _last_element_oracle(workspace: Path) -> tuple[bool, str]:
    fn = _load_function(workspace / "src/last.py", "last")
    if fn([1, 2, 3]) != 3:
        return False, "external oracle mismatch: last"
    return True, ""


def _sorted_copy_oracle(workspace: Path) -> tuple[bool, str]:
    fn = _load_function(workspace / "src/sortcopy.py", "sorted_copy")
    values = [3, 1, 2]
    returned = fn(values)
    if returned != [1, 2, 3] or values != [3, 1, 2]:
        return False, "external oracle mismatch: sorted_copy"
    return True, ""


def _replace_dash_oracle(workspace: Path) -> tuple[bool, str]:
    fn = _load_function(workspace / "src/replace.py", "replace_dash")
    if fn("a-b-c") != "a_b_c":
        return False, "external oracle mismatch: replace_dash"
    return True, ""


def _starts_with_oracle(workspace: Path) -> tuple[bool, str]:
    fn = _load_function(workspace / "src/starts.py", "starts_with")
    if not fn("hello", "he") or fn("hello", "xy"):
        return False, "external oracle mismatch: starts_with"
    return True, ""


def _dot_product_oracle(workspace: Path) -> tuple[bool, str]:
    fn = _load_function(workspace / "src/dot.py", "dot")
    if fn([1, 2, 3], [4, 5, 6]) != 32:
        return False, "external oracle mismatch: dot"
    return True, ""


def _min_value_oracle(workspace: Path) -> tuple[bool, str]:
    fn = _load_function(workspace / "src/minval.py", "min_value")
    if fn([3, 7, 2, 5]) != 2:
        return False, "external oracle mismatch: min_value"
    return True, ""


def _title_case_oracle(workspace: Path) -> tuple[bool, str]:
    fn = _load_function(workspace / "src/title.py", "title_case")
    if fn("hello world") != "Hello World":
        return False, "external oracle mismatch: title_case"
    return True, ""


def _hard_normalize_record_oracle(workspace: Path) -> tuple[bool, str]:
    fn = _load_function(workspace / "src/normalize_record.py", "normalize")
    if fn({"name": "  ada lovelace ", "score": "-2"}) != {"name": "Ada Lovelace", "score": 0}:
        return False, "external oracle mismatch: normalize_record"
    return True, ""


def _hard_window_total_oracle(workspace: Path) -> tuple[bool, str]:
    fn = _load_function(workspace / "src/window_total.py", "window_total")
    if fn([10, 20, 30, 40], 1, 3) != (50, 2):
        return False, "external oracle mismatch: window_total"
    return True, ""


def _hard_parse_flag_oracle(workspace: Path) -> tuple[bool, str]:
    fn = _load_function(workspace / "src/parse_flag.py", "parse_flag")
    if fn("enabled=TRUE") is not True or fn("enabled= off ") is not False:
        return False, "external oracle mismatch: parse_flag"
    return True, ""


def _hard_merge_defaults_oracle(workspace: Path) -> tuple[bool, str]:
    fn = _load_function(workspace / "src/merge_defaults.py", "merge_defaults")
    if fn({"timeout": 10}, {"timeout": 5}) != {"timeout": 5, "retries": 2}:
        return False, "external oracle mismatch: merge_defaults"
    return True, ""


def _hard_strip_lines_oracle(workspace: Path) -> tuple[bool, str]:
    fn = _load_function(workspace / "src/strip_lines.py", "strip_lines")
    if fn("  one  \n\n two \n") != ["one", "two"]:
        return False, "external oracle mismatch: strip_lines"
    return True, ""


def _hard_safe_average_oracle(workspace: Path) -> tuple[bool, str]:
    fn = _load_function(workspace / "src/safe_average.py", "safe_average")
    if fn([]) != 0 or fn([2, 4, 8]) != 14 / 3:
        return False, "external oracle mismatch: safe_average"
    return True, ""


def _hard_clamp_range_oracle(workspace: Path) -> tuple[bool, str]:
    fn = _load_function(workspace / "src/clamp_range.py", "clamp")
    if fn(-2, 0, 10) != 0 or fn(12, 0, 10) != 10:
        return False, "external oracle mismatch: clamp_range"
    return True, ""


def _hard_zip_values_oracle(workspace: Path) -> tuple[bool, str]:
    fn = _load_function(workspace / "src/zip_values.py", "add_pairs")
    if fn([1, 2, 3], [10, 20]) != [11, 22]:
        return False, "external oracle mismatch: zip_values"
    return True, ""


def _hard_parse_csv_oracle(workspace: Path) -> tuple[bool, str]:
    fn = _load_function(workspace / "src/parse_csv.py", "parse_csv")
    if fn(" a, ,b ,, c ") != ["a", "b", "c"]:
        return False, "external oracle mismatch: parse_csv"
    return True, ""


def _hard_build_index_oracle(workspace: Path) -> tuple[bool, str]:
    fn = _load_function(workspace / "src/build_index.py", "build_index")
    rows = [{"id": 1, "value": "old"}, {"id": 1, "value": "new"}]
    if fn(rows) != {1: "new"}:
        return False, "external oracle mismatch: build_index"
    return True, ""


def _hard_pricing_contract_oracle(workspace: Path) -> tuple[bool, str]:
    subtotal = _load_function(workspace / "src/pricing.py", "subtotal")
    total = _load_function(workspace / "src/checkout.py", "total")
    if total([100, 50], subtotal, 0.2) != 180:
        return False, "external oracle mismatch: pricing contract"
    return True, ""


def _hard_pagination_contract_oracle(workspace: Path) -> tuple[bool, str]:
    page = _load_function(workspace / "src/paging.py", "page")
    list_page = _load_function(workspace / "src/listing.py", "list_page")
    if list_page([0, 1, 2, 3, 4], 1, 2, page) != [2, 3]:
        return False, "external oracle mismatch: pagination contract"
    return True, ""


def _hard_format_contract_oracle(workspace: Path) -> tuple[bool, str]:
    render = _load_function(workspace / "src/formatting.py", "render")
    report = _load_function(workspace / "src/report.py", "render_report")
    if report("ok", "[", render) != "[ok":
        return False, "external oracle mismatch: format contract"
    return True, ""


def _hard_codec_contract_oracle(workspace: Path) -> tuple[bool, str]:
    encode = _load_function(workspace / "src/codec.py", "encode")
    normalize = _load_function(workspace / "src/consumer.py", "normalize")
    if normalize("  AbC ", True, encode) != "abc" or normalize("  AbC ", False, encode) != "AbC":
        return False, "external oracle mismatch: codec contract"
    return True, ""


def _hard_dedupe_records_oracle(workspace: Path) -> tuple[bool, str]:
    fn = _load_function(workspace / "src/dedupe_records.py", "dedupe")
    records = [{"id": 1, "v": "a"}, {"id": 2, "v": "b"}, {"id": 1, "v": "c"}]
    expected = [{"id": 1, "v": "c"}, {"id": 2, "v": "b"}]
    if fn(records) != expected:
        return False, "external oracle mismatch: dedupe_records"
    return True, ""


def _hard_count_words_oracle(workspace: Path) -> tuple[bool, str]:
    fn = _load_function(workspace / "src/count_words_hard.py", "count_words")
    if fn(" one\t two  three ") != 3:
        return False, "external oracle mismatch: count_words_hard"
    return True, ""


def _hard_lookup_fallback_oracle(workspace: Path) -> tuple[bool, str]:
    fn = _load_function(workspace / "src/lookup_fallback.py", "lookup")
    if fn({"enabled": False}, "enabled", True) is not False or fn({}, "enabled", True) is not True:
        return False, "external oracle mismatch: lookup_fallback"
    return True, ""


def _hard_normalize_pipeline_oracle(workspace: Path) -> tuple[bool, str]:
    fn = _load_function(workspace / "src/normalize_pipeline.py", "normalize_name")
    if fn("  Ada  ") != "ada":
        return False, "external oracle mismatch: normalize_pipeline"
    return True, ""
