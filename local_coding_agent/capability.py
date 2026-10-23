"""Multi-Dimensional Capability Ladder and Intelligence Gatekeeper (R15–R16)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .benchmark import (
    BenchmarkCase,
    ChatModel,
    run_case,
    _wilson_score_interval,
    _percent,
)
from .task import TaskEnvelope


class Tier(IntEnum):
    SYNTAX_TIER_0 = 0
    ATOMIC_TIER_1 = 1
    MULTI_HUNK_TIER_2 = 2
    CROSS_FILE_TIER_3 = 3
    ALGORITHMIC_TIER_4 = 4


TIER_LABELS: Mapping[int, str] = {
    Tier.SYNTAX_TIER_0: "Syntax & Formatting Repair",
    Tier.ATOMIC_TIER_1: "Atomic Pure Functions",
    Tier.MULTI_HUNK_TIER_2: "Single-File Multi-Hunk",
    Tier.CROSS_FILE_TIER_3: "Cross-File Invariants",
    Tier.ALGORITHMIC_TIER_4: "Algorithmic & Strict Constraints",
}

GRANULARITY_BY_TIER: Mapping[int, str] = {
    Tier.SYNTAX_TIER_0: "atomic_hunk",
    Tier.ATOMIC_TIER_1: "function_level",
    Tier.MULTI_HUNK_TIER_2: "file_level",
    Tier.CROSS_FILE_TIER_3: "multi_file",
    Tier.ALGORITHMIC_TIER_4: "multi_file",
}


@dataclass(frozen=True)
class CapabilityVector:
    model: str
    overall_tier: int
    tier_label: str
    confidence_95_ci: tuple[float, float]
    correctness_percent: float
    granularity_tolerance: str
    turn_horizon: int
    languages: tuple[str, ...]
    tps_generation: float | None
    tested_tiers: dict[int, dict[str, Any]] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "overall_tier": int(self.overall_tier),
            "tier_label": self.tier_label,
            "confidence_95_ci": list(self.confidence_95_ci),
            "correctness_percent": self.correctness_percent,
            "granularity_tolerance": self.granularity_tolerance,
            "turn_horizon": self.turn_horizon,
            "languages": list(self.languages),
            "tps_generation": self.tps_generation,
            "tested_tiers": self.tested_tiers,
            "timestamp": self.timestamp,
        }


def _syntax_colon_oracle(workspace: Path) -> tuple[bool, str]:
    try:
        fn = _load_function(workspace / "src/syntax_add.py", "add")
        if fn(2, 3) != 5:
            return False, "external oracle mismatch: add(2, 3) != 5"
        return True, ""
    except Exception as exc:
        return False, f"syntax error remains: {exc}"


def _syntax_typo_oracle(workspace: Path) -> tuple[bool, str]:
    try:
        fn = _load_function(workspace / "src/square.py", "square")
        if fn(4) != 16:
            return False, "external oracle mismatch: square(4) != 16"
        return True, ""
    except Exception as exc:
        return False, f"error in square: {exc}"


def _multihunk_divide_oracle(workspace: Path) -> tuple[bool, str]:
    try:
        calc = _load_function(workspace / "src/calc.py", "calculate_ratio")
        if calc(10, 2) != 5.0 or calc(10, 0) != 0.0:
            return False, "external oracle mismatch: ratio calculation incorrect"
        return True, ""
    except Exception as exc:
        return False, f"calc error: {exc}"


def _crossfile_oracle(workspace: Path) -> tuple[bool, str]:
    try:
        service = _load_function(workspace / "src/service.py", "render_user")
        res = service("Alice", 30)
        if res != "User: Alice, Age: 30":
            return False, f"expected 'User: Alice, Age: 30', got {res!r}"
        return True, ""
    except Exception as exc:
        return False, f"crossfile error: {exc}"


def _algo_lru_oracle(workspace: Path) -> tuple[bool, str]:
    try:
        cache_cls = _load_function(workspace / "src/lru.py", "SimpleLRU")
        c = cache_cls(capacity=2)
        c.put("a", 1)
        c.put("b", 2)
        if c.get("a") != 1:
            return False, "LRU get failed"
        c.put("c", 3)  # evicts b
        if c.get("b") is not None or c.get("a") != 1 or c.get("c") != 3:
            return False, "LRU eviction failed"
        return True, ""
    except Exception as exc:
        return False, f"algo error: {exc}"



def ladder_cases() -> dict[Tier, tuple[BenchmarkCase, ...]]:
    """Return benchmark test cases partitioned by difficulty tier."""
    from .benchmark import (
        _unique_oracle,
        _limit_oracle,
        _count_positives_oracle,
        _abs_sum_oracle,
    )

    t0_cases = (
        BenchmarkCase(
            id="syntax-missing-colon",
            task=TaskEnvelope(
                id="syntax-missing-colon",
                goal="исправить синтаксическую ошибку отсутствующего двоеточия",
                files=("src/syntax_add.py",),
                context="В определении функции пропущено двоеточие.",
                constraints=("не менять имя функции",),
                acceptance=("синтаксис валиден", "функция складывает числа"),
            ),
            fixture={"src/syntax_add.py": "def add(a, b)\n    return a + b\n"},
            expected_files={"src/syntax_add.py": "def add(a, b):\n    return a + b\n"},
            oracle=_syntax_colon_oracle,
        ),
        BenchmarkCase(
            id="syntax-typo-var",
            task=TaskEnvelope(
                id="syntax-typo-var",
                goal="исправить опечатку в имени переменной",
                files=("src/square.py",),
                context="Переменная y не определена, нужно использовать x.",
                constraints=("не менять имя функции",),
                acceptance=("square(4) == 16",),
            ),
            fixture={"src/square.py": "def square(x):\n    return y * x\n"},
            expected_files={"src/square.py": "def square(x):\n    return x * x\n"},
            oracle=_syntax_typo_oracle,
        ),
    )

    t1_cases = (
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
            id="count-positives",
            task=TaskEnvelope(
                id="count-positives",
                goal="посчитать количество положительных элементов",
                files=("src/count.py",),
                context="Считать нужно только элементы строго больше нуля.",
                constraints=("сохранить имя функции", "не добавлять зависимости"),
                acceptance=("счётчик учитывает только положительные элементы",),
            ),
            fixture={"src/count.py": "def count_positives(values):\n    return len(values)\n"},
            expected_files={"src/count.py": "def count_positives(values):\n    return sum(1 for value in values if value > 0)\n"},
            oracle=_count_positives_oracle,
        ),
    )

    t2_cases = (
        BenchmarkCase(
            id="multihunk-safe-divide",
            task=TaskEnvelope(
                id="multihunk-safe-divide",
                goal="добавить безопасное деление с обработкой нуля в хелпере и вызове",
                files=("src/calc.py",),
                context="Обнови safe_div для возврата 0.0 при b=0 и используй safe_div в calculate_ratio.",
                constraints=("не менять сигнатуру calculate_ratio",),
                acceptance=("calculate_ratio(10, 0) == 0.0", "calculate_ratio(10, 2) == 5.0"),
            ),
            fixture={
                "src/calc.py": (
                    "def safe_div(a, b):\n"
                    "    return a / b\n\n"
                    "def calculate_ratio(a, b):\n"
                    "    return a / b\n"
                )
            },
            expected_files={
                "src/calc.py": (
                    "def safe_div(a, b):\n"
                    "    return a / b if b != 0 else 0.0\n\n"
                    "def calculate_ratio(a, b):\n"
                    "    return safe_div(a, b)\n"
                )
            },
            oracle=_multihunk_divide_oracle,
        ),
    )

    t3_cases = (
        BenchmarkCase(
            id="crossfile-signature-update",
            task=TaskEnvelope(
                id="crossfile-signature-update",
                goal="обновить класс User и функцию рендеринга для поддержки age",
                files=("src/models.py", "src/service.py"),
                context="Добавь поле age в User и включи его в строку render_user: 'User: {name}, Age: {age}'.",
                constraints=("обновить оба файла согласованно",),
                acceptance=("render_user('Alice', 30) возвращает 'User: Alice, Age: 30'",),
            ),
            fixture={
                "src/models.py": "class User:\n    def __init__(self, name):\n        self.name = name\n",
                "src/service.py": "from .models import User\n\ndef render_user(name, age=0):\n    u = User(name)\n    return f'User: {u.name}'\n",
            },
            expected_files={
                "src/models.py": "class User:\n    def __init__(self, name, age=0):\n        self.name = name\n        self.age = age\n",
                "src/service.py": "from .models import User\n\ndef render_user(name, age=0):\n    u = User(name, age)\n    return f'User: {u.name}, Age: {u.age}'\n",
            },
            oracle=_crossfile_oracle,
        ),
    )

    t4_cases = (
        BenchmarkCase(
            id="algo-lru-cache",
            task=TaskEnvelope(
                id="algo-lru-cache",
                goal="реализовать алгоритм вытеснения LRU кэша заданной емкости",
                files=("src/lru.py",),
                context="Класс SimpleLRU с методами get(key) и put(key, val), вытесняющий наименее используемый элемент при превышении capacity.",
                constraints=("использовать dict или OrderedDict",),
                acceptance=("get возвращает значение", "старые элементы вытесняются"),
            ),
            fixture={
                "src/lru.py": (
                    "class SimpleLRU:\n"
                    "    def __init__(self, capacity=2):\n"
                    "        self.capacity = capacity\n"
                    "        self.items = {}\n\n"
                    "    def get(self, key):\n"
                    "        return self.items.get(key)\n\n"
                    "    def put(self, key, val):\n"
                    "        self.items[key] = val\n"
                )
            },
            expected_files={
                "src/lru.py": (
                    "class SimpleLRU:\n"
                    "    def __init__(self, capacity=2):\n"
                    "        self.capacity = capacity\n"
                    "        self.items = {}\n\n"
                    "    def get(self, key):\n"
                    "        if key not in self.items:\n"
                    "            return None\n"
                    "        val = self.items.pop(key)\n"
                    "        self.items[key] = val\n"
                    "        return val\n\n"
                    "    def put(self, key, val):\n"
                    "        if key in self.items:\n"
                    "            self.items.pop(key)\n"
                    "        elif len(self.items) >= self.capacity:\n"
                    "            first_key = next(iter(self.items))\n"
                    "            del self.items[first_key]\n"
                    "        self.items[key] = val\n"
                )
            },
            oracle=_algo_lru_oracle,
        ),
    )

    return {
        Tier.SYNTAX_TIER_0: t0_cases,
        Tier.ATOMIC_TIER_1: t1_cases,
        Tier.MULTI_HUNK_TIER_2: t2_cases,
        Tier.CROSS_FILE_TIER_3: t3_cases,
        Tier.ALGORITHMIC_TIER_4: t4_cases,
    }


class CapabilityLadder:
    """Evaluates a model across progressive difficulty tiers with adaptive early exit."""

    def __init__(
        self,
        cases_by_tier: Mapping[Tier, Sequence[BenchmarkCase]] | None = None,
        *,
        threshold: float = 0.60,
    ) -> None:
        self.cases_by_tier = cases_by_tier or ladder_cases()
        self.threshold = threshold

    def evaluate(
        self,
        model_name: str,
        model: ChatModel,
        *,
        max_turns: int = 4,
    ) -> CapabilityVector:
        tested_tiers: dict[int, dict[str, Any]] = {}
        highest_passing_tier = -1
        total_eval_tokens = 0
        total_eval_duration_ns = 0
        total_turns = 0
        total_cases_run = 0
        all_passed_count = 0

        for tier in (
            Tier.SYNTAX_TIER_0,
            Tier.ATOMIC_TIER_1,
            Tier.MULTI_HUNK_TIER_2,
            Tier.CROSS_FILE_TIER_3,
            Tier.ALGORITHMIC_TIER_4,
        ):
            cases = self.cases_by_tier.get(tier, ())
            if not cases:
                continue

            results = [run_case(model, case, max_turns=max_turns) for case in cases]
            correct_count = sum(r.correct for r in results)
            tier_total = len(results)
            tier_score = correct_count / tier_total if tier_total > 0 else 0.0

            total_cases_run += tier_total
            all_passed_count += correct_count
            known = [r.eval_count for r in results]
            total_eval_tokens = (total_eval_tokens + sum(known)
                                 if total_eval_tokens is not None and all(n is not None for n in known)
                                 else None)
            total_eval_duration_ns += sum(r.eval_duration_ns for r in results)
            total_turns += sum(r.model_calls for r in results)

            passed = tier_score >= self.threshold
            tested_tiers[int(tier)] = {
                "status": "passed" if passed else "failed",
                "score_percent": round(tier_score * 100.0, 2),
                "passed_cases": correct_count,
                "total_cases": tier_total,
                "label": TIER_LABELS.get(int(tier), f"Tier {tier}"),
            }

            if passed:
                highest_passing_tier = int(tier)
            else:
                # Adaptive early exit: do not test higher tiers
                break

        overall_tier = max(0, highest_passing_tier) if highest_passing_tier >= 0 else 0
        tier_label = TIER_LABELS.get(overall_tier, "Unrated")
        ci_95 = _wilson_score_interval(all_passed_count, total_cases_run)
        correctness_pct = _percent(all_passed_count, total_cases_run) if total_cases_run > 0 else 0.0

        eval_tps = (
            round(total_eval_tokens * 1e9 / total_eval_duration_ns, 2)
            if total_eval_tokens is not None and total_eval_duration_ns > 0
            else None
        )
        avg_turns = round(total_turns / total_cases_run) if total_cases_run > 0 else max_turns
        granularity = GRANULARITY_BY_TIER.get(overall_tier, "atomic_hunk")

        return CapabilityVector(
            model=model_name,
            overall_tier=overall_tier,
            tier_label=tier_label,
            confidence_95_ci=ci_95,
            correctness_percent=correctness_pct,
            granularity_tolerance=granularity,
            turn_horizon=max(1, avg_turns),
            languages=("python",),
            tps_generation=eval_tps,
            tested_tiers=tested_tiers,
        )


def check_capability_overload(
    task: TaskEnvelope, profile: CapabilityVector | None
) -> tuple[bool, str | None, str | None]:
    """Check if task complexity exceeds verified capability tier bounds."""
    if profile is None:
        return False, None, None

    file_count = len(task.files)
    if file_count > 1 and profile.overall_tier < Tier.CROSS_FILE_TIER_3:
        reason = (
            f"CAPABILITY_OVERLOAD: task targets {file_count} files, exceeding verified model tier "
            f"{profile.overall_tier} ({profile.tier_label}) which supports single-file tasks."
        )
        prescription = (
            "Decompose this multi-file task with `local-agent decompose --task ...`; "
            "that command only creates child envelopes. Run the children through a "
            "sequential quality strategy (`tests` or `plan`) so dependent edits "
            "share accumulated scratch state, or reduce the allowlist files to <= 1 file."
        )
        return True, reason, prescription

    if file_count > 1 and profile.granularity_tolerance != "multi_file":
        reason = (
            f"CAPABILITY_OVERLOAD: model granularity tolerance is '{profile.granularity_tolerance}', "
            f"which does not support multi-file modifications."
        )
        prescription = "Decompose task with `local-agent decompose`."
        return True, reason, prescription

    return False, None, None

