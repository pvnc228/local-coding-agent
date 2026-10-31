import difflib
import json
import tempfile
import unittest
from pathlib import Path
from pathlib import PurePosixPath
from unittest.mock import patch

from local_coding_agent.benchmark import (
    BenchmarkCase,
    InstrumentedModel,
    default_cases,
    hard_cases,
    run_case,
    summarize_results,
)
from local_coding_agent.task import TaskEnvelope


class FakeBenchmarkModel:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    def chat(self, messages, *, tools=None):
        self.calls += 1
        return self.response


def test_instrumentation_counts_failed_backend_call_without_invented_tokens():
    class Broken:
        def chat(self, messages, **kwargs):
            raise RuntimeError('backend unavailable')
    model = InstrumentedModel(Broken())
    try:
        model.chat([], tools=[])
    except RuntimeError:
        pass
    assert model.model_calls == 1
    assert model.eval_count is None


def test_support_paths_reject_windows_escapes_on_posix_and_alias_overlap():
    case = default_cases()[0]
    for support in ({'C:\\outside.py': ''}, {'..\\outside.py': ''},
                    {'./' + next(iter(case.fixture)): ''}):
        with patch('local_coding_agent.benchmark._cases.Path', PurePosixPath):
            try:
                BenchmarkCase(id='unsafe', task=case.task, fixture=case.fixture,
                              expected_files=case.expected_files, support_files=support)
            except ValueError:
                continue
            raise AssertionError('support path escape or alias overlap was accepted')


class SequenceBenchmarkModel:
    def __init__(self, responses):
        self.responses = list(responses)

    def chat(self, messages, *, tools=None):
        return self.responses.pop(0)


def restricted_process_oracle(workspace):
    read = _load_function(workspace / "src/value.py", "read")
    return read() == "restricted", "oracle process allowed an unsafe import"


class BenchmarkTests(unittest.TestCase):
    def test_default_cases_are_comparable_and_have_unique_ids(self):
        cases = default_cases()

        self.assertEqual(len(cases), 20)
        self.assertEqual(len({case.id for case in cases}), len(cases))
        for case in cases:
            self.assertIsInstance(case, BenchmarkCase)
            self.assertEqual(set(case.task.files), set(case.fixture))
            self.assertTrue(case.expected_files)

    def test_support_files_must_not_overlap_task_allowlist(self):
        with self.assertRaisesRegex(ValueError, "support files must be outside task allowlist"):
            BenchmarkCase(
                id="support-overlap",
                task=TaskEnvelope(
                    id="support-overlap",
                    goal="проверить границу support-файлов",
                    files=("src/value.py",),
                ),
                fixture={"src/value.py": "VALUE = 1\n"},
                support_files={"src/value.py": "не тест"},
                expected_files={"src/value.py": "VALUE = 2\n"},
            )

    def test_support_files_are_materialized_and_allowlisted_checks_run_after_apply(self):
        check = "python -c \"from pathlib import Path; assert Path('tests/support_marker.txt').read_text() == 'support'\""
        case = BenchmarkCase(
            id="support-materialized",
            task=TaskEnvelope(
                id="support-materialized",
                goal="заменить значение",
                files=("src/value.py",),
                checks=(check,),
            ),
            fixture={"src/value.py": "VALUE = 1\n"},
            support_files={"tests/support_marker.txt": "support"},
            expected_files={"src/value.py": "VALUE = 2\n"},
        )
        patch = (
            "diff --git a/src/value.py b/src/value.py\n"
            "--- a/src/value.py\n"
            "+++ b/src/value.py\n"
            "@@ -1 +1 @@\n"
            "-VALUE = 1\n"
            "+VALUE = 2\n"
        )
        model = FakeBenchmarkModel({
            "message": {
                "role": "assistant",
                "content": json.dumps({
                    "status": "candidate",
                    "summary": "значение заменено",
                    "patch": patch,
                    "checks": [],
                    "risks": [],
                }),
            }
        })

        result = run_case(model, case)

        self.assertTrue(result.correct, result.patch_error)

    def test_support_file_patch_is_rejected_by_scope(self):
        case = BenchmarkCase(
            id="support-scope",
            task=TaskEnvelope(
                id="support-scope",
                goal="заменить значение",
                files=("src/value.py",),
            ),
            fixture={"src/value.py": "VALUE = 1\n"},
            support_files={"tests/check.py": "assert True\n"},
            expected_files={"src/value.py": "VALUE = 2\n"},
        )
        patch = (
            "diff --git a/tests/check.py b/tests/check.py\n"
            "--- a/tests/check.py\n"
            "+++ b/tests/check.py\n"
            "@@ -1 +1 @@\n"
            "-assert True\n"
            "+assert False\n"
        )
        result = run_case(
            SequenceBenchmarkModel([
                {
                    "message": {
                        "role": "assistant",
                        "tool_calls": [{
                            "function": {
                                "name": "propose_patch",
                                "arguments": {"patch": patch},
                            }
                        }],
                    }
                },
                {
                    "message": {
                        "role": "assistant",
                        "content": json.dumps({
                            "status": "candidate",
                            "summary": "тест изменён",
                            "patch": "",
                            "checks": [],
                            "risks": [],
                        }),
                    }
                },
            ]),
            case,
        )

        self.assertFalse(result.correct)
        self.assertIn("allowlist", result.patch_error)

    def test_hard_cases_are_separate_and_have_external_checks_and_support_files(self):
        cases = hard_cases()

        self.assertGreaterEqual(len(cases), 15)
        self.assertLessEqual(len(cases), 25)
        self.assertEqual(len({case.id for case in cases}), len(cases))
        self.assertTrue(any(len(case.task.files) > 1 for case in cases))
        for case in cases:
            self.assertTrue(case.task.checks, case.id)
            self.assertTrue(case.support_files, case.id)
            self.assertTrue(set(case.support_files).isdisjoint(case.task.files), case.id)
            self.assertIsNotNone(case.oracle, case.id)

    def test_hard_cases_run_through_checks_and_independent_oracles_with_mock_model(self):
        for case in hard_cases():
            patch_parts = []
            for path, expected in case.expected_files.items():
                old = case.fixture[path].splitlines(keepends=True)
                new = expected.splitlines(keepends=True)
                if old == new:
                    continue
                patch_parts.append(
                    f"diff --git a/{path} b/{path}\n"
                    + "".join(difflib.unified_diff(
                        old,
                        new,
                        fromfile=f"a/{path}",
                        tofile=f"b/{path}",
                        n=3,
                    ))
                )
            model = FakeBenchmarkModel({
                "message": {
                    "role": "assistant",
                    "content": json.dumps({
                        "status": "candidate",
                        "summary": "mock patch",
                        "patch": "".join(patch_parts),
                        "checks": [],
                        "risks": [],
                    }),
                }
            })

            result = run_case(model, case)

            self.assertTrue(result.correct, f"{case.id}: {result.patch_error}")
            self.assertEqual(result.model_calls, 1, case.id)

    def test_run_benchmark_exposes_suite_quality_controls_and_metrics(self):
        case = BenchmarkCase(
            id="quality-controls",
            task=TaskEnvelope(
                id="quality-controls",
                goal="заменить значение",
                files=("src/value.py",),
            ),
            fixture={"src/value.py": "VALUE = 1\n"},
            expected_files={"src/value.py": "VALUE = 2\n"},
        )
        patch = (
            "diff --git a/src/value.py b/src/value.py\n"
            "--- a/src/value.py\n"
            "+++ b/src/value.py\n"
            "@@ -1 +1 @@\n"
            "-VALUE = 1\n"
            "+VALUE = 2\n"
        )
        from local_coding_agent.benchmark import run_benchmark

        artifact = run_benchmark(
            "fake",
            FakeBenchmarkModel({
                "message": {
                    "role": "assistant",
                    "content": json.dumps({
                        "status": "candidate",
                        "summary": "значение заменено",
                        "patch": patch,
                        "checks": [],
                        "risks": [],
                    }),
                }
            }),
            cases=(case,),
            suite="hard",
            samples=2,
            strategy="single",
            max_model_calls=4,
        )

        self.assertEqual(artifact["suite"], "hard")
        self.assertEqual(artifact["samples"], 2)
        self.assertEqual(artifact["summary"]["model_calls"], 2)
        self.assertIsNone(artifact["summary"]["eval_count"])
        self.assertIn("wall_time_ms", artifact["summary"])

    def test_run_case_model_factory_uses_distinct_sample_clients(self):
        case = BenchmarkCase(
            id="sample-factory",
            task=TaskEnvelope(
                id="sample-factory",
                goal="заменить значение",
                files=("src/value.py",),
            ),
            fixture={"src/value.py": "VALUE = 1\n"},
            expected_files={"src/value.py": "VALUE = 2\n"},
        )
        patch = (
            "diff --git a/src/value.py b/src/value.py\n"
            "--- a/src/value.py\n"
            "+++ b/src/value.py\n"
            "@@ -1 +1 @@\n"
            "-VALUE = 1\n"
            "+VALUE = 2\n"
        )
        created: list[int] = []

        def factory(index: int):
            created.append(index)
            return FakeBenchmarkModel({
                "message": {
                    "role": "assistant",
                    "content": json.dumps({
                        "status": "candidate",
                        "summary": "значение заменено",
                        "patch": patch,
                        "checks": [],
                        "risks": [],
                    }),
                }
            })

        result = run_case(
            FakeBenchmarkModel({}),
            case,
            samples=2,
            model_factory=factory,
        )

        self.assertEqual(created, [0, 1])
        self.assertEqual(result.model_calls, 2)
        self.assertTrue(result.correct)

    def test_run_case_applies_candidate_only_in_isolated_fixture_and_scores_metrics(self):
        case = BenchmarkCase(
            id="replace-value",
            task=TaskEnvelope(
                id="replace-value",
                goal="заменить значение",
                files=("src/value.py",),
            ),
            fixture={"src/value.py": "VALUE = 1\n"},
            expected_files={"src/value.py": "VALUE = 2\n"},
        )
        model = FakeBenchmarkModel(
            {
                "total_duration": 2_000_000,
                "load_duration": 500_000,
                "prompt_eval_count": 20,
                "prompt_eval_duration": 700_000,
                "eval_count": 8,
                "eval_duration": 800_000,
                "message": {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "status": "candidate",
                            "summary": "значение заменено",
                            "patch": (
                                "diff --git a/src/value.py b/src/value.py\n"
                                "--- a/src/value.py\n"
                                "+++ b/src/value.py\n"
                                "@@ -1 +1 @@\n"
                                "-VALUE = 1\n"
                                "+VALUE = 2\n"
                            ),
                            "checks": [],
                            "risks": [],
                        },
                        ensure_ascii=False,
                    ),
                },
            }
        )

        result = run_case(model, case)

        self.assertTrue(result.correct)
        self.assertTrue(result.loop_reliable)
        self.assertEqual(result.model_calls, 1)
        self.assertEqual(result.eval_count, 8)
        self.assertEqual(result.prompt_tokens, 20)
        self.assertGreaterEqual(result.wall_time_ms, 0)
        self.assertEqual(Path("src/value.py").exists(), False)

    def test_instrumented_model_preserves_response_and_accumulates_ollama_metrics(self):
        response = {
            "total_duration": 10,
            "prompt_eval_count": 3,
            "eval_count": 4,
            "message": {"role": "assistant", "content": "{}"},
        }
        model = InstrumentedModel(FakeBenchmarkModel(response))

        self.assertIs(model.chat([], tools=[]), response)
        self.assertEqual(model.model_calls, 1)
        self.assertEqual(model.prompt_tokens, 3)
        self.assertEqual(model.eval_count, 4)
        self.assertEqual(model.total_duration_ns, 10)

    def test_instrumented_model_keeps_missing_eval_count_unknown(self):
        model = InstrumentedModel(
            FakeBenchmarkModel({"message": {"role": "assistant", "content": "{}"}})
        )

        model.chat([], tools=[])

        self.assertIsNone(model.eval_count)

    def test_instrumented_model_keeps_compatible_content_tool_patch(self):
        patch = (
            "diff --git a/src/value.py b/src/value.py\n"
            "--- a/src/value.py\n"
            "+++ b/src/value.py\n"
            "@@ -1 +1 @@\n"
            "-VALUE = 1\n"
            "+VALUE = 2\n"
        )
        response = {
            "message": {
                "role": "assistant",
                "content": json.dumps(
                    {"name": "propose_patch", "arguments": {"patch": patch}}
                ),
            }
        }
        model = InstrumentedModel(FakeBenchmarkModel(response))

        model.chat([], tools=[])

        self.assertEqual(model.proposed_patches, [patch])

    def test_benchmark_oracle_runs_in_restricted_child_process(self):
        case = BenchmarkCase(
            id="restricted-oracle",
            task=TaskEnvelope(
                id="restricted-oracle",
                goal="проверить ограничение oracle",
                files=("src/value.py",),
            ),
            fixture={"src/value.py": "def read():\n    return 'original'\n"},
            expected_files={
                "src/value.py": (
                    "def read():\n"
                    "    try:\n"
                    "        import os\n"
                    "        return 'unsafe'\n"
                    "    except ImportError:\n"
                    "        return 'restricted'\n"
                )
            },
            oracle=restricted_process_oracle,
        )
        patch = (
            "diff --git a/src/value.py b/src/value.py\n"
            "--- a/src/value.py\n"
            "+++ b/src/value.py\n"
            "@@ -1,2 +1,6 @@\n"
            " def read():\n"
            "-    return 'original'\n"
            "+    try:\n"
            "+        import os\n"
            "+        return 'unsafe'\n"
            "+    except ImportError:\n"
            "+        return 'restricted'\n"
        )
        model = FakeBenchmarkModel(
            {
                "message": {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "status": "candidate",
                            "summary": "ограничение oracle проверено",
                            "patch": patch,
                            "checks": [],
                            "risks": [],
                        }
                    ),
                }
            }
        )

        result = run_case(model, case)

        self.assertTrue(result.correct, result.patch_error)

    def test_benchmark_uses_content_tool_patch_as_fallback(self):
        patch = (
            "diff --git a/src/value.py b/src/value.py\n"
            "--- a/src/value.py\n"
            "+++ b/src/value.py\n"
            "@@ -1 +1 @@\n"
            "-VALUE = 1\n"
            "+VALUE = 2\n"
        )
        model = SequenceBenchmarkModel(
            [
                {
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(
                            {"name": "propose_patch", "arguments": {"patch": patch}}
                        ),
                    }
                },
                {
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(
                            {
                                "status": "candidate",
                                "summary": "proposal завершён",
                                "patch": "",
                                "checks": [],
                                "risks": [],
                            }
                        ),
                    }
                },
            ]
        )
        case = BenchmarkCase(
            id="content-tool-fallback",
            task=TaskEnvelope(
                id="content-tool-fallback",
                goal="заменить значение",
                files=("src/value.py",),
            ),
            fixture={"src/value.py": "VALUE = 1\n"},
            expected_files={"src/value.py": "VALUE = 2\n"},
        )

        result = run_case(model, case)

        self.assertTrue(result.correct)
        self.assertEqual(result.patch_source, "accepted_result")

    def test_run_case_scores_search_replace_edits_proposal(self):
        case = BenchmarkCase(
            id="replace-value-edits",
            task=TaskEnvelope(
                id="replace-value-edits",
                goal="заменить значение",
                files=("src/value.py",),
            ),
            fixture={"src/value.py": "VALUE = 1\n"},
            expected_files={"src/value.py": "VALUE = 2\n"},
        )
        model = FakeBenchmarkModel(
            {
                "message": {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "status": "candidate",
                            "summary": "значение заменено",
                            "edits": [
                                {"file": "src/value.py", "search": "VALUE = 1", "replace": "VALUE = 2"}
                            ],
                            "checks": [],
                            "risks": [],
                        },
                        ensure_ascii=False,
                    ),
                },
            }
        )

        result = run_case(model, case)

        self.assertTrue(result.correct, result.patch_error)
        self.assertTrue(result.loop_reliable)
        self.assertEqual(result.model_calls, 1)

    def test_run_case_failed_tool_proposal_reports_validation_without_name_error(self):
        case = BenchmarkCase(
            id="invalid-fallback-proposal",
            task=TaskEnvelope(id="invalid-fallback-proposal", goal="заменить значение", files=("src/value.py",)),
            fixture={"src/value.py": "VALUE = 1\n"},
            expected_files={"src/value.py": "VALUE = 2\n"},
        )
        model = SequenceBenchmarkModel(
            [
                {
                    "message": {
                        "role": "assistant",
                        "tool_calls": [{
                            "function": {
                                "name": "propose_patch",
                                "arguments": {"patch": "not a unified diff"},
                            }
                        }],
                    }
                },
                {"message": {"role": "assistant", "content": "not json"}},
            ]
        )

        result = run_case(model, case)

        self.assertFalse(result.correct)
        self.assertEqual(result.patch_source, "tool_proposal")
        self.assertIn("patch is not a unified diff", result.patch_error)

    def test_summarize_results_reports_correctness_and_reliability_rates(self):
        cases = default_cases()
        model = FakeBenchmarkModel(
            {
                "message": {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "status": "candidate",
                            "summary": "нет изменений",
                            "patch": "",
                            "checks": [],
                            "risks": [],
                        }
                    ),
                }
            }
        )
        results = [run_case(model, case) for case in cases[:2]]

    def test_run_case_records_tokens_per_second_metrics(self):
        case = default_cases()[0]
        model = FakeBenchmarkModel(
            {
                "total_duration": 1_000_000_000,
                "load_duration": 100_000_000,
                "prompt_eval_count": 200,
                "prompt_eval_duration": 500_000_000,  # 0.5s -> 400 tok/s
                "eval_count": 50,
                "eval_duration": 250_000_000,  # 0.25s -> 200 tok/s
                "message": {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "status": "candidate",
                            "summary": "ok",
                            "patch": "",
                            "checks": [],
                            "risks": [],
                        }
                    ),
                },
            }
        )
        result = run_case(model, case)
        self.assertEqual(result.prompt_tokens, 200)
        self.assertEqual(result.eval_count, 50)
        d = result.as_dict()
        self.assertIn("eval_tps", d)
        self.assertIn("prompt_eval_tps", d)
        self.assertAlmostEqual(d["prompt_eval_tps"], 400.0, places=1)
        self.assertAlmostEqual(d["eval_tps"], 200.0, places=1)

    def test_summarize_results_includes_tps_error_categories_and_confidence_intervals(self):
        cases = default_cases()
        model = FakeBenchmarkModel(
            {
                "prompt_eval_count": 100,
                "prompt_eval_duration": 1_000_000_000,  # 1.0s -> 100 tok/s
                "eval_count": 50,
                "eval_duration": 2_000_000_000,  # 2.0s -> 25 tok/s
                "message": {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "status": "candidate",
                            "summary": "нет изменений",
                            "patch": "",
                            "checks": [],
                            "risks": [],
                        }
                    ),
                },
            }
        )
        results = [run_case(model, case) for case in cases[:4]]
        summary = summarize_results("fake", results)

        self.assertIn("eval_tokens_per_second", summary)
        self.assertIn("eval_count", summary)
        self.assertIn("wall_time_ms", summary)
        self.assertIn("prompt_tokens_per_second", summary)
        self.assertIn("error_categories", summary)
        self.assertIn("failure_taxonomy", summary)
        self.assertIn("correctness_ci_95", summary)
        self.assertIn("tool_loop_reliability_ci_95", summary)
        self.assertIn("patch_apply_ci_95", summary)
        self.assertEqual(len(summary["correctness_ci_95"]), 2)
        self.assertIsInstance(summary["error_categories"], dict)
        self.assertIsInstance(summary["failure_taxonomy"], dict)

    def test_categorize_failure_taxonomy_separates_friction_from_capability(self):
        from local_coding_agent.benchmark import BenchmarkCaseResult, categorize_failure_taxonomy

        results = [
            BenchmarkCaseResult(
                case_id="c1",
                status="rejected",
                correct=False,
                loop_reliable=False,
                model_calls=1,
                tool_calls=0,
                patch_applied=False,
                validation_valid=False,
                wall_time_ms=10.0,
                eval_count=10,
                prompt_tokens=10,
                total_duration_ns=100,
                load_duration_ns=0,
                prompt_eval_duration_ns=50,
                eval_duration_ns=50,
                patch_source="none",
                patch_error="search block not found in src/a.py",
                result={},
            ),
            BenchmarkCaseResult(
                case_id="c2",
                status="accepted",
                correct=False,
                loop_reliable=True,
                model_calls=1,
                tool_calls=0,
                patch_applied=True,
                validation_valid=True,
                wall_time_ms=10.0,
                eval_count=10,
                prompt_tokens=10,
                total_duration_ns=100,
                load_duration_ns=0,
                prompt_eval_duration_ns=50,
                eval_duration_ns=50,
                patch_source="accepted_result",
                patch_error="oracle mismatch: VALUE was 3 instead of 2",
                result={},
            ),
            BenchmarkCaseResult(
                case_id="c3",
                status="rejected",
                correct=False,
                loop_reliable=False,
                model_calls=1,
                tool_calls=0,
                patch_applied=False,
                validation_valid=False,
                wall_time_ms=10.0,
                eval_count=10,
                prompt_tokens=10,
                total_duration_ns=100,
                load_duration_ns=0,
                prompt_eval_duration_ns=50,
                eval_duration_ns=50,
                patch_source="none",
                patch_error="search is not line-aligned",
                result={"error": {"kind": "invalid_json"}},
            ),
        ]

        taxonomy = categorize_failure_taxonomy(results)
        self.assertEqual(taxonomy["total_failures"], 3)
        self.assertEqual(taxonomy["contract_friction_count"], 2)
        self.assertEqual(taxonomy["capability_failure_count"], 1)
        self.assertAlmostEqual(taxonomy["friction_ratio"], 0.67, places=2)
        self.assertAlmostEqual(taxonomy["capability_ratio"], 0.33, places=2)
        self.assertIn("search_not_found", taxonomy["contract_friction"])
        self.assertIn("search_not_line_aligned", taxonomy["contract_friction"])
        self.assertIn("oracle_mismatch", taxonomy["capability_failures"])


if __name__ == "__main__":
    unittest.main()

