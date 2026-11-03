"""Benchmark case model and the fixed default case set."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
import os
from typing import Any, Callable, Mapping

from ..task import TaskEnvelope

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
    _hard_normalize_record_oracle,
    _hard_window_total_oracle,
    _hard_parse_flag_oracle,
    _hard_merge_defaults_oracle,
    _hard_strip_lines_oracle,
    _hard_safe_average_oracle,
    _hard_clamp_range_oracle,
    _hard_zip_values_oracle,
    _hard_parse_csv_oracle,
    _hard_build_index_oracle,
    _hard_pricing_contract_oracle,
    _hard_pagination_contract_oracle,
    _hard_format_contract_oracle,
    _hard_codec_contract_oracle,
    _hard_dedupe_records_oracle,
    _hard_count_words_oracle,
    _hard_lookup_fallback_oracle,
    _hard_normalize_pipeline_oracle,
)


@dataclass(frozen=True)
class BenchmarkCase:
    id: str
    task: TaskEnvelope
    fixture: Mapping[str, str]
    expected_files: Mapping[str, str]
    oracle: Callable[[Path], tuple[bool, str]] | None = None
    support_files: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("benchmark case id must be non-empty")
        fixture_paths = set(self.fixture)
        if fixture_paths != set(self.task.files):
            raise ValueError("benchmark fixture paths must match task allowlist")
        support_paths = set(self.support_files)
        if support_paths.intersection(fixture_paths):
            raise ValueError("support files must be outside task allowlist")
        def key(name):
            if not isinstance(name, str):
                raise ValueError('benchmark support file paths must be valid relative paths')
            return os.path.normcase(PurePosixPath(name.replace('\\', '/')).as_posix())
        if {key(name) for name in support_paths}.intersection(key(name) for name in fixture_paths):
            raise ValueError('support files must be outside task allowlist, including path aliases')
        if len({key(name) for name in support_paths}) != len(support_paths):
            raise ValueError('support file path aliases collide')
        for raw_path in support_paths:
            if not isinstance(raw_path, str) or not raw_path or "\x00" in raw_path:
                raise ValueError("benchmark support file paths must be valid relative paths")
            paths = (PurePosixPath(raw_path), PureWindowsPath(raw_path))
            if any(path.is_absolute() or path.drive or path.root or '..' in path.parts for path in paths):
                raise ValueError(
                    f"benchmark support file must be relative and inside workspace: {raw_path!r}"
                )
        if not all(isinstance(content, str) for content in self.support_files.values()):
            raise ValueError("benchmark support file contents must be strings")
        if not self.expected_files:
            raise ValueError("benchmark case must have expected files")
        if not set(self.expected_files).issubset(fixture_paths):
            raise ValueError("expected files must be part of the fixture")


def _edit_case(
    case_id: str,
    goal: str,
    file_name: str,
    buggy: str,
    expected: str,
    context: str,
    acceptance: str,
    oracle: Callable[[Path], tuple[bool, str]],
) -> BenchmarkCase:
    return BenchmarkCase(
        id=case_id,
        task=TaskEnvelope(
            id=case_id,
            goal=goal,
            files=(f"src/{file_name}.py",),
            context=context,
            constraints=("сохранить имя функции", "не добавлять зависимости"),
            acceptance=(acceptance,),
        ),
        fixture={f"src/{file_name}.py": buggy},
        expected_files={f"src/{file_name}.py": expected},
        oracle=oracle,
    )


def default_cases() -> tuple[BenchmarkCase, ...]:
    """Return a small fixed task set with deterministic external oracles."""

    return (
        BenchmarkCase(
            id="unique-preserve-order",
            task=TaskEnvelope(
                id="unique-preserve-order",
                goal="убрать сортировку из unique и сохранить порядок первого появления",
                files=("src/unique.py",),
                context="Функция должна удалить повторы, но не менять порядок входных значений.",
                constraints=("не менять публичную сигнатуру", "не добавлять зависимости"),
                acceptance=("повторы удаляются", "порядок первого появления сохраняется"),
            ),
            fixture={"src/unique.py": "def unique(values):\n    return sorted(set(values))\n"},
            expected_files={"src/unique.py": "def unique(values):\n    return list(dict.fromkeys(values))\n"},
            oracle=_unique_oracle,
        ),
        BenchmarkCase(
            id="limit-inclusive",
            task=TaskEnvelope(
                id="limit-inclusive",
                goal="исправить off-by-one и вернуть ровно limit элементов",
                files=("src/window.py",),
                context="При положительном limit срез должен включать элемент с индексом limit-1.",
                constraints=("изменить только выражение среза",),
                acceptance=("limit=3 возвращает первые три элемента",),
            ),
            fixture={"src/window.py": "def take(values, limit):\n    return values[: limit - 1]\n"},
            expected_files={"src/window.py": "def take(values, limit):\n    return values[:limit]\n"},
            oracle=_limit_oracle,
        ),
        BenchmarkCase(
            id="utf8-json",
            task=TaskEnvelope(
                id="utf8-json",
                goal="сохранить русские символы при сериализации JSON",
                files=("src/encoding.py",),
                context="JSON должен оставаться валидным и не превращать Unicode-символы в escape-последовательности.",
                constraints=("не менять имя функции", "использовать только стандартную библиотеку"),
                acceptance=("ensure_ascii отключён",),
            ),
            fixture={
                "src/encoding.py": "import json\n\ndef encode(value):\n    return json.dumps(value)\n"
            },
            expected_files={
                "src/encoding.py": "import json\n\ndef encode(value):\n    return json.dumps(value, ensure_ascii=False)\n"
            },
            oracle=_utf8_oracle,
        ),
        BenchmarkCase(
            id="avoid-input-mutation",
            task=TaskEnvelope(
                id="avoid-input-mutation",
                goal="добавить flag без изменения входного списка",
                files=("src/flags.py",),
                context="Вызов не должен мутировать values: исходный список должен остаться прежним.",
                constraints=("сохранить имя функции", "не добавлять зависимости"),
                acceptance=("возвращается новый список с flag в конце",),
            ),
            fixture={"src/flags.py": "def append_flag(values, flag):\n    values.append(flag)\n    return values\n"},
            expected_files={
                "src/flags.py": "def append_flag(values, flag):\n    return [*values, flag]\n"
            },
            oracle=_no_mutation_oracle,
        ),
        _edit_case(
            "count-positives",
            "посчитать количество положительных элементов",
            "count",
            "def count_positives(values):\n    return len(values)\n",
            "def count_positives(values):\n    return sum(1 for value in values if value > 0)\n",
            "Считать нужно только элементы строго больше нуля.",
            "счётчик учитывает только положительные элементы",
            _count_positives_oracle,
        ),
        _edit_case(
            "max-value",
            "вернуть максимальное значение из списка",
            "maxval",
            "def max_value(values):\n    return values[0]\n",
            "def max_value(values):\n    return max(values)\n",
            "Функция должна возвращать наибольший элемент входного списка.",
            "для [3,7,2,5] возвращается 7",
            _max_value_oracle,
        ),
        _edit_case(
            "abs-sum",
            "вернуть сумму модулей элементов",
            "abssum",
            "def abs_sum(values):\n    return sum(values)\n",
            "def abs_sum(values):\n    return sum(abs(value) for value in values)\n",
            "Отрицательные числа должны входить в сумму как положительные.",
            "для [-1,2,-3] возвращается 6",
            _abs_sum_oracle,
        ),
        _edit_case(
            "reverse-str",
            "развернуть строку в обратном порядке",
            "reverse",
            "def reverse_str(value):\n    return value\n",
            "def reverse_str(value):\n    return value[::-1]\n",
            "Результат — та же строка в обратном порядке символов.",
            "для 'мир' возвращается 'рим'",
            _reverse_str_oracle,
        ),
        _edit_case(
            "filter-evens",
            "оставить только чётные элементы",
            "evens",
            "def evens(values):\n    return values\n",
            "def evens(values):\n    return [value for value in values if value % 2 == 0]\n",
            "Нужно вернуть новый список только с чётными числами.",
            "для [1,2,3,4,6] возвращается [2,4,6]",
            _filter_evens_oracle,
        ),
        _edit_case(
            "count-words",
            "посчитать слова в строке",
            "words",
            "def count_words(text):\n    return len(text)\n",
            "def count_words(text):\n    return len(text.split())\n",
            "Слова разделены пробелами; считать именно слова, а не символы.",
            "для 'один два три' возвращается 3",
            _count_words_oracle,
        ),
        _edit_case(
            "dict-default",
            "вернуть значение ключа или ноль",
            "defval",
            "def get_or_zero(mapping, key):\n    return mapping[key]\n",
            "def get_or_zero(mapping, key):\n    return mapping.get(key, 0)\n",
            "Отсутствующий ключ не должен выбрасывать ошибку.",
            "для отсутствующего ключа возвращается 0",
            _dict_default_oracle,
        ),
        _edit_case(
            "strip-text",
            "убрать пробелы по краям строки",
            "strip",
            "def normalize(text):\n    return text.replace(' ', '')\n",
            "def normalize(text):\n    return text.strip()\n",
            "Убирать пробелы нужно только по краям, а не внутри.",
            "для '  hi  ' возвращается 'hi'",
            _strip_text_oracle,
        ),
        _edit_case(
            "join-words",
            "склеить слова через пробел",
            "join",
            "def join_words(words):\n    return words\n",
            "def join_words(words):\n    return ' '.join(words)\n",
            "Результат — одна строка из слов, разделённых одиночным пробелом.",
            "для ['a','b','c'] возвращается 'a b c'",
            _join_words_oracle,
        ),
        _edit_case(
            "last-element",
            "вернуть последний элемент списка",
            "last",
            "def last(values):\n    return values[0]\n",
            "def last(values):\n    return values[-1]\n",
            "Нужен именно последний, а не первый элемент.",
            "для [1,2,3] возвращается 3",
            _last_element_oracle,
        ),
        _edit_case(
            "sorted-copy",
            "вернуть отсортированную копию без изменения входа",
            "sortcopy",
            "def sorted_copy(values):\n    values.sort()\n    return values\n",
            "def sorted_copy(values):\n    return sorted(values)\n",
            "Исходный список не должен изменяться.",
            "вход [3,1,2] остаётся неизменным, результат [1,2,3]",
            _sorted_copy_oracle,
        ),
        _edit_case(
            "replace-dash",
            "заменить дефисы на подчёркивания",
            "replace",
            "def replace_dash(text):\n    return text\n",
            "def replace_dash(text):\n    return text.replace('-', '_')\n",
            "Все символы '-' должны стать '_'.",
            "для 'a-b-c' возвращается 'a_b_c'",
            _replace_dash_oracle,
        ),
        _edit_case(
            "starts-with",
            "проверить начало строки",
            "starts",
            "def starts_with(text, prefix):\n    return prefix in text\n",
            "def starts_with(text, prefix):\n    return text.startswith(prefix)\n",
            "Совпадение должно быть именно в начале строки.",
            "для 'hello','he' — True; для 'hello','xy' — False",
            _starts_with_oracle,
        ),
        _edit_case(
            "dot-product",
            "посчитать скалярное произведение",
            "dot",
            "def dot(left, right):\n    return sum(left) * sum(right)\n",
            "def dot(left, right):\n    return sum(a * b for a, b in zip(left, right))\n",
            "Суммировать нужно попарные произведения элементов.",
            "для [1,2,3] и [4,5,6] возвращается 32",
            _dot_product_oracle,
        ),
        _edit_case(
            "min-value",
            "вернуть минимальное значение из списка",
            "minval",
            "def min_value(values):\n    return values[0]\n",
            "def min_value(values):\n    return min(values)\n",
            "Функция должна возвращать наименьший элемент входного списка.",
            "для [3,7,2,5] возвращается 2",
            _min_value_oracle,
        ),
        _edit_case(
            "title-case",
            "перевести каждое слово в регистр заголовка",
            "title",
            "def title_case(text):\n    return text.upper()\n",
            "def title_case(text):\n    return text.title()\n",
            "Первая буква каждого слова — заглавная, остальные — строчные.",
            "для 'hello world' возвращается 'Hello World'",
            _title_case_oracle,
        ),
    )


def _hard_case(
    case_id: str,
    goal: str,
    source: Mapping[str, str],
    expected: Mapping[str, str],
    test_source: str,
    oracle: Callable[[Path], tuple[bool, str]],
    *,
    context: str,
) -> BenchmarkCase:
    test_path = f"tests/test_{case_id.replace('-', '_')}.py"
    check = f"python -m pytest -q {test_path}"
    return BenchmarkCase(
        id=case_id,
        task=TaskEnvelope(
            id=case_id,
            goal=goal,
            files=tuple(source),
            context=context,
            constraints=("не менять публичные имена функций", "не добавлять зависимости"),
            checks=(check,),
            acceptance=("все внешние тесты проходят",),
        ),
        fixture=source,
        support_files={test_path: test_source},
        expected_files=expected,
        oracle=oracle,
    )


def hard_cases() -> tuple[BenchmarkCase, ...]:
    """Return the separate multi-step benchmark suite used for quality gates.

    The source fixture is allowlisted for the model.  The pytest files live in
    ``support_files`` so a model cannot weaken the checks, while the oracle is
    still evaluated independently after the candidate patch is applied.
    """

    return (
        _hard_case(
            "hard-normalize-record",
            "нормализовать имя и не допускать отрицательный score",
            {"src/normalize_record.py": (
                "def normalize(record):\n"
                "    name = record.get('name', '').strip()\n"
                "    score = int(record.get('score', 0))\n"
                "    return {'name': name, 'score': score}\n"
            )},
            {"src/normalize_record.py": (
                "def normalize(record):\n"
                "    name = record.get('name', '').strip().title()\n"
                "    score = max(0, int(record.get('score', 0)))\n"
                "    return {'name': name, 'score': score}\n"
            )},
            "from src.normalize_record import normalize\n\n"
            "def test_name_and_score_are_normalized_together():\n"
            "    assert normalize({'name': '  ada lovelace ', 'score': '-2'}) == {'name': 'Ada Lovelace', 'score': 0}\n",
            _hard_normalize_record_oracle,
            context="Имя обрезается и переводится в title case; score преобразуется в int и ограничивается снизу нулём.",
        ),
        _hard_case(
            "hard-window-total",
            "вернуть сумму и размер выбранного окна",
            {"src/window_total.py": (
                "def window_total(values, start, stop):\n"
                "    window = values[start:stop]\n"
                "    return sum(window), len(values)\n"
            )},
            {"src/window_total.py": (
                "def window_total(values, start, stop):\n"
                "    window = values[start:stop]\n"
                "    return sum(window), len(window)\n"
            )},
            "from src.window_total import window_total\n\n"
            "def test_sum_and_length_use_same_window():\n"
            "    assert window_total([10, 20, 30, 40], 1, 3) == (50, 2)\n",
            _hard_window_total_oracle,
            context="Сумма и длина должны относиться к одному срезу values[start:stop].",
        ),
        _hard_case(
            "hard-parse-flag",
            "распознать текстовые значения true и false без учёта регистра",
            {"src/parse_flag.py": (
                "def parse_flag(text):\n"
                "    parts = text.split('=', 1)\n"
                "    if len(parts) != 2:\n"
                "        return False\n"
                "    return bool(parts[1])\n"
            )},
            {"src/parse_flag.py": (
                "def parse_flag(text):\n"
                "    parts = text.split('=', 1)\n"
                "    if len(parts) != 2:\n"
                "        return False\n"
                "    return parts[1].strip().lower() in {'1', 'true', 'yes'}\n"
            )},
            "from src.parse_flag import parse_flag\n\n"
            "def test_flag_accepts_true_values_and_rejects_false_text():\n"
            "    assert parse_flag('enabled=TRUE') is True\n"
            "    assert parse_flag('enabled= off ') is False\n",
            _hard_parse_flag_oracle,
            context="После знака '=' допускаются 1, true и yes, с пробелами и любым регистром.",
        ),
        _hard_case(
            "hard-merge-defaults",
            "объединить базовые настройки и overrides с приоритетом overrides",
            {"src/merge_defaults.py": (
                "def merge_defaults(base, overrides):\n"
                "    result = {'timeout': 30, 'retries': 2}\n"
                "    result.update(overrides)\n"
                "    result.update(base)\n"
                "    return result\n"
            )},
            {"src/merge_defaults.py": (
                "def merge_defaults(base, overrides):\n"
                "    result = {'timeout': 30, 'retries': 2}\n"
                "    result.update(base)\n"
                "    result.update(overrides)\n"
                "    return result\n"
            )},
            "from src.merge_defaults import merge_defaults\n\n"
            "def test_overrides_win_after_base_settings():\n"
            "    assert merge_defaults({'timeout': 10}, {'timeout': 5}) == {'timeout': 5, 'retries': 2}\n",
            _hard_merge_defaults_oracle,
            context="Сначала применяются base к значениям по умолчанию, затем overrides поверх base.",
        ),
        _hard_case(
            "hard-strip-lines",
            "убрать пустые строки и пробелы по краям каждой строки",
            {"src/strip_lines.py": (
                "def strip_lines(text):\n"
                "    return [line for line in text.split('\\n') if line]\n"
            )},
            {"src/strip_lines.py": (
                "def strip_lines(text):\n"
                "    return [line.strip() for line in text.split('\\n') if line.strip()]\n"
            )},
            "from src.strip_lines import strip_lines\n\n"
            "def test_lines_are_trimmed_and_empty_lines_removed():\n"
            "    assert strip_lines('  one  \\n\\n two \\n') == ['one', 'two']\n",
            _hard_strip_lines_oracle,
            context="Пустые строки определяются после strip, а результат содержит очищенные строки.",
        ),
        _hard_case(
            "hard-safe-average",
            "посчитать среднее значение и вернуть ноль для пустого списка",
            {"src/safe_average.py": (
                "def safe_average(values):\n"
                "    return sum(values) / len(values)\n"
            )},
            {"src/safe_average.py": (
                "def safe_average(values):\n"
                "    if not values:\n"
                "        return 0\n"
                "    return sum(values) / len(values)\n"
            )},
            "from src.safe_average import safe_average\n\n"
            "def test_average_handles_empty_and_nonempty_values():\n"
            "    assert safe_average([]) == 0\n"
            "    assert safe_average([2, 4, 8]) == 14 / 3\n",
            _hard_safe_average_oracle,
            context="Среднее обычного списка сохраняется; пустой список не должен делить на ноль.",
        ),
        _hard_case(
            "hard-clamp-range",
            "ограничить значение одновременно снизу и сверху",
            {"src/clamp_range.py": (
                "def clamp(value, low, high):\n"
                "    return max(value, low)\n"
            )},
            {"src/clamp_range.py": (
                "def clamp(value, low, high):\n"
                "    return min(max(value, low), high)\n"
            )},
            "from src.clamp_range import clamp\n\n"
            "def test_clamp_applies_lower_bound():\n"
            "    assert clamp(-2, 0, 10) == 0\n"
            "\n"
            "def test_clamp_applies_upper_bound():\n"
            "    assert clamp(12, 0, 10) == 10\n",
            _hard_clamp_range_oracle,
            context="Значение должно попасть в закрытый диапазон [low, high].",
        ),
        _hard_case(
            "hard-zip-values",
            "сложить попарные значения до длины более короткого списка",
            {"src/zip_values.py": (
                "def add_pairs(left, right):\n"
                "    return [a + b for a, b in zip(left, right)] + list(left[len(right):])\n"
            )},
            {"src/zip_values.py": (
                "def add_pairs(left, right):\n"
                "    return [a + b for a, b in zip(left, right)]\n"
            )},
            "from src.zip_values import add_pairs\n\n"
            "def test_extra_values_are_not_carried_over():\n"
            "    assert add_pairs([1, 2, 3], [10, 20]) == [11, 22]\n",
            _hard_zip_values_oracle,
            context="Результат содержит только попарные суммы, поэтому хвост длинного списка отбрасывается.",
        ),
        _hard_case(
            "hard-parse-csv",
            "распарсить CSV-подобную строку с пробелами и пустыми полями",
            {"src/parse_csv.py": (
                "def parse_csv(line):\n"
                "    return line.split(',')\n"
            )},
            {"src/parse_csv.py": (
                "def parse_csv(line):\n"
                "    return [part.strip() for part in line.split(',') if part.strip()]\n"
            )},
            "from src.parse_csv import parse_csv\n\n"
            "def test_csv_parts_are_trimmed_and_empty_parts_removed():\n"
            "    assert parse_csv(' a, ,b ,, c ') == ['a', 'b', 'c']\n",
            _hard_parse_csv_oracle,
            context="Разделители дают поля; пробелы очищаются, пустые поля не попадают в результат.",
        ),
        _hard_case(
            "hard-build-index",
            "построить индекс по ключу, сохраняя последнее значение",
            {"src/build_index.py": (
                "def build_index(rows):\n"
                "    result = {}\n"
                "    for row in rows:\n"
                "        result.setdefault(row['id'], row['value'])\n"
                "    return result\n"
            )},
            {"src/build_index.py": (
                "def build_index(rows):\n"
                "    result = {}\n"
                "    for row in rows:\n"
                "        result[row['id']] = row['value']\n"
                "    return result\n"
            )},
            "from src.build_index import build_index\n\n"
            "def test_last_duplicate_row_wins():\n"
            "    assert build_index([{'id': 1, 'value': 'old'}, {'id': 1, 'value': 'new'}]) == {1: 'new'}\n",
            _hard_build_index_oracle,
            context="При повторном id индекс должен отражать последнее наблюденное значение.",
        ),
        _hard_case(
            "hard-pricing-contract",
            "синхронизировать сигнатуру subtotal и вызов в checkout",
            {
                "src/pricing.py": (
                    "def subtotal(items, tax_rate):\n"
                    "    return sum(items)\n"
                ),
                "src/checkout.py": (
                    "def total(items, subtotal_fn, tax_rate):\n"
                    "    return subtotal_fn(items)\n"
                ),
            },
            {
                "src/pricing.py": (
                    "def subtotal(items, tax_rate):\n"
                    "    return sum(items) * (1 + tax_rate)\n"
                ),
                "src/checkout.py": (
                    "def total(items, subtotal_fn, tax_rate):\n"
                    "    return subtotal_fn(items, tax_rate)\n"
                ),
            },
            "from src.checkout import total\nfrom src.pricing import subtotal\n\n"
            "def test_checkout_passes_tax_rate_to_pricing():\n"
            "    assert total([100, 50], subtotal, 0.2) == 180\n",
            _hard_pricing_contract_oracle,
            context="pricing.subtotal принимает tax_rate; checkout.total обязан передать тот же аргумент.",
        ),
        _hard_case(
            "hard-pagination-contract",
            "синхронизировать размер страницы между paging и listing",
            {
                "src/paging.py": (
                    "def page(items, number, size):\n"
                    "    start = number * size\n"
                    "    return items[start:start + size]\n"
                ),
                "src/listing.py": (
                    "def list_page(items, number, size, page_fn):\n"
                    "    return page_fn(items, number)\n"
                ),
            },
            {
                "src/listing.py": (
                    "def list_page(items, number, size, page_fn):\n"
                    "    return page_fn(items, number, size)\n"
                ),
            },
            "from src.listing import list_page\nfrom src.paging import page\n\n"
            "def test_listing_passes_page_size():\n"
            "    assert list_page([0, 1, 2, 3, 4], 1, 2, page) == [2, 3]\n",
            _hard_pagination_contract_oracle,
            context="page требует number и size; listing должен передать оба значения.",
        ),
        _hard_case(
            "hard-format-contract",
            "синхронизировать обязательный prefix между форматтером и отчётом",
            {
                "src/formatting.py": (
                    "def render(value, prefix):\n"
                    "    return f'{prefix}{value}'\n"
                ),
                "src/report.py": (
                    "def render_report(value, prefix, render_fn):\n"
                    "    return render_fn(value)\n"
                ),
            },
            {
                "src/report.py": (
                    "def render_report(value, prefix, render_fn):\n"
                    "    return render_fn(value, prefix)\n"
                ),
            },
            "from src.formatting import render\nfrom src.report import render_report\n\n"
            "def test_report_passes_prefix_to_renderer():\n"
            "    assert render_report('ok', '[', render) == '[ok'\n",
            _hard_format_contract_oracle,
            context="formatting.render требует prefix; report должен сохранить его при вызове render_fn.",
        ),
        _hard_case(
            "hard-codec-contract",
            "синхронизировать флаг lower между codec и consumer",
            {
                "src/codec.py": (
                    "def encode(value, lower):\n"
                    "    return value.strip()\n"
                ),
                "src/consumer.py": (
                    "def normalize(value, lower, encode_fn):\n"
                    "    return encode_fn(value)\n"
                ),
            },
            {
                "src/codec.py": (
                    "def encode(value, lower):\n"
                    "    value = value.strip()\n"
                    "    return value.lower() if lower else value\n"
                ),
                "src/consumer.py": (
                    "def normalize(value, lower, encode_fn):\n"
                    "    return encode_fn(value, lower)\n"
                ),
            },
            "from src.codec import encode\nfrom src.consumer import normalize\n\n"
            "def test_consumer_passes_lower_flag_to_codec():\n"
            "    assert normalize('  AbC ', True, encode) == 'abc'\n"
            "    assert normalize('  AbC ', False, encode) == 'AbC'\n",
            _hard_codec_contract_oracle,
            context="codec.encode принимает lower; consumer обязан передать флаг и сохранить оба режима.",
        ),
        _hard_case(
            "hard-dedupe-records",
            "удалить дубли записей по id, сохранив последнее содержимое",
            {"src/dedupe_records.py": (
                "def dedupe(records):\n"
                "    seen = set()\n"
                "    result = []\n"
                "    for record in records:\n"
                "        if record['id'] not in seen:\n"
                "            seen.add(record['id'])\n"
                "            result.append(record)\n"
                "    return result\n"
            )},
            {"src/dedupe_records.py": (
                "def dedupe(records):\n"
                "    positions = {}\n"
                "    result = []\n"
                "    for record in records:\n"
                "        if record['id'] in positions:\n"
                "            result[positions[record['id']]] = record\n"
                "        else:\n"
                "            positions[record['id']] = len(result)\n"
                "            result.append(record)\n"
                "    return result\n"
            )},
            "from src.dedupe_records import dedupe\n\n"
            "def test_dedupe_keeps_first_position_and_last_value():\n"
            "    assert dedupe([{'id': 1, 'v': 'a'}, {'id': 2, 'v': 'b'}, {'id': 1, 'v': 'c'}]) == [{'id': 1, 'v': 'c'}, {'id': 2, 'v': 'b'}]\n",
            _hard_dedupe_records_oracle,
            context="Порядок определяется первым появлением id, значение при дубликате берётся последнее.",
        ),
        _hard_case(
            "hard-count-words",
            "посчитать непустые слова после смешанных разделителей",
            {"src/count_words_hard.py": (
                "def count_words(text):\n"
                "    return len(text.split(' '))\n"
            )},
            {"src/count_words_hard.py": (
                "def count_words(text):\n"
                "    return len(text.split())\n"
            )},
            "from src.count_words_hard import count_words\n\n"
            "def test_mixed_whitespace_counts_words():\n"
            "    assert count_words(' one\\t two  three ') == 3\n",
            _hard_count_words_oracle,
            context="Любые последовательности пробельных символов разделяют слова; пустые элементы не считаются.",
        ),
        _hard_case(
            "hard-lookup-fallback",
            "вернуть fallback только при отсутствии ключа, сохранив falsey значения",
            {"src/lookup_fallback.py": (
                "def lookup(mapping, key, fallback):\n"
                "    return mapping.get(key) or fallback\n"
            )},
            {"src/lookup_fallback.py": (
                "def lookup(mapping, key, fallback):\n"
                "    return mapping[key] if key in mapping else fallback\n"
            )},
            "from src.lookup_fallback import lookup\n\n"
            "def test_present_falsey_values_are_not_replaced():\n"
            "    assert lookup({'enabled': False}, 'enabled', True) is False\n"
            "    assert lookup({}, 'enabled', True) is True\n",
            _hard_lookup_fallback_oracle,
            context="Fallback используется только для отсутствующего ключа, а не для False, 0 или пустой строки.",
        ),
        _hard_case(
            "hard-normalize-pipeline",
            "согласовать очистку и форматирование имени в одной цепочке функций",
            {"src/normalize_pipeline.py": (
                "def parse_name(text):\n"
                "    return text\n\n"
                "def format_name(name):\n"
                "    return name\n\n"
                "def normalize_name(text):\n"
                "    return format_name(parse_name(text))\n"
            )},
            {"src/normalize_pipeline.py": (
                "def parse_name(text):\n"
                "    return text.strip()\n\n"
                "def format_name(name):\n"
                "    return name.lower()\n\n"
                "def normalize_name(text):\n"
                "    return format_name(parse_name(text))\n"
            )},
            "from src.normalize_pipeline import normalize_name\n\n"
            "def test_pipeline_strips_then_formats_name():\n"
            "    assert normalize_name('  Ada  ') == 'ada'\n",
            _hard_normalize_pipeline_oracle,
            context="parse_name очищает ввод, format_name приводит его к нижнему регистру, normalize_name связывает оба шага.",
        ),
    )
