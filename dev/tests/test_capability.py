import json
import unittest
from pathlib import Path

from local_coding_agent.capability import (
    Tier,
    CapabilityVector,
    CapabilityLadder,
    check_capability_overload,
    ladder_cases,
)
from local_coding_agent.task import TaskEnvelope


class FakeModelForLadder:
    def __init__(self, responses_by_task_id):
        self.responses = dict(responses_by_task_id)
        self.requests = []

    def chat(self, messages, *, tools=None):
        self.requests.append(messages)
        # Extract task id from user message if possible
        user_content = messages[1]["content"] if len(messages) > 1 else "{}"
        try:
            task_dict = json.loads(user_content)
            task_id = task_dict.get("id")
        except Exception:
            task_id = "unknown"

        response = self.responses.get(task_id, {
            "message": {
                "role": "assistant",
                "content": json.dumps({"status": "candidate", "summary": "done", "patch": "", "checks": [], "risks": []}),
            }
        })
        return response


class CapabilityTests(unittest.TestCase):
    def test_capability_vector_as_dict(self):
        vec = CapabilityVector(
            model="ling-3.0-tiny-q6k",
            overall_tier=1,
            tier_label="Atomic Pure Functions",
            confidence_95_ci=(75.0, 95.0),
            correctness_percent=85.0,
            granularity_tolerance="function_level",
            turn_horizon=3,
            languages=("python", "javascript"),
            tps_generation=85.0,
            tested_tiers={0: {"status": "passed", "score": 100.0}, 1: {"status": "passed", "score": 85.0}},
            timestamp="2026-08-19T00:00:00Z",
        )
        d = vec.as_dict()
        self.assertEqual(d["model"], "ling-3.0-tiny-q6k")
        self.assertEqual(d["overall_tier"], 1)
        self.assertEqual(d["tier_label"], "Atomic Pure Functions")
        self.assertEqual(d["granularity_tolerance"], "function_level")
        self.assertIn("python", d["languages"])
        self.assertEqual(d["tps_generation"], 85.0)

    def test_ladder_cases_covers_all_tiers(self):
        cases = ladder_cases()
        self.assertIn(Tier.SYNTAX_TIER_0, cases)
        self.assertIn(Tier.ATOMIC_TIER_1, cases)
        self.assertIn(Tier.MULTI_HUNK_TIER_2, cases)
        self.assertIn(Tier.CROSS_FILE_TIER_3, cases)
        self.assertIn(Tier.ALGORITHMIC_TIER_4, cases)
        self.assertTrue(len(cases[Tier.SYNTAX_TIER_0]) >= 2)
        self.assertTrue(len(cases[Tier.ATOMIC_TIER_1]) >= 2)

    def test_capability_ladder_evaluates_and_exits_early_on_failure(self):
        import difflib

        def make_patch(filename, old, new):
            return "".join(
                difflib.unified_diff(
                    old.splitlines(True),
                    new.splitlines(True),
                    fromfile=f"a/{filename}",
                    tofile=f"b/{filename}",
                )
            )

        tier_cases = ladder_cases()
        t0_case1 = tier_cases[Tier.SYNTAX_TIER_0][0]
        t0_case2 = tier_cases[Tier.SYNTAX_TIER_0][1]

        p1 = make_patch("src/syntax_add.py", t0_case1.fixture["src/syntax_add.py"], t0_case1.expected_files["src/syntax_add.py"])
        p2 = make_patch("src/square.py", t0_case2.fixture["src/square.py"], t0_case2.expected_files["src/square.py"])

        # Model that solves both Tier 0 syntax cases but fails Tier 1
        responses = {
            t0_case1.id: {
                "message": {
                    "role": "assistant",
                    "content": json.dumps({
                        "status": "candidate",
                        "summary": "fixed colon",
                        "patch": p1,
                        "checks": [],
                        "risks": [],
                    }),
                }
            },
            t0_case2.id: {
                "message": {
                    "role": "assistant",
                    "content": json.dumps({
                        "status": "candidate",
                        "summary": "fixed typo",
                        "patch": p2,
                        "checks": [],
                        "risks": [],
                    }),
                }
            },
        }
        model = FakeModelForLadder(responses)
        ladder = CapabilityLadder(cases_by_tier=tier_cases, threshold=0.6)
        vector = ladder.evaluate("test-model", model, max_turns=2)

        self.assertEqual(vector.overall_tier, Tier.SYNTAX_TIER_0)
        self.assertIn(0, vector.tested_tiers)
        self.assertIn(1, vector.tested_tiers)
        self.assertEqual(vector.tested_tiers[0]["status"], "passed")
        self.assertEqual(vector.tested_tiers[1]["status"], "failed")
        # Early exit: Tiers 2, 3, 4 should not have been tested
        self.assertNotIn(2, vector.tested_tiers)
        self.assertNotIn(3, vector.tested_tiers)



    def test_check_capability_overload_blocks_multifile_for_tier1(self):
        profile = CapabilityVector(
            model="tiny-model",
            overall_tier=1,
            tier_label="Atomic Pure Functions",
            confidence_95_ci=(60.0, 90.0),
            correctness_percent=75.0,
            granularity_tolerance="function_level",
            turn_horizon=3,
            languages=("python",),
            tps_generation=80.0,
            tested_tiers={},
            timestamp="",
        )
        task = TaskEnvelope(
            id="multi-file-task",
            goal="изменить два файла",
            files=("src/a.py", "src/b.py"),
        )
        overloaded, reason, prescription = check_capability_overload(task, profile)
        self.assertTrue(overloaded)
        self.assertIn("CAPABILITY_OVERLOAD", reason)
        self.assertIn("decompose", prescription.lower())
        self.assertIn("sequential", prescription.lower())
        self.assertIn("scratch", prescription.lower())

    def test_check_capability_overload_allows_single_file_task(self):
        profile = CapabilityVector(
            model="tiny-model",
            overall_tier=1,
            tier_label="Atomic Pure Functions",
            confidence_95_ci=(60.0, 90.0),
            correctness_percent=75.0,
            granularity_tolerance="function_level",
            turn_horizon=3,
            languages=("python",),
            tps_generation=80.0,
            tested_tiers={},
            timestamp="",
        )
        task = TaskEnvelope(
            id="single-file-task",
            goal="изменить один файл",
            files=("src/a.py",),
        )
        overloaded, reason, prescription = check_capability_overload(task, profile)
        self.assertFalse(overloaded)
        self.assertIsNone(reason)
        self.assertIsNone(prescription)


if __name__ == "__main__":
    unittest.main()

