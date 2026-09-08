import tempfile
import unittest
from pathlib import Path

from local_coding_agent.task_store import JsonFileTaskStore, TaskRecord
from local_coding_agent.tasks import task_dict
from local_coding_agent.worker_pool import BoundedWorkerPool, DelegationRequest
from local_coding_agent.task import TaskEnvelope


class RecordingService:
    def delegate(self, caller_id, request, cancel_event=None, completion_event=None):
        return {
            "status": "accepted",
            "summary": "task executed",
            "patch": "",
            "audit": [],
        }


class TaskStoreTests(unittest.TestCase):
    def test_json_file_task_store_crud(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = JsonFileTaskStore(temp_dir)
            record = TaskRecord(
                task_id="task-123",
                caller_id="test-caller",
                request_id="req-abc",
                workspace_ref="ws-main",
                model_profile="qwen3-8b-q6k",
                state="queued",
                created_at="2026-08-15T12:00:00Z",
                updated_at="2026-08-15T12:00:00Z",
            )
            store.save(record)

            loaded = store.get("task-123")
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.task_id, "task-123")
            self.assertEqual(loaded.state, "queued")

            # Update state to completed with result
            updated = TaskRecord(
                task_id="task-123",
                caller_id="test-caller",
                request_id="req-abc",
                workspace_ref="ws-main",
                model_profile="qwen3-8b-q6k",
                state="completed",
                created_at="2026-08-15T12:00:00Z",
                updated_at="2026-08-15T12:01:00Z",
                result={"status": "accepted", "summary": "done"},
            )
            store.save(updated)

            loaded_updated = store.get("task-123")
            self.assertEqual(loaded_updated.state, "completed")
            self.assertEqual(loaded_updated.result, {"status": "accepted", "summary": "done"})

            # List filtering
            records = store.list(caller_id="test-caller")
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].task_id, "task-123")

            empty = store.list(caller_id="other-caller")
            self.assertEqual(len(empty), 0)

            # Delete
            deleted = store.delete("task-123")
            self.assertTrue(deleted)
            self.assertIsNone(store.get("task-123"))

    def test_json_file_task_store_recovers_interrupted_tasks_on_restart(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store1 = JsonFileTaskStore(temp_dir)
            working_task = TaskRecord(
                task_id="task-crashed",
                caller_id="caller-1",
                request_id="req-1",
                workspace_ref="ws-1",
                model_profile="qwen3-8b-q6k",
                state="working",
                created_at="2026-08-15T12:00:00Z",
                updated_at="2026-08-15T12:00:00Z",
            )
            completed_task = TaskRecord(
                task_id="task-done",
                caller_id="caller-1",
                request_id="req-2",
                workspace_ref="ws-1",
                model_profile="qwen3-8b-q6k",
                state="completed",
                created_at="2026-08-15T12:00:00Z",
                updated_at="2026-08-15T12:05:00Z",
                result={"status": "accepted"},
            )
            store1.save(working_task)
            store1.save(completed_task)

            # Simulate process restart by instantiating new store on same dir
            store2 = JsonFileTaskStore(temp_dir)
            recovered_crashed = store2.get("task-crashed")
            recovered_done = store2.get("task-done")

            self.assertEqual(recovered_crashed.state, "interrupted")
            self.assertEqual(recovered_done.state, "completed")

    def test_worker_pool_persists_to_task_store(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = JsonFileTaskStore(temp_dir)
            service = RecordingService()
            pool = BoundedWorkerPool(service, max_workers=1, max_queue=2, task_store=store)
            try:
                task = TaskEnvelope(id="durable-task", goal="persist job", files=("a.py",))
                req = DelegationRequest(
                    request_id="req-durable-1",
                    task=task,
                    workspace_ref="ws-1",
                    model_profile="qwen3-8b-q6k",
                )
                submitted = pool.submit("caller-durable", req)
                job_id = submitted["job_id"]
                result = pool.wait("caller-durable", job_id, timeout=2)
                self.assertEqual(result["status"], "completed")

                persisted = store.get(job_id)
                self.assertIsNotNone(persisted)
                self.assertEqual(persisted.state, "completed")
                self.assertEqual(persisted.caller_id, "caller-durable")
            finally:
                pool.shutdown()

            # Verify that a new pool on the same store recovers the completed job record
            pool2 = BoundedWorkerPool(service, max_workers=1, max_queue=2, task_store=store)
            try:
                snapshot = pool2.get("caller-durable", job_id)
                self.assertEqual(snapshot["status"], "completed")
            finally:
                pool2.shutdown()

    def test_recovered_interrupted_task_is_terminal_to_worker_and_mcp_pollers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = JsonFileTaskStore(temp_dir)
            store.save(
                TaskRecord(
                    task_id="task-interrupted",
                    caller_id="caller-restart",
                    request_id="request-interrupted",
                    workspace_ref="ws-restart",
                    model_profile="qwen3-8b-q6k",
                    state="working",
                    created_at="2026-08-15T12:00:00Z",
                    updated_at="2026-08-15T12:00:00Z",
                )
            )

            # A fresh store marks the old in-flight record as interrupted;
            # the worker pool must expose that as a terminal result.
            restarted_store = JsonFileTaskStore(temp_dir)
            self.assertEqual(restarted_store.get("task-interrupted").state, "interrupted")
            pool = BoundedWorkerPool(RecordingService(), task_store=restarted_store)
            try:
                snapshot = pool.get("caller-restart", "task-interrupted")
                self.assertEqual(snapshot["status"], "interrupted")
                self.assertEqual(snapshot["result"]["error"]["kind"], "process_interrupted")

                wire_task = task_dict(pool, "caller-restart", "task-interrupted")
                self.assertEqual(wire_task["status"], "completed")
                self.assertEqual(
                    wire_task["result"]["structuredContent"]["error"]["kind"],
                    "process_interrupted",
                )
            finally:
                pool.shutdown()


if __name__ == "__main__":
    unittest.main()
