"""Unit tests for Streaming Progress & Token Telemetry (R20)."""

import json
import urllib.request
import pytest
from local_coding_agent.monitor import MonitorServer
from local_coding_agent.stats import DelegationStats


def test_monitor_event_emission_and_snapshot():
    stats = DelegationStats()
    with MonitorServer(stats=stats) as server:
        # Emit an event
        server.emit_event({
            "type": "progress",
            "task_id": "test-task",
            "phase": "generating",
            "tps": 118.5,
        })

        events = server.get_recent_events()
        assert len(events) == 1
        assert events[0]["type"] == "progress"
        assert events[0]["task_id"] == "test-task"
        assert events[0]["phase"] == "generating"
        assert events[0]["tps"] == 118.5


def test_monitor_events_http_endpoint():
    stats = DelegationStats()
    with MonitorServer(stats=stats) as server:
        server.emit_event({
            "type": "state_transition",
            "state": "candidate_ready",
        })

        req = urllib.request.Request(f"{server.url}/api/events")
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            assert resp.status == 200
            content = resp.read().decode("utf-8")
            data = json.loads(content)
            assert "events" in data
            assert len(data["events"]) >= 1
            assert data["events"][0]["state"] == "candidate_ready"
