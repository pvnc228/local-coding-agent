import json
import threading
import time
import unittest
from urllib.request import urlopen

from local_coding_agent.monitor import MonitorServer
from local_coding_agent.stats import DelegationStats
from local_coding_agent.task import TaskEnvelope
from local_coding_agent.worker_pool import BoundedWorkerPool, DelegationRequest


class FakeService:
    def delegate(self, request, cancel_event=None):
        return {
            "status": "accepted",
            "summary": "ok",
            "patch": "",
            "audit": [{"event": "tool_call"}, {"event": "model_request"}],
        }


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.stats = DelegationStats()
        self.service = FakeService()
        self.pool = BoundedWorkerPool(self.service, max_workers=2, max_queue=8)
        self.server = MonitorServer(
            host="127.0.0.1",
            port=0,
            worker_pool=self.pool,
            stats=self.stats,
        )
        self.server.start()

    def tearDown(self):
        self.server.stop()
        self.pool.shutdown()

    def test_health_endpoint_returns_ok_json(self):
        with urlopen(f"{self.server.url}/health", timeout=3) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(data["status"], "ok")

    def test_stats_endpoint_returns_aggregated_metrics(self):
        self.stats.record(
            {"status": "accepted", "audit": [{"event": "tool_call"}]},
            model="qwen3-8b-q6k",
            latency_ns=100_000_000,
        )
        with urlopen(f"{self.server.url}/stats", timeout=3) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers.get("Content-Type"), "application/json; charset=utf-8")
            data = json.loads(resp.read().decode("utf-8"))
            self.assertIn("stats", data)
            self.assertIn("worker_pool", data)
            self.assertEqual(data["stats"]["total"], 1)
            self.assertEqual(data["worker_pool"]["max_workers"], 2)
            self.assertEqual(data["worker_pool"]["queued_jobs"], 0)

    def test_dashboard_endpoint_returns_html(self):
        with urlopen(f"{self.server.url}/dashboard", timeout=3) as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn("text/html", resp.headers.get("Content-Type"))
            html = resp.read().decode("utf-8")
            self.assertIn("Local Coding Agent Monitor", html)
            self.assertIn("Worker Capacity", html)


    def test_tasks_endpoint_returns_jobs_list(self):
        task = TaskEnvelope(id="mon-task-1", goal="test monitor", files=("a.py",))
        req = DelegationRequest(
            request_id="req-1",
            task=task,
            workspace_ref="ws-1",
            model_profile="qwen3-8b-q6k",
        )
        self.pool.submit("test-caller", req)

        with urlopen(f"{self.server.url}/tasks", timeout=3) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertIn("jobs", data)
            self.assertGreaterEqual(len(data["jobs"]), 1)


if __name__ == "__main__":
    unittest.main()
