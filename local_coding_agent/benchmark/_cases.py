"""Benchmark case model and the fixed default case set."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
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
)


@dataclass(frozen=True)
class BenchmarkCase:
    id: str
    task: TaskEnvelope
    fixture: Mapping[str, str]
    expected_files: Mapping[str, str]
    oracle: Callable[[Path], tuple[bool, str]] | None = None

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("benchmark case id must be non-empty")
        fixture_paths = set(self.fixture)
        if fixture_paths != set(self.task.files):
            raise ValueError("benchmark fixture paths must match task allowlist")
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
