"""Tests for the interactive smoke test module."""

import unittest
from unittest.mock import MagicMock, patch

from local_coding_agent.smoke import run_smoke_test


class TestSmoke(unittest.TestCase):
    def test_run_smoke_mock_success(self):
        result = run_smoke_test(use_mock=True, verbose=False)
        self.assertTrue(result["success"])
        self.assertEqual(result["status"], "accepted")
        self.assertIn("steps", result)
        self.assertTrue(all(step["status"] == "ok" for step in result["steps"]))
        self.assertTrue(result["tps"] > 0)

    def test_run_smoke_real_client_fallback_to_mock_when_no_ollama(self):
        with patch("local_coding_agent.smoke.build_client") as mock_client_factory:
            instance = MagicMock()
            instance.available_models.side_effect = Exception("Ollama offline")
            mock_client_factory.return_value = instance

            result = run_smoke_test(use_mock=False, fallback_to_mock=True, verbose=False)
            self.assertTrue(result["success"])
            self.assertTrue(result.get("mock_fallback", False))

    def test_run_smoke_live_failure_is_not_reported_as_success_by_default(self):
        with patch("local_coding_agent.smoke.build_client") as mock_client_factory:
            instance = MagicMock()
            instance.available_models.side_effect = Exception("Ollama offline")
            mock_client_factory.return_value = instance

            result = run_smoke_test(use_mock=False, verbose=False)

        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "backend_unavailable")
        self.assertFalse(result["backend_verified"])
        self.assertFalse(result["mock_fallback"])

    def test_run_smoke_handles_controller_failure(self):
        with patch("local_coding_agent.smoke.Controller") as mock_ctrl_cls:
            mock_ctrl = MagicMock()
            mock_ctrl.run.return_value = {"status": "failed", "audit": []}
            mock_ctrl_cls.return_value = mock_ctrl

            result = run_smoke_test(use_mock=True, verbose=False)
            self.assertFalse(result["success"])
            self.assertEqual(result["status"], "failed")
            self.assertTrue(any(step["status"] == "fail" for step in result["steps"]))


if __name__ == "__main__":
    unittest.main()
