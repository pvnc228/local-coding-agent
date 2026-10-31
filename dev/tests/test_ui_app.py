"""Unit tests for Standalone Web Workbench & UI App (R22)."""

import json
import urllib.request
import pytest
from local_coding_agent.monitor import MonitorServer
from local_coding_agent.stats import DelegationStats


def test_ui_workbench_html_endpoint():
    stats = DelegationStats()
    with MonitorServer(stats=stats) as server:
        req = urllib.request.Request(f"{server.url}/workbench")
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            assert resp.status == 200
            content = resp.read().decode("utf-8")
            assert "Interactive Coding Workbench" in content
            assert "Task Envelope" in content


def test_ui_api_delegate_endpoint(monkeypatch):
    import local_coding_agent.ollama_adapter as oa

    class _FakeClient:
        def chat(self, *a, **k):
            raise RuntimeError("no model in test")

        def complete(self, *a, **k):
            raise RuntimeError("no model in test")

    monkeypatch.setattr(oa, "build_client", lambda profile: _FakeClient())
    stats = DelegationStats()
    with MonitorServer(stats=stats) as server:
        # Check endpoint handles POST requests gracefully
        req = urllib.request.Request(
            f"{server.url}/api/delegate",
            data=json.dumps({
                "task": {
                    "id": "test-ui-task",
                    "goal": "Test goal",
                    "files": ["test.py"],
                    "checks": [],
                }
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert "status" in data
