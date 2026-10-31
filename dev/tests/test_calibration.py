import unittest

from local_coding_agent.calibration import (
    calibrate_for_model,
    calibrate_workers,
    model_vram_bytes,
)


class FakeRuntime:
    def __init__(self, loaded=None, tags=None):
        self._loaded = loaded or []
        self._tags = tags or []

    def loaded_models(self):
        return {"models": self._loaded}

    def available_models(self):
        return {"models": self._tags}


class SnapshotRuntime(FakeRuntime):
    def __init__(self):
        super().__init__()
        self.loaded_calls = 0

    def loaded_models(self):
        self.loaded_calls += 1
        if self.loaded_calls == 1:
            return {"models": [{"name": "m", "size_vram": 100}, {"name": "other", "size_vram": 20}]}
        return {"models": [{"name": "m", "size_vram": 900}]}


class CalibrationTests(unittest.TestCase):
    def test_calibrate_workers_charges_model_once_and_context_per_worker(self):
        self.assertEqual(
            calibrate_workers(
                100,
                vram_budget_bytes=500,
                per_worker_context_bytes=100,
                hard_max=4,
            ),
            4,
        )
        self.assertEqual(
            calibrate_workers(
                100,
                vram_budget_bytes=250,
                per_worker_context_bytes=100,
                hard_max=4,
            ),
            2,
        )
        self.assertEqual(
            calibrate_workers(
                100,
                vram_budget_bytes=50,
                per_worker_context_bytes=100,
                hard_max=4,
            ),
            1,
        )

    def test_calibrate_workers_accounts_for_other_loaded_models(self):
        self.assertEqual(
            calibrate_workers(
                100,
                vram_budget_bytes=500,
                per_worker_context_bytes=100,
                other_loaded_vram_bytes=200,
                hard_max=4,
            ),
            3,
        )

    def test_calibrate_workers_honors_hard_max(self):
        self.assertEqual(
            calibrate_workers(
                100,
                vram_budget_bytes=10_000,
                per_worker_context_bytes=100,
                hard_max=8,
            ),
            8,
        )

    def test_calibrate_workers_unknown_footprint_yields_one(self):
        self.assertEqual(calibrate_workers(0, vram_budget_bytes=1000), 1)
        self.assertEqual(calibrate_workers(-5, vram_budget_bytes=1000), 1)
        self.assertEqual(
            calibrate_workers(100, vram_budget_bytes=1000),
            1,
        )

    def test_calibrate_workers_rejects_non_positive_budget(self):
        with self.assertRaises(ValueError):
            calibrate_workers(100, vram_budget_bytes=0)

    def test_model_vram_prefers_loaded_size_vram(self):
        client = FakeRuntime(
            loaded=[{"name": "m", "size_vram": 100}],
            tags=[{"name": "m", "size": 5000}],
        )
        self.assertEqual(model_vram_bytes(client, "m"), (100, "ps.size_vram"))

    def test_model_vram_falls_back_to_tags_size(self):
        client = FakeRuntime(tags=[{"name": "m", "size": 5000}])
        self.assertEqual(model_vram_bytes(client, "m"), (5000, "tags.size_estimate"))

    def test_model_vram_unknown_model(self):
        client = FakeRuntime(tags=[{"name": "other", "size": 5000}])
        self.assertEqual(model_vram_bytes(client, "m"), (0, "unknown"))

    def test_calibrate_for_model_returns_diagnostic(self):
        client = FakeRuntime(loaded=[{"name": "m", "size_vram": 100}])
        report = calibrate_for_model(
            client,
            "m",
            vram_budget_bytes=300,
            per_worker_context_bytes=50,
            hard_max=4,
        )
        self.assertEqual(report["model_vram_bytes"], 100)
        self.assertEqual(report["max_workers"], 4)
        self.assertEqual(report["calibration_basis"], "runtime_model_plus_parallel_context")

    def test_calibrate_for_model_does_not_treat_tags_size_as_runtime_vram(self):
        client = FakeRuntime(tags=[{"name": "m", "size": 5000}])
        report = calibrate_for_model(
            client,
            "m",
            vram_budget_bytes=10_000,
            per_worker_context_bytes=100,
        )
        self.assertEqual(report["max_workers"], 1)
        self.assertEqual(report["calibration_basis"], "runtime_vram_unconfirmed")

    def test_calibrate_for_model_validates_inputs_before_source_shortcuts(self):
        client = FakeRuntime(tags=[{"name": "m", "size": 5000}])
        for kwargs in (
            {"vram_budget_bytes": 0},
            {"vram_budget_bytes": 1000, "hard_max": 0},
            {"vram_budget_bytes": 1000, "per_worker_context_bytes": -1},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                calibrate_for_model(client, "m", **kwargs)

    def test_calibrate_for_model_uses_one_loaded_snapshot(self):
        client = SnapshotRuntime()
        report = calibrate_for_model(
            client,
            "m",
            vram_budget_bytes=300,
            per_worker_context_bytes=50,
            hard_max=4,
        )

        self.assertEqual(client.loaded_calls, 1)
        self.assertEqual(report["model_vram_bytes"], 100)
        self.assertEqual(report["other_loaded_vram_bytes"], 20)


if __name__ == "__main__":
    unittest.main()
