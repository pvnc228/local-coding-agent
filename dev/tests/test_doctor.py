"""Tests for the doctor diagnostic wizard."""

import unittest
from unittest.mock import MagicMock, patch

from local_coding_agent.doctor import (
    CheckResult,
    DoctorReport,
    check_git_installed,
    check_host_memory,
    check_ollama_api,
    diagnose_environment,
    recommend_models,
)


class TestDoctor(unittest.TestCase):
    def test_check_result_properties(self):
        ok_res = CheckResult(name="Test", status="ok", message="All good")
        self.assertTrue(ok_res.is_ok)
        self.assertFalse(ok_res.is_fail)

        fail_res = CheckResult(name="Test", status="fail", message="Error occurred")
        self.assertFalse(fail_res.is_ok)
        self.assertTrue(fail_res.is_fail)

    def test_check_git_installed_success(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="git version 2.43.0\n")
            res = check_git_installed()
            self.assertEqual(res.status, "ok")
            self.assertIn("2.43.0", res.message)

    def test_check_git_installed_failure(self):
        with patch("subprocess.run", side_effect=FileNotFoundError("git not found")):
            res = check_git_installed()
            self.assertEqual(res.status, "fail")
            self.assertIn("not found", res.message.lower())

    def test_check_ollama_api_success(self):
        mock_client = MagicMock()
        mock_client.available_models.return_value = {
            "models": [
                {"name": "qwen2.5-coder:latest", "size": 4500000000},
                {"name": "qwen3-8b-q6k:latest", "size": 8000000000},
            ]
        }
        mock_client.endpoint = "http://127.0.0.1:11434"
        with patch("local_coding_agent.doctor.OllamaClient", return_value=mock_client):
            res, models = check_ollama_api("http://127.0.0.1:11434")
            self.assertEqual(res.status, "ok")
            self.assertEqual(len(models), 2)
            self.assertIn("qwen2.5-coder:latest", models)

    def test_check_ollama_api_failure(self):
        mock_client = MagicMock()
        mock_client.available_models.side_effect = Exception("Connection refused")
        mock_client.endpoint = "http://127.0.0.1:11434"
        with patch("local_coding_agent.doctor.OllamaClient", return_value=mock_client):
            res, models = check_ollama_api("http://127.0.0.1:11434")
            self.assertEqual(res.status, "fail")
            self.assertEqual(models, [])

    def test_recommend_models(self):
        installed = ["qwen2.5-coder:latest"]
        recs = recommend_models(installed)
        self.assertTrue(len(recs["installed"]) >= 1)
        self.assertTrue(len(recs["missing"]) >= 1)
        self.assertTrue(any("qwen2.5-coder" in m["name"] for m in recs["installed"]))

    def test_recommend_models_does_not_match_similar_tag(self):
        recs = recommend_models(["qwen3-0.6b:latest"])
        assert not any(item["name"] == "qwen3-8b-q6k" for item in recs["recommended_installed"])

    def test_check_host_memory(self):
        res = check_host_memory()
        self.assertIn(res.status, ("ok", "warn"))
        self.assertIn("RAM", res.message)

    def test_diagnose_environment_aggregate(self):
        with patch("local_coding_agent.doctor.check_git_installed") as mock_git, \
             patch("local_coding_agent.doctor.check_ollama_api") as mock_ollama, \
             patch("local_coding_agent.doctor.check_host_memory") as mock_mem:
            mock_git.return_value = CheckResult(name="Git", status="ok", message="git 2.40")
            mock_ollama.return_value = (
                CheckResult(name="Ollama API", status="ok", message="Connected"),
                ["qwen2.5-coder:latest", "qwen3-8b-q6k:latest"]
            )
            mock_mem.return_value = CheckResult(name="Host Memory", status="ok", message="32 GB RAM")

            report = diagnose_environment("http://127.0.0.1:11434")
            self.assertIsInstance(report, DoctorReport)
            self.assertTrue(report.is_healthy)
            self.assertIn("Git", [c.name for c in report.checks])
            as_dict = report.to_dict()
            self.assertTrue(as_dict["healthy"])
            rendered = report.render_text()
            self.assertIn("Git: git 2.40", rendered)
            self.assertIn("Ollama API: Connected", rendered)


if __name__ == "__main__":
    unittest.main()
