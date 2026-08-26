import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from threading import Event, Lock
from typing import Literal
from unittest.mock import patch

from pydantic import BaseModel, Field

from local_coding_agent.mcp_server import build_server
from local_coding_agent.service import DelegationService


CHECK_COMMAND = f'"{sys.executable}" -B -c "pass"'


class FakeModel:
    def chat(self, messages, *, tools=None):
        return {
            "message": {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "status": "candidate",
                        "summary": "готово",
                        "patch": "",
                        "checks": [],
                        "risks": [],
                    },
                    ensure_ascii=False,
                ),
            }
        }


class PatchModel:
    def __init__(self):
        self.calls = 0

    def chat(self, messages, *, tools=None):
        self.calls += 1
        if self.calls == 1 and any(
            definition["function"]["name"] == "run_tests" for definition in (tools or [])
        ):
            return {
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "run_tests",
                                "arguments": {"command": CHECK_COMMAND},
                            }
                        }
                    ],
                }
            }
        return {
            "message": {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "status": "candidate",
                        "summary": "изменено значение",
                        "patch": (
                            "diff --git a/value.py b/value.py\n"
                            "--- a/value.py\n"
                            "+++ b/value.py\n"
                            "@@ -1 +1 @@\n"
                            "-VALUE = 1\n"
                            "+VALUE = 2\n"
                        ),
                        "checks": [],
                        "risks": [],
                    },
                    ensure_ascii=False,
                ),
            }
        }


class CreateTaskClaimModel(BaseModel):
    result_type: Literal["task"] = Field(alias="resultType", default="task")
    task_id: str = Field(alias="taskId")
    status: str


class FlatTaskResult(BaseModel):
    result_type: str = Field(alias="resultType", default="complete")
    task_id: str = Field(alias="taskId")
    status: str
    created_at: str = Field(alias="createdAt")
    last_updated_at: str = Field(alias="lastUpdatedAt")
    ttl_ms: int | None = Field(alias="ttlMs", default=None)
    poll_interval_ms: int | None = Field(alias="pollIntervalMs", default=None)
    result: dict | None = None
    error: dict | None = None


class FailingModel:
    def chat(self, messages, *, tools=None):
        raise RuntimeError("model unavailable")


class MixedBlockingService:
    def __init__(self):
        self.started = Event()
        self.overlap = Event()
        self.triple_overlap = Event()
        self.release = Event()
        self.finished = Event()
        self._lock = Lock()
        self._active = 0
        self.max_active = 0

    def delegate(self, caller_id, request, **kwargs):
        with self._lock:
            self._active += 1
            self.max_active = max(self.max_active, self._active)
            if self._active >= 2:
                self.overlap.set()
            if self._active >= 3:
                self.triple_overlap.set()
            self.started.set()
        try:
            # The test releases both calls together after observing the mixed path.
            self.release.wait(timeout=5)
        finally:
            with self._lock:
                self._active -= 1
                if self._active == 0:
                    self.finished.set()
        completion_event = kwargs.get("completion_event")
        if completion_event is not None:
            completion_event.set()
        return {"status": "accepted", "summary": "ok", "patch": "", "checks": [], "risks": []}


def _arguments(request_id="r1", checks=(), files=("allowed.py",), goal="прочитать файл"):
    return {
        "request_id": request_id,
        "workspace_ref": "fixture",
        "model_profile": "qwen2.5-1.5b",
        "task": {
            "id": "t1",
            "goal": goal,
            "files": list(files),
            "checks": list(checks),
        },
    }


class TasksExtensionTests(unittest.TestCase):
    def _service(self, workspace: Path, model=None):
        return DelegationService({"fixture": workspace}, model_factory=lambda profile: model or FakeModel())

    def _build_tasks_client(self):
        from mcp.client import Client
        from mcp.client.extension import ClientExtension, ResultClaim
        from mcp.types import Result

        class ClaimModel(Result):
            result_type: Literal["task"] = "task"
            task_id: str = Field(alias="taskId")
            status: str

        class TasksClaimExtension(ClientExtension):
            identifier = "io.modelcontextprotocol/tasks"

            def claims(self):
                return [
                    ResultClaim(
                        result_type="task",
                        model=ClaimModel,
                        resolve=self._resolve,
                    )
                ]

            async def _resolve(self, task, ctx):
                from mcp.types import CallToolResult, TextContent

                return CallToolResult(
                    content=[TextContent(type="text", text=json.dumps(task.model_dump(by_alias=True)))],
                    structured_content=task.model_dump(by_alias=True),
                )

        return Client, TasksClaimExtension

    def test_tasks_lifecycle_returns_working_then_completed(self):
        from mcp.types import GetTaskRequest, GetTaskRequestParams

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "allowed.py").write_text("VALUE = 1\n", encoding="utf-8")
            server = build_server(self._service(workspace), enable_tasks=True)
            Client, TasksClaimExtension = self._build_tasks_client()

            async def run():
                async with Client(server, extensions=[TasksClaimExtension()]) as client:
                    tools = await client.list_tools()
                    result = await client.call_tool("delegate_code", _arguments())
                    # Drain the background delegation before the workspace
                    # temp dir unwinds; the worker thread keeps running after
                    # the "working" snapshot, racing rmtree otherwise.
                    for _ in range(50):
                        raw = await client.session.send_request(
                            GetTaskRequest(
                                params=GetTaskRequestParams(task_id=result.structured_content["taskId"])
                            ),
                            FlatTaskResult,
                        )
                        if raw.status in {"completed", "failed", "cancelled"}:
                            break
                        await asyncio.sleep(0.02)
                    return [t.name for t in tools.tools], result

            names, result = asyncio.run(run())

        self.assertEqual(names, ["delegate_code", "apply_proposal"])
        task_id = result.structured_content["taskId"]
        self.assertEqual(result.result_type, "complete")
        self.assertEqual(result.structured_content["status"], "working")
        self.assertTrue(task_id)

    def test_tasks_get_reports_terminal_result(self):
        from mcp.types import GetTaskRequest, GetTaskRequestParams

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "allowed.py").write_text("VALUE = 1\n", encoding="utf-8")
            server = build_server(self._service(workspace), enable_tasks=True)
            Client, TasksClaimExtension = self._build_tasks_client()

            async def run():
                async with Client(server, extensions=[TasksClaimExtension()]) as client:
                    result = await client.call_tool("delegate_code", _arguments())
                    task_id = result.structured_content["taskId"]
                    for _ in range(50):
                        raw = await client.session.send_request(
                            GetTaskRequest(params=GetTaskRequestParams(task_id=task_id)),
                            FlatTaskResult,
                        )
                        if raw.status in {"completed", "failed", "cancelled"}:
                            return raw
                        await asyncio.sleep(0.02)
                    return raw

            raw = asyncio.run(run())

        self.assertEqual(raw.status, "completed")
        self.assertIn("content", raw.result)
        self.assertFalse(raw.result["isError"])
        self.assertEqual(raw.result["structuredContent"]["status"], "accepted")
        self.assertFalse(raw.result["structuredContent"].get("applied", False))

    def test_controller_failure_is_completed_task_with_call_tool_error_result(self):
        from mcp.types import GetTaskRequest, GetTaskRequestParams

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "allowed.py").write_text("VALUE = 1\n", encoding="utf-8")
            server = build_server(self._service(workspace, model=FailingModel()), enable_tasks=True)
            Client, TasksClaimExtension = self._build_tasks_client()

            async def run():
                async with Client(server, extensions=[TasksClaimExtension()]) as client:
                    created = await client.call_tool("delegate_code", _arguments())
                    task_id = created.structured_content["taskId"]
                    for _ in range(50):
                        raw = await client.session.send_request(
                            GetTaskRequest(params=GetTaskRequestParams(task_id=task_id)),
                            FlatTaskResult,
                        )
                        if raw.status == "completed":
                            return raw
                        await asyncio.sleep(0.02)
                    return raw

            raw = asyncio.run(run())

        self.assertEqual(raw.status, "completed")
        self.assertTrue(raw.result["isError"])
        self.assertIn("content", raw.result)
        self.assertEqual(raw.result["structuredContent"]["status"], "failed")

    def test_unknown_task_id_is_invalid_params(self):
        from mcp.client import Client
        from mcp.shared.exceptions import MCPError
        from mcp.types import GetTaskRequest, GetTaskRequestParams

        with tempfile.TemporaryDirectory() as temp_dir:
            server = build_server(self._service(Path(temp_dir)), enable_tasks=True)

            async def run():
                async with Client(server) as client:
                    with self.assertRaises(MCPError) as raised:
                        await client.session.send_request(
                            GetTaskRequest(params=GetTaskRequestParams(task_id="missing")),
                            FlatTaskResult,
                        )
                    return raised.exception

            error = asyncio.run(run())

        self.assertEqual(error.code, -32602)

    def test_delegate_code_without_tasks_capability_stays_synchronous(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "allowed.py").write_text("VALUE = 1\n", encoding="utf-8")
            server = build_server(self._service(workspace), enable_tasks=True)

            async def run():
                from mcp.client import Client

                async with Client(server) as client:
                    result = await client.call_tool("delegate_code", _arguments())
                    return result

            result = asyncio.run(run())

        self.assertEqual(result.result_type, "complete")
        self.assertEqual(result.structured_content["status"], "accepted")

    def test_tasks_and_legacy_clients_share_global_worker_admission(self):
        service = MixedBlockingService()
        server = build_server(service, enable_tasks=True, max_workers=1, max_queue=0)
        Client, TasksClaimExtension = self._build_tasks_client()

        async def run():
            async with Client(server, extensions=[TasksClaimExtension()]) as tasks_client:
                async with Client(server) as legacy_client:
                    task_call = asyncio.create_task(
                        tasks_client.call_tool("delegate_code", _arguments(request_id="tasks"))
                    )
                    await asyncio.to_thread(service.started.wait, 1)
                    legacy_call = asyncio.create_task(
                        legacy_client.call_tool("delegate_code", _arguments(request_id="legacy"))
                    )
                    await asyncio.to_thread(service.overlap.wait, 0.25)
                    overlap = service.overlap.is_set()
                    service.release.set()
                    legacy_result = await legacy_call
                    task_result = await task_call
                    await asyncio.to_thread(service.finished.wait, 1)
                    return overlap, legacy_result, task_result

        overlap, legacy_result, task_result = asyncio.run(run())

        self.assertFalse(overlap)
        self.assertEqual(service.max_active, 1)
        self.assertTrue(legacy_result.is_error)
        self.assertEqual(legacy_result.structured_content["error"]["kind"], "queue_overload")
        self.assertEqual(task_result.structured_content["status"], "working")

    def test_shared_gate_limits_active_tasks_when_legacy_call_is_waiting(self):
        service = MixedBlockingService()
        server = build_server(service, enable_tasks=True, max_workers=2, max_queue=1)
        Client, TasksClaimExtension = self._build_tasks_client()

        async def run():
            async with Client(server, extensions=[TasksClaimExtension()]) as tasks_client:
                async with Client(server) as legacy_client:
                    first = asyncio.create_task(
                        tasks_client.call_tool("delegate_code", _arguments(request_id="task-1"))
                    )
                    second = asyncio.create_task(
                        tasks_client.call_tool("delegate_code", _arguments(request_id="task-2"))
                    )
                    await asyncio.to_thread(service.overlap.wait, 1)
                    legacy = asyncio.create_task(
                        legacy_client.call_tool("delegate_code", _arguments(request_id="legacy"))
                    )
                    await asyncio.to_thread(service.triple_overlap.wait, 0.25)
                    triple_overlap = service.triple_overlap.is_set()
                    service.release.set()
                    legacy_result = await legacy
                    first_result = await first
                    second_result = await second
                    await asyncio.to_thread(service.finished.wait, 1)
                    return triple_overlap, legacy_result, first_result, second_result

        triple_overlap, legacy_result, first_result, second_result = asyncio.run(run())

        self.assertFalse(triple_overlap)
        self.assertEqual(service.max_active, 2)
        self.assertFalse(legacy_result.is_error)
        self.assertEqual(first_result.structured_content["status"], "working")
        self.assertEqual(second_result.structured_content["status"], "working")


class ApplyProposalTests(unittest.TestCase):
    def _service(self, workspace: Path):
        return DelegationService({"fixture": workspace}, model_factory=lambda profile: PatchModel())

    def _apply_arguments(self):
        return {"request_id": "r1", "workspace_ref": "fixture"}

    @staticmethod
    async def _accept_callback(context, params):
        from mcp.types import ElicitResult

        message = str(getattr(params, "message", ""))
        values = {}
        for line in message.splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                values[key.strip()] = value.strip()
        return ElicitResult(
            action="accept",
            content={
                "confirm": True,
                "proposal_id": values.get("proposal_id", "r1"),
                "workspace_ref": values.get("workspace", "fixture"),
                "proposal_digest": values.get("proposal_digest", ""),
            },
        )

    @staticmethod
    async def _decline_callback(context, params):
        from mcp.types import ElicitResult

        return ElicitResult(action="decline")

    def test_apply_proposal_applies_after_confirmation(self):
        from mcp.client import Client

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "value.py").write_text("VALUE = 1\n", encoding="utf-8")
            server = build_server(self._service(workspace), enable_tasks=True)

            async def run():
                async with Client(server, elicitation_callback=self._accept_callback) as client:
                    delegated = await client.call_tool(
                        "delegate_code",
                        {
                            "request_id": "r1",
                            "workspace_ref": "fixture",
                            "model_profile": "qwen2.5-1.5b",
                            "task": {
                                "id": "t1",
                                "goal": "change",
                                "files": ["value.py"],
                                "checks": [CHECK_COMMAND],
                            },
                        },
                    )
                    applied = await client.call_tool("apply_proposal", self._apply_arguments())
                    return delegated.structured_content, applied.structured_content

            delegated, applied = asyncio.run(run())
            content = (workspace / "value.py").read_text(encoding="utf-8")

        self.assertEqual(delegated["status"], "accepted")
        self.assertFalse(delegated.get("applied", False))
        self.assertEqual(applied["status"], "accepted")
        self.assertTrue(applied["applied"])
        self.assertEqual(content, "VALUE = 2\n")

    def test_apply_confirmation_includes_proposal_identity_summary_workspace_files_and_diff(self):
        from mcp.client import Client

        messages = []

        async def accept(context, params):
            messages.append(getattr(params, "message", str(params)))
            return await ApplyProposalTests._accept_callback(context, params)

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "value.py").write_text("VALUE = 1\n", encoding="utf-8")
            server = build_server(self._service(workspace), enable_tasks=True)

            async def run():
                async with Client(server, elicitation_callback=accept) as client:
                    await client.call_tool(
                        "delegate_code",
                        _arguments(checks=(CHECK_COMMAND,), files=("value.py",), goal="change"),
                    )
                    return await client.call_tool("apply_proposal", self._apply_arguments())

            applied = asyncio.run(run())

        self.assertEqual(applied.structured_content["status"], "accepted")
        self.assertTrue(messages)
        message = str(messages[0])
        for expected in ("r1", "изменено значение", "fixture", "value.py", "VALUE = 2"):
            self.assertIn(expected, message)

    def test_apply_proposal_rejects_confirmation_with_wrong_digest(self):
        from mcp.client import Client

        async def wrong_digest(context, params):
            content = await ApplyProposalTests._accept_callback(context, params)
            content.content["proposal_digest"] = "wrong-digest"
            return content

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "value.py").write_text("VALUE = 1\n", encoding="utf-8")
            server = build_server(self._service(workspace), enable_tasks=True)

            async def run():
                async with Client(server, elicitation_callback=wrong_digest) as client:
                    await client.call_tool(
                        "delegate_code",
                        _arguments(checks=(CHECK_COMMAND,), files=("value.py",), goal="change"),
                    )
                    return await client.call_tool("apply_proposal", self._apply_arguments())

            applied = asyncio.run(run())
            content = (workspace / "value.py").read_text(encoding="utf-8")

        self.assertTrue(applied.is_error)
        self.assertEqual(applied.structured_content["error"]["kind"], "apply_confirmation_mismatch")
        self.assertEqual(content, "VALUE = 1\n")

    def test_apply_proposal_fails_closed_when_preview_is_unavailable(self):
        from mcp.client import Client

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "value.py").write_text("VALUE = 1\n", encoding="utf-8")
            service = self._service(workspace)
            server = build_server(service, enable_tasks=True)

            async def run():
                async with Client(server, elicitation_callback=self._accept_callback) as client:
                    await client.call_tool(
                        "delegate_code",
                        _arguments(checks=(CHECK_COMMAND,), files=("value.py",), goal="change"),
                    )
                    try:
                        result = await client.call_tool("apply_proposal", self._apply_arguments())
                    except Exception:  # SDK may surface resolver failures as JSON-RPC errors.
                        return True, None
                    return False, result

            with patch.object(service, "proposal_preview", side_effect=ValueError("preview unavailable")):
                raised, applied = asyncio.run(run())
            content = (workspace / "value.py").read_text(encoding="utf-8")

        self.assertTrue(raised or applied.is_error)
        self.assertEqual(content, "VALUE = 1\n")

    def test_apply_proposal_decline_leaves_workspace_untouched(self):
        from mcp.client import Client

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "value.py").write_text("VALUE = 1\n", encoding="utf-8")
            server = build_server(self._service(workspace), enable_tasks=True)

            async def run():
                async with Client(server, elicitation_callback=self._decline_callback) as client:
                    await client.call_tool(
                        "delegate_code",
                        {
                            "request_id": "r1",
                            "workspace_ref": "fixture",
                            "model_profile": "qwen2.5-1.5b",
                            "task": {
                                "id": "t1",
                                "goal": "change",
                                "files": ["value.py"],
                                "checks": [CHECK_COMMAND],
                            },
                        },
                    )
                    applied = await client.call_tool("apply_proposal", self._apply_arguments())
                    return applied.structured_content

            applied = asyncio.run(run())
            content = (workspace / "value.py").read_text(encoding="utf-8")

        self.assertEqual(applied["status"], "rejected")
        self.assertEqual(applied["error"]["kind"], "apply_declined")
        self.assertEqual(content, "VALUE = 1\n")

    def test_apply_proposal_stale_workspace_rejected(self):
        from mcp.client import Client

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "value.py").write_text("VALUE = 1\n", encoding="utf-8")
            server = build_server(self._service(workspace), enable_tasks=True)

            async def run():
                async with Client(server, elicitation_callback=self._accept_callback) as client:
                    await client.call_tool(
                        "delegate_code",
                        {
                            "request_id": "r1",
                            "workspace_ref": "fixture",
                            "model_profile": "qwen2.5-1.5b",
                            "task": {
                                "id": "t1",
                                "goal": "change",
                                "files": ["value.py"],
                                "checks": [CHECK_COMMAND],
                            },
                        },
                    )
                    (workspace / "value.py").write_text("VALUE = 999\n", encoding="utf-8")
                    applied = await client.call_tool("apply_proposal", self._apply_arguments())
                    return applied.structured_content

            applied = asyncio.run(run())
            content = (workspace / "value.py").read_text(encoding="utf-8")

        self.assertEqual(applied["status"], "failed")
        self.assertEqual(applied["error"]["kind"], "stale_workspace")
        self.assertEqual(content, "VALUE = 999\n")


if __name__ == "__main__":
    unittest.main()
