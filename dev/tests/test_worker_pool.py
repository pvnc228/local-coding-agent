import json
import tempfile
import threading
import unittest
from pathlib import Path

from local_coding_agent.service import DelegationRequest, DelegationService
from local_coding_agent.task import TaskEnvelope
from local_coding_agent.worker_pool import BoundedWorkerPool


class RecordingService:
    def __init__(self, *, block_first=False):
        self.block_first = block_first
        self.calls = []
        self.first_started = threading.Event()
        self.release = threading.Event()
        self._lock = threading.Lock()

    def delegate(self, caller_id, request, *, cancel_event=None, completion_event=None):
        with self._lock:
            self.calls.append((caller_id, request.request_id))
            call_number = len(self.calls)
        try:
            if self.block_first and call_number == 1:
                self.first_started.set()
                while not self.release.wait(0.01):
                    if cancel_event is not None and cancel_event.is_set():
                        return {
                            "status": "failed",
                            "error": {"kind": "cancelled", "message": "cancelled"},
                            "applied": False,
                        }
            return {"status": "accepted", "summary": request.task.goal, "applied": False}
        finally:
            if completion_event is not None:
                completion_event.set()


class ParallelService:
    def __init__(self):
        self.entered = threading.Barrier(2)
        self.calls = []
        self._lock = threading.Lock()

    def delegate(self, caller_id, request, *, cancel_event=None, completion_event=None):
        with self._lock:
            self.calls.append((caller_id, request.request_id))
        try:
            self.entered.wait(timeout=1)
            return {"status": "accepted", "summary": request.task.goal, "applied": False}
        finally:
            if completion_event is not None:
                completion_event.set()


class BlockingModel:
    def __init__(self):
        self.calls = 0
        self.entered = threading.Event()
        self.release = threading.Event()

    def chat(self, messages, *, tools=None):
        self.calls += 1
        self.entered.set()
        self.release.wait(2)
        return {
            "message": {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "status": "candidate",
                        "summary": "done",
                        "patch": "",
                        "checks": [],
                        "risks": [],
                    }
                ),
            }
        }


class ImmediateModel:
    def chat(self, messages, *, tools=None):
        return {
            "message": {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "status": "candidate",
                        "summary": "done",
                        "patch": "",
                        "checks": [],
                        "risks": [],
                    }
                ),
            }
        }


class BoundedWorkerPoolTests(unittest.TestCase):
    @staticmethod
    def _request(request_id, goal=None, files=("allowed.py",)):
        return DelegationRequest(
            request_id=request_id,
            workspace_ref="fixture",
            model_profile="qwen2.5-1.5b",
            task=TaskEnvelope(
                id=request_id,
                goal=goal or request_id,
                files=files,
            ),
        )

    def test_queue_capacity_rejects_overload_and_runs_bounded_work(self):
        service = RecordingService(block_first=True)
        pool = BoundedWorkerPool(service, max_workers=1, max_queue=1)
        try:
            first = pool.submit("caller", self._request("one"))
            self.assertEqual(first["status"], "queued")
            self.assertTrue(service.first_started.wait(1))

            second = pool.submit("caller", self._request("two"))
            self.assertEqual(second["status"], "queued")
            overloaded = pool.submit("caller", self._request("three"))
            self.assertEqual(overloaded["status"], "failed")
            self.assertEqual(overloaded["error"]["kind"], "queue_overload")

            service.release.set()
            self.assertEqual(
                pool.wait("caller", first["job_id"], timeout=1)["status"], "completed"
            )
            self.assertEqual(
                pool.wait("caller", second["job_id"], timeout=1)["status"], "completed"
            )
            self.assertEqual(service.calls, [("caller", "one"), ("caller", "two")])
        finally:
            pool.shutdown()

    def test_queued_cancellation_never_calls_service(self):
        service = RecordingService(block_first=True)
        pool = BoundedWorkerPool(service, max_workers=1, max_queue=1)
        try:
            first = pool.submit("caller", self._request("one"))
            self.assertTrue(service.first_started.wait(1))
            second = pool.submit("caller", self._request("two"))

            cancelled = pool.cancel("caller", second["job_id"])
            self.assertEqual(cancelled["status"], "cancelled")
            service.release.set()
            self.assertEqual(
                pool.wait("caller", first["job_id"], timeout=1)["status"], "completed"
            )
            self.assertEqual(pool.get("caller", second["job_id"])["status"], "cancelled")
            self.assertEqual(service.calls, [("caller", "one")])
        finally:
            pool.shutdown()

    def test_running_cancellation_propagates_to_service_and_is_bounded(self):
        service = RecordingService(block_first=True)
        pool = BoundedWorkerPool(service, max_workers=1, max_queue=0)
        try:
            submitted = pool.submit("caller", self._request("one"))
            self.assertTrue(service.first_started.wait(1))
            cancellation = pool.cancel("caller", submitted["job_id"])
            self.assertEqual(cancellation["status"], "working")
            self.assertTrue(cancellation["cancellation_requested"])
            result = pool.wait("caller", submitted["job_id"], timeout=1)
            self.assertEqual(result["status"], "cancelled")
        finally:
            pool.shutdown()

    def test_idempotency_and_caller_scope_are_enforced(self):
        service = RecordingService()
        pool = BoundedWorkerPool(service, max_workers=1, max_queue=1)
        try:
            request = self._request("same")
            first = pool.submit("caller", request)
            duplicate = pool.submit("caller", request)
            self.assertIn(duplicate["status"], {"queued", "working", "completed"})
            self.assertEqual(duplicate["job_id"], first["job_id"])

            conflict = pool.submit("caller", self._request("same", goal="different"))
            self.assertEqual(conflict["status"], "failed")
            self.assertEqual(conflict["error"]["kind"], "idempotency_conflict")

            hidden = pool.get("other-caller", first["job_id"])
            self.assertEqual(hidden["status"], "failed")
            self.assertEqual(hidden["error"]["kind"], "unknown_job")
            self.assertEqual(
                pool.wait("caller", first["job_id"], timeout=1)["status"], "completed"
            )
            self.assertEqual(len(service.calls), 1)
        finally:
            pool.shutdown()

    def test_parallel_jobs_keep_request_and_caller_state_separate(self):
        service = ParallelService()
        pool = BoundedWorkerPool(service, max_workers=2, max_queue=2)
        try:
            first = pool.submit("caller-a", self._request("one", goal="alpha"))
            second = pool.submit("caller-b", self._request("two", goal="beta"))
            first_result = pool.wait("caller-a", first["job_id"], timeout=1)
            second_result = pool.wait("caller-b", second["job_id"], timeout=1)
            self.assertEqual(first_result["result"]["summary"], "alpha")
            self.assertEqual(second_result["result"]["summary"], "beta")
            self.assertCountEqual(service.calls, [("caller-a", "one"), ("caller-b", "two")])
        finally:
            pool.shutdown()

    def test_completed_job_retention_is_bounded(self):
        service = RecordingService()
        pool = BoundedWorkerPool(service, max_workers=1, max_queue=1, max_completed_jobs=1)
        try:
            first = pool.submit("caller", self._request("one"))
            self.assertEqual(pool.wait("caller", first["job_id"], timeout=1)["status"], "completed")
            second = pool.submit("caller", self._request("two"))
            self.assertEqual(pool.wait("caller", second["job_id"], timeout=1)["status"], "completed")
            self.assertEqual(pool.get("caller", first["job_id"])["error"]["kind"], "unknown_job")
            self.assertEqual(pool.get("caller", second["job_id"])["status"], "completed")
        finally:
            pool.shutdown()

    def test_cancellation_keeps_physical_model_slot_occupied_until_chat_finishes(self):
        model = BlockingModel()
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "allowed.py").write_text("VALUE = 1\n", encoding="utf-8")
            service = DelegationService(
                {"fixture": workspace},
                model_factory=lambda profile: model,
            )
            pool = BoundedWorkerPool(service, max_workers=1, max_queue=0)
            try:
                first = pool.submit("caller", self._request("one"))
                self.assertTrue(model.entered.wait(1))
                self.assertTrue(pool.cancel("caller", first["job_id"])["cancellation_requested"])

                blocked = pool.submit("caller", self._request("two"))
                self.assertEqual(blocked["status"], "failed")
                self.assertEqual(blocked["error"]["kind"], "queue_overload")

                model.release.set()
                self.assertEqual(pool.wait("caller", first["job_id"], timeout=1)["status"], "cancelled")
                second = pool.submit("caller", self._request("two"))
                self.assertEqual(pool.wait("caller", second["job_id"], timeout=1)["status"], "completed")
                self.assertEqual(model.calls, 2)
            finally:
                pool.shutdown()

    def test_early_repository_policy_failure_releases_worker_slot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            files = tuple(f"allowed-{index}.py" for index in range(6))
            for name in files:
                (workspace / name).write_text("VALUE = 1\n", encoding="utf-8")
            service = DelegationService(
                {"fixture": workspace},
                model_factory=lambda profile: ImmediateModel(),
            )
            pool = BoundedWorkerPool(service, max_workers=1, max_queue=0)
            try:
                first = pool.submit("caller", self._request("too-wide", files=files))
                first_result = pool.wait("caller", first["job_id"], timeout=1)
                self.assertEqual(first_result["status"], "failed")
                self.assertEqual(first_result["result"]["status"], "failed")

                second = pool.submit("caller", self._request("after-policy"))
                self.assertIn(second["status"], {"queued", "working", "completed"})
                self.assertEqual(pool.wait("caller", second["job_id"], timeout=1)["status"], "completed")
            finally:
                pool.shutdown()


    def test_worker_pool_status_reports_accurate_metrics(self):
        service = RecordingService()
        pool = BoundedWorkerPool(service, max_workers=2, max_queue=4)
        try:
            status_before = pool.status()
            self.assertEqual(status_before["active_workers"], 0)
            self.assertEqual(status_before["max_workers"], 2)
            self.assertEqual(status_before["queued_jobs"], 0)
            self.assertEqual(status_before["max_queue"], 4)
            self.assertFalse(status_before["stopping"])

            job_resp = pool.submit("caller-status", self._request("status-check"))
            self.assertEqual(pool.wait("caller-status", job_resp["job_id"], timeout=1)["status"], "completed")

            status_after = pool.status()
            self.assertGreaterEqual(status_after["total_jobs"], 1)
            self.assertEqual(len(status_after["jobs"]), 1)
            self.assertEqual(status_after["jobs"][0]["caller_id"], "caller-status")
            self.assertEqual(status_after["jobs"][0]["state"], "completed")
        finally:
            pool.shutdown()

    def test_task_store_recovery_preserves_idempotency_without_conflict(self):
        from local_coding_agent.task_store import JsonFileTaskStore
        with tempfile.TemporaryDirectory() as store_dir:
            store = JsonFileTaskStore(store_dir)
            service = RecordingService()
            pool1 = BoundedWorkerPool(service, max_workers=1, max_queue=1, task_store=store)
            request = self._request("persist-test")
            job1 = pool1.submit("caller-store", request)
            self.assertEqual(pool1.wait("caller-store", job1["job_id"], timeout=1)["status"], "completed")
            pool1.shutdown()

            # Now create a new pool sharing the same store
            pool2 = BoundedWorkerPool(service, max_workers=1, max_queue=1, task_store=store)
            try:
                # Re-submitting the exact same request must return snapshot, not idempotency_conflict
                re_submitted = pool2.submit("caller-store", request)
                self.assertEqual(re_submitted["status"], "completed")
                self.assertEqual(re_submitted["job_id"], job1["job_id"])
            finally:
                pool2.shutdown()


if __name__ == "__main__":
    unittest.main()


