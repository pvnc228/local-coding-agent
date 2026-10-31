import unittest

from local_coding_agent.memory import MemoryBudgetError, MemoryOperationError, ModelMemoryManager


class FakeMemoryClient:
    def __init__(self, models):
        self.models = {model["name"]: dict(model) for model in models}
        self.unloaded = []

    def loaded_models(self):
        return {"models": list(self.models.values())}

    def unload_model(self, model=None):
        target = model or ""
        self.unloaded.append(target)
        self.models.pop(target, None)
        return {"done": True, "model": target}


class MemoryManagerTests(unittest.TestCase):
    def test_snapshot_reports_total_vram_and_model_details(self):
        client = FakeMemoryClient(
            [
                {"name": "small", "size_vram": 100, "size": 200, "expires_at": "later"},
                {"name": "large", "size_vram": 300, "size": 500, "expires_at": "later"},
            ]
        )

        snapshot = ModelMemoryManager(client).snapshot()

        self.assertEqual(snapshot.total_vram_bytes, 400)
        self.assertEqual([model.name for model in snapshot.models], ["small", "large"])
        self.assertEqual(snapshot.as_dict()["total_vram_bytes"], 400)

    def test_enforce_limit_unloads_largest_unprotected_models(self):
        client = FakeMemoryClient(
            [
                {"name": "small", "size_vram": 100},
                {"name": "large", "size_vram": 300},
            ]
        )

        snapshot = ModelMemoryManager(client).enforce_limit(150, keep=("small",))

        self.assertEqual(client.unloaded, ["large"])
        self.assertEqual(snapshot.total_vram_bytes, 100)
        self.assertEqual([model.name for model in snapshot.models], ["small"])

    def test_enforce_limit_fails_when_protected_models_exceed_budget(self):
        client = FakeMemoryClient([{"name": "large", "size_vram": 300}])

        with self.assertRaisesRegex(MemoryBudgetError, "protected"):
            ModelMemoryManager(client).enforce_limit(150, keep=("large",))

    def test_enforce_limit_handles_async_unload_without_spinning(self):
        # A client whose unload_model does not immediately change the snapshot
        # (async/stale unload) must not make the loop spin on zero progress:
        # every candidate is attempted exactly once, then MemoryBudgetError is
        # raised because the budget still cannot be met.
        class StaleUnloadClient(FakeMemoryClient):
            def unload_model(self, model=None):
                self.unloaded.append(model or "")
                return {"done": True, "model": model}  # deliberately no-op on state

        client = StaleUnloadClient(
            [
                {"name": "small", "size_vram": 100},
                {"name": "large", "size_vram": 300},
                {"name": "mid", "size_vram": 200},
            ]
        )

        # No candidate is protected; unload does nothing visible to the
        # snapshot, so every candidate is attempted exactly once (no infinite
        # spin), the budget is never met, and MemoryBudgetError is raised.
        with self.assertRaisesRegex(MemoryBudgetError, "still use"):
            ModelMemoryManager(client).enforce_limit(100)

        self.assertEqual(client.unloaded, ["large", "mid", "small"])

    def test_unload_all_releases_every_loaded_model(self):
        client = FakeMemoryClient(
            [{"name": "one", "size_vram": 100}, {"name": "two", "size_vram": 200}]
        )

        snapshot = ModelMemoryManager(client).unload_all()

        self.assertEqual(set(client.unloaded), {"one", "two"})
        self.assertEqual(snapshot.total_vram_bytes, 0)

    def test_snapshot_handles_unsupported_memory_client_gracefully(self):
        from local_coding_agent.ollama_adapter import OllamaError

        class UnsupportedMemoryClient:
            def loaded_models(self):
                raise OllamaError("unsupported", kind="unsupported")

            def unload_model(self, model=None):
                raise OllamaError("unsupported", kind="unsupported")

        client = UnsupportedMemoryClient()
        snapshot = ModelMemoryManager(client).snapshot()
        self.assertFalse(snapshot.is_supported)
        self.assertEqual(snapshot.total_vram_bytes, 0)
        self.assertEqual(snapshot.models, ())
        self.assertEqual(snapshot.as_dict()["supported"], False)

    def test_operations_reject_unsupported_or_partial_snapshot(self):
        class PartialClient:
            def loaded_models(self):
                return {"status": "ok"}

            def unload_model(self, model=None):
                raise AssertionError("must not unload without verified state")

        manager = ModelMemoryManager(PartialClient())
        with self.assertRaisesRegex(MemoryOperationError, "unavailable"):
            manager.unload_all()
        with self.assertRaisesRegex(MemoryOperationError, "unavailable"):
            manager.enforce_limit(0)

    def test_unload_model_requires_model_in_verified_snapshot(self):
        client = FakeMemoryClient([{"name": "loaded", "size_vram": 100}])
        with self.assertRaisesRegex(MemoryOperationError, "not present"):
            ModelMemoryManager(client).unload_model("missing")
        self.assertEqual(client.unloaded, [])


if __name__ == "__main__":
    unittest.main()

